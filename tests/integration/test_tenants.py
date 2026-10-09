"""Account isolation, encrypted routing and durable atomic system trials."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import socket
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import func, select

from interview_intelligence.api import create_app
from interview_intelligence.access import AccessService
from interview_intelligence.access_models import AccessPolicy
from interview_intelligence.config import Settings
from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.domain.models import AgentConversation, AgentTurn, create_database
from interview_intelligence.tenant import TENANT_COOKIE, TenantService
from interview_intelligence.tenant_models import TenantAccount, TenantAdmission
from interview_intelligence.providers.tenant_transport import PublicAsyncBackend, PublicSyncBackend, validate_tenant_endpoint

ACTION = {"X-Tenant-Action": "1"}


class OfflinePlanner:
    async def plan(self, run, context):
        return QuerySpec(action="LIST")


def configuration(tmp_path, **changes):
    return Settings(multi_tenant_enabled=True, app_signing_key="isolated-tenant-test-key",
        tenant_default_base_url="http://127.0.0.1:18792/v1", tenant_default_api_key="default-secret",
        tenant_default_query_model="qwen-default", tenant_default_reranker_model="qwen-rerank",
        snapshot_root=tmp_path / "snapshots", model_lock_path=tmp_path / "gate.lock",
        model_min_interval_seconds=0, **changes)


def register(client, username="alice"):
    result = client.post("/api/account/register", headers=ACTION,
        json={"username": username, "password": "account-secret-password"})
    assert result.status_code == 200, result.text
    return result.json()["data"]["tenant_id"]


def ask(client, request_id=None, **fields):
    return client.post("/api/query", json={"message": "列出题目", "request_id": request_id or str(uuid4()), **fields})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))])
    database = create_database("sqlite:///:memory:")
    settings = configuration(tmp_path)
    app = create_app(database, settings, query_planner=OfflinePlanner())
    client = TestClient(app, client=("198.51.100.9", 1))
    return database, settings, app, client


def test_auth_session_tamper_revoke_csrf_and_credential_validation(setup):
    database, settings, app, client = setup
    assert client.get("/api/account").json()["data"] == {"enabled": True, "authenticated": False}
    assert ask(client).status_code == 401
    assert client.get("/api/conversations", headers={"X-Tenant-ID": "local", "X-User-ID": "local"}).status_code == 401
    identifier = register(client)
    cookie = client.cookies.get(TENANT_COOKIE)
    assert "account-secret-password" not in cookie
    assert client.get("/api/account").json()["data"]["tenant_id"] == identifier
    forbidden = client.patch("/api/account/model", headers={**ACTION, "Origin": "https://evil.example"},
        json={"expected_version": 0, "base_url": "https://public.example/v1", "api_key": "own-key", "model": "qwen"})
    assert forbidden.status_code == 403
    malformed = client.patch("/api/account/model", headers=ACTION,
        json={"expected_version": 0, "base_url": "https://public.example/v1", "api_key": "private-key\nsecret", "model": "qwen"})
    assert malformed.status_code == 422 and "private-key" not in malformed.text
    client.cookies.set(TENANT_COOKIE, cookie[:-4] + "aaaa")
    assert client.get("/api/account").json()["data"]["authenticated"] is False
    client.cookies.set(TENANT_COOKIE, cookie)
    assert client.post("/api/account/logout", headers=ACTION).status_code == 200
    client.cookies.set(TENANT_COOKIE, cookie)
    assert client.get("/api/account").json()["data"]["authenticated"] is False


def test_authenticated_private_api_and_sse_cannot_be_cached_between_accounts(setup):
    database, settings, app, client = setup
    register(client)
    response = ask(client)
    assert response.headers["cache-control"] == "private, no-store"
    run_id = response.json()["meta"]["run_id"]
    assert client.get("/api/preferences").headers["cache-control"] == "private, no-store"
    events = client.get(f"/api/runs/{run_id}/events")
    assert events.status_code == 200
    assert events.headers["cache-control"] == "private, no-store, no-transform"


def test_exact_ten_default_trials_sql_replay_and_invalid_ownership_do_not_spend(setup):
    database, settings, app, client = setup
    register(client)
    sql = client.post("/api/questions/list", json={"request_id": "sql-one", "list_request": {}})
    assert sql.status_code == 200, sql.text
    assert client.get("/api/account/model").json()["data"]["trial"]["used"] == 0
    assert ask(client, conversation_id=str(uuid4())).status_code == 404
    assert ask(client, expected_version=99).status_code == 409
    first = ask(client, "first")
    assert first.status_code == 200, first.text
    conversation_id = first.json()["meta"]["conversation_id"]
    assert ask(client, "stale", conversation_id=conversation_id, expected_version=0).status_code == 409
    assert ask(client, "first").status_code == 200
    for index in range(9):
        assert ask(client, f"next-{index}").status_code == 200
    exhausted = ask(client, "eleventh")
    assert exhausted.status_code == 429
    assert exhausted.json()["error"]["code"] == "SYSTEM_TRIAL_EXHAUSTED"
    assert exhausted.json()["error"]["retryable"] is False
    assert ask(client, "first").status_code == 200
    assert client.get("/api/account/model").json()["data"]["trial"] == {"limit": 10, "used": 10, "remaining": 0}
    assert client.get("/api/questions/list").status_code == 200
    client.get("/api/questions/search", params={"query": "test", "pipeline": "BM25"})
    assert client.get("/api/account/model").json()["data"]["trial"]["used"] == 10


def test_personal_config_encrypted_no_trial_and_reset_preserves_usage(setup):
    database, settings, app, client = setup
    identifier = register(client)
    assert ask(client, "system-one").status_code == 200
    saved = client.patch("/api/account/model", headers=ACTION, json={"expected_version": 0,
        "base_url": "https://public.example/v1", "api_key": "tenant-private-secret-abcd", "model": "qwen-personal",
        "reranker_model": "qwen-personal-rerank"})
    assert saved.status_code == 200 and "tenant-private-secret" not in saved.text
    assert saved.json()["data"]["api_key_hint"] == "••••abcd"
    assert ask(client, "personal-one").status_code == 200
    with database.session() as session:
        account = session.get(TenantAccount, identifier)
        admission = session.scalar(select(TenantAdmission).where(TenantAdmission.request_id == "personal-one"))
        assert account.trial_used == 1
        assert "tenant-private-secret" not in account.provider_key + admission.provider_snapshot
    assert client.patch("/api/account/model", headers=ACTION, json={"expected_version": 1,
        "base_url": "https://public.example/v2", "model": "new-model"}).status_code == 200
    assert client.delete("/api/account/model?expected_version=2", headers=ACTION).status_code == 200
    assert ask(client, "personal-one").status_code == 200
    assert client.get("/api/account/model").json()["data"]["trial"]["used"] == 1
    assert ask(client, "system-two").status_code == 200
    assert client.get("/api/account/model").json()["data"]["trial"]["used"] == 2


@pytest.mark.parametrize("api_key", ["x", "abcd", "1234567"])
def test_short_provider_keys_never_disclose_complete_value_in_hint(setup, api_key):
    database, settings, app, client = setup
    register(client)
    response = client.patch("/api/account/model", headers=ACTION, json={"expected_version": 0,
        "base_url": "https://public.example/v1", "api_key": api_key, "model": "qwen"})
    assert response.status_code == 200
    assert response.json()["data"]["api_key_hint"] == "••••"
    assert client.get("/api/account/model").json()["data"]["api_key_hint"] == "••••"


def test_same_browser_account_switch_cannot_reuse_device_admission(setup):
    database, settings, app, client = setup
    with app.state.access_service.transaction() as session:
        policy = app.state.access_service.locked_policy(session)
        policy.quota_enabled, policy.quota_limit, policy.quota_period = True, 1, "LIFETIME"
    register(client, "alice")
    assert ask(client, "same-request").status_code == 200
    register(client, "bob")
    switched = ask(client, "same-request")
    assert switched.status_code == 429 and switched.json()["error"]["code"] == "QUOTA_EXCEEDED"
    assert client.get("/api/account/model").json()["data"]["trial"]["used"] == 0


def test_two_accounts_scope_history_runs_feedback_preferences(setup):
    database, settings, app, alice = setup
    alice_id = register(alice)
    first = ask(alice, "shared-id")
    assert first.status_code == 200
    run_id, conversation_id = first.json()["meta"]["run_id"], first.json()["meta"]["conversation_id"]
    feedback = alice.post("/api/feedback", json={"category": "OTHER", "note": "alice-private-note", "run_id": run_id})
    assert feedback.status_code == 201
    assert alice.patch("/api/preferences/language", json={"expected_version": 0, "value": "JAVA",
        "source_message": "alice-private-preference"}).status_code == 200
    bob = TestClient(app, client=("198.51.100.10", 1))
    bob_id = register(bob, "bob")
    assert bob.get(f"/api/conversations/{conversation_id}").status_code == 404
    assert bob.get(f"/api/runs/{run_id}").status_code == 404
    assert bob.get(f"/api/runs/{run_id}/events").status_code == 404
    assert bob.post(f"/api/runs/{run_id}/cancel").status_code == 404
    assert ask(bob, conversation_id=conversation_id).status_code == 404
    assert bob.get("/api/feedback").json()["data"] == []
    assert bob.get("/api/preferences").json()["data"] == []
    assert "alice-private-preference" in alice.get("/api/preferences").text
    assert "alice-private-note" in alice.get("/api/feedback").text
    assert ask(bob, "shared-id").status_code == 200
    assert len(app.state.query_service.services) == 2
    assert app.state.query_service.services[alice_id] is not app.state.query_service.services[bob_id]
    with database.session() as session:
        assert set(session.scalars(select(AgentTurn.user_id))) == {alice_id, bob_id}


def test_gateway_routes_real_tenant_events_default_and_personal_without_global_fallback(tmp_path, monkeypatch):
    import interview_intelligence.agent.model_gateway as gateway_module
    sent = []
    original = httpx.AsyncClient
    plan = QuerySpec(action="LIST").model_dump(mode="json")
    def provider(request):
        sent.append((str(request.url), request.headers["authorization"], json.loads(request.content)["model"]))
        if sent[-1][2] == "own-fail":
            return httpx.Response(401, json={"error": "server echoed own-secret"})
        text = json.dumps(plan)
        chunk = {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
        finish = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\ndata: " + json.dumps(finish) + "\n\ndata: [DONE]\n\n")
    class ProviderClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(provider)
            super().__init__(*args, **kwargs)
    monkeypatch.setattr(gateway_module.httpx, "AsyncClient", ProviderClient)
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))])
    database = create_database("sqlite:///:memory:")
    app = create_app(database, configuration(tmp_path, model_api_key="global-paid-secret", model_base_url="https://paid.example/v1"))
    client = TestClient(app)
    register(client)
    result = ask(client, "default")
    assert result.status_code == 200, result.text
    assert sent == [("http://127.0.0.1:18792/v1/chat/completions", "Bearer default-secret", "qwen-default")]
    assert client.get(f"/api/runs/{result.json()['meta']['run_id']}").status_code == 200
    saved = client.patch("/api/account/model", headers=ACTION, json={"expected_version": 0,
        "base_url": "https://public.example/v1", "api_key": "own-secret", "model": "own-qwen"})
    assert saved.status_code == 200
    assert ask(client, "personal").status_code == 200
    assert sent[-1] == ("https://public.example/v1/chat/completions", "Bearer own-secret", "own-qwen")
    assert all("paid.example" not in url for url, _, _ in sent)
    assert client.get("/api/account/model").json()["data"]["trial"]["used"] == 1
    assert client.patch("/api/account/model", headers=ACTION, json={"expected_version": 1,
        "base_url": "https://public.example/v1", "model": "own-fail"}).status_code == 200
    failed = ask(client, "own-provider-failure")
    assert failed.status_code == 503 and "own-secret" not in failed.text
    receipt = client.get("/api/query/receipts/own-provider-failure")
    assert "own-secret" not in receipt.text
    assert sent[-1] == ("https://public.example/v1/chat/completions", "Bearer own-secret", "own-fail")
    assert client.get("/api/account/model").json()["data"]["trial"]["used"] == 1


@pytest.mark.parametrize("endpoint", ["http://public.example/v1", "https://127.0.0.1/v1", "https://user:secret@public.example/v1",
    "https://public.example:9999/v1", "https://public.example/v1?secret=value", "https://public.example/v1#secret"])
def test_personal_endpoint_restrictions(endpoint):
    with pytest.raises(ValueError):
        validate_tenant_endpoint(endpoint)


def test_dns_rebinding_blocks_private_addresses_at_socket_creation(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", port))])
    with pytest.raises(ValueError, match="PROVIDER_ENDPOINT_FORBIDDEN"):
        PublicSyncBackend().connect_tcp("was-public.example", 443)
    with pytest.raises(ValueError, match="PROVIDER_ENDPOINT_FORBIDDEN"):
        asyncio.run(PublicAsyncBackend().connect_tcp("was-public.example", 443))


def test_cross_database_concurrent_tenant_cap_and_restart(tmp_path):
    url = f"sqlite:///{(tmp_path / 'tenants.db').as_posix()}"
    settings = configuration(tmp_path)
    databases = [create_database(url), create_database(url)]
    services = [TenantService(db, settings, AccessService(db, settings)) for db in databases]
    tenant_id = str(uuid4())
    with databases[0].session() as session, session.begin():
        session.add(TenantAccount(id=tenant_id, username="concurrent", password_hash="test-hash"))
    scope = {"type": "http", "method": "POST", "path": "/api/query", "headers": [],
        "client": ("198.51.100.9", 1), "server": ("test", 80), "scheme": "http", "query_string": b""}
    seed = Request(scope)
    visitor, _, _ = services[0].access.observe(seed)
    def admission(index):
        request = Request(scope.copy())
        request.state.access_visitor_id, request.state.access_ip, request.state.tenant_id = visitor, "198.51.100.9", tenant_id
        try:
            services[index % 2].admit(request, f"query-{index}", {"message": f"query {index}"})
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(admission, range(32)))
    assert statuses.count(200) == 10 and statuses.count(429) == 22
    with databases[1].session() as session:
        assert session.get(TenantAccount, tenant_id).trial_used == 10
        assert session.scalar(select(func.count()).select_from(TenantAdmission)) == 10
    restarted = TenantService(create_database(url), settings, AccessService(create_database(url), settings))
    assert restarted.model_status(tenant_id)["trial"]["remaining"] == 0


def test_incomplete_system_provider_and_admin_corpus_controls(tmp_path):
    database = create_database("sqlite:///:memory:")
    settings = configuration(tmp_path, admin_access_token="admin-secret").model_copy(update={"tenant_default_api_key": None})
    client = TestClient(create_app(database, settings, query_planner=OfflinePlanner()))
    register(client)
    assert ask(client).status_code == 503
    assert client.get("/api/account/model").json()["data"]["trial"]["used"] == 0
    assert client.post("/api/ingest", json={"idempotency_key": "test"}).status_code == 401
    assert client.post("/api/task-annotations/publish", json={"expected_version": 1}).status_code == 401


def test_same_conversation_competing_turn_does_not_spend_trial(tmp_path):
    started, release = Event(), Event()
    class SlowPlanner:
        async def plan(self, run, context):
            started.set()
            await asyncio.to_thread(release.wait, 5)
            return QuerySpec(action="LIST")
    app = create_app(create_database("sqlite:///:memory:"), configuration(tmp_path), query_planner=SlowPlanner())
    first = TestClient(app, client=("198.51.100.8", 1))
    register(first)
    conversation = first.post("/api/conversations").json()["data"]["conversation_id"]
    second = TestClient(app, client=("198.51.100.8", 2))
    second.cookies.update(first.cookies)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(ask, first, "in-flight", conversation_id=conversation, expected_version=0)
        try:
            assert started.wait(5)
            competing = ask(second, "competing", conversation_id=conversation, expected_version=0)
            assert competing.status_code == 409 and competing.json()["error"]["code"] == "QUERY_IN_PROGRESS"
            assert second.get("/api/account/model").json()["data"]["trial"]["used"] == 1
        finally:
            release.set()
        assert running.result(timeout=5).status_code == 200
    assert second.get("/api/account/model").json()["data"]["trial"]["used"] == 1


def test_personal_malformed_reranker_response_is_sanitized_and_budget_codes_preserved():
    from interview_intelligence.tenant_retriever import PersonalReranker
    calls = []
    def complete(**payload):
        calls.append(payload["model"])
        return SimpleNamespace(usage=None, model="own-qwen", choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(content='{"query_task":"provider-echoed-private-api-key"'))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete)))
    reranker = PersonalReranker(model="own-qwen", client=client)
    with pytest.raises(ValueError) as failure:
        reranker.rerank("Redis", [{"canonical_question_id": "question-one", "canonical_text": "Redis"}])
    assert str(failure.value) == "MODEL_PROVIDER_UNAVAILABLE"
    assert calls == ["own-qwen"]
    def exhausted(**payload):
        raise ValueError("QUERY_TOKEN_BUDGET_EXCEEDED")
    client.chat.completions.create = exhausted
    with pytest.raises(ValueError, match="^QUERY_TOKEN_BUDGET_EXCEEDED$"):
        reranker.rerank("Redis", [{"canonical_question_id": "question-one", "canonical_text": "Redis"}])
    def wrapped_budget(query, candidates):
        try:
            raise ValueError("QUERY_TOKEN_BUDGET_EXCEEDED")
        except ValueError as error:
            raise ValueError("RERANK_CANDIDATE_VERIFICATION_FAILED_CLOSED") from error
    reranker._rank_candidates = wrapped_budget
    with pytest.raises(ValueError, match="^QUERY_TOKEN_BUDGET_EXCEEDED$"):
        reranker.rerank("Redis", [{"canonical_question_id": "question-one", "canonical_text": "Redis"}])
