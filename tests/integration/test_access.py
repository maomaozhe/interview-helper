"""Access controls are exercised through HTTP and cross-connection admissions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from interview_intelligence.api import create_app
from interview_intelligence.access import ADMIN_COOKIE, DEVICE_COOKIE, AccessService, register_access
from interview_intelligence.access_models import AccessAdmission, AccessAudit, AccessCounter, AccessPolicy, AccessRequest
from interview_intelligence.config import Settings
from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.domain.models import create_database, now_utc


SECRET = "independent-admin-secret"
AUTH = {"Authorization": f"Bearer {SECRET}", "X-Admin-Action": "1"}


class OfflinePlanner:
    async def plan(self, run, context):
        return QuerySpec(action="LIST", page_size=20)


@pytest.fixture
def panel():
    database = create_database("sqlite:///:memory:")
    app = create_app(database, Settings(admin_access_token=SECRET), query_planner=OfflinePlanner())
    return database, app, TestClient(app, client=("198.51.100.5", 12345))


def configure(client, **changes):
    version = client.get("/api/admin/policy", headers=AUTH).json()["data"]["version"]
    return client.patch("/api/admin/policy", headers=AUTH, json={"expected_version": version, **changes})


def listing(client, request_id=None):
    # A user-submitted natural-language question stays chargeable when the
    # offline decision model returns a LIST plan. Passive SQL is tested separately.
    return client.post("/api/query", json={"request_id": request_id or str(uuid4()), "message": "列出题目"})


def first_visitor(client):
    return client.get("/api/admin/visitors", headers=AUTH).json()["data"]["items"][0]


def test_admin_fails_closed_before_validation_and_requires_independent_secret():
    client = TestClient(create_app(create_database("sqlite:///:memory:"), Settings(internal_agent_token="internal")))
    for method, path in (("GET", "/api/admin/overview"), ("PATCH", "/api/admin/policy"), ("POST", "/api/admin/session")):
        response = client.request(method, path, json={"token": "private"}, headers=AUTH)
        assert response.status_code == 503
        assert "private" not in response.text


def test_session_cookie_security_tampering_csrf_and_secret_redaction(panel):
    database, app, client = panel
    assert client.get("/api/admin/policy").status_code == 401
    assert client.get("/api/admin/policy", headers={"Authorization": "Bearer internal"}).status_code == 401
    logged = client.post("/api/admin/session", headers={"X-Admin-Action": "1"}, json={"token": SECRET})
    assert logged.status_code == 200
    assert "HttpOnly" in logged.headers["set-cookie"] and "SameSite=strict" in logged.headers["set-cookie"]
    assert SECRET not in logged.text + logged.headers["set-cookie"]
    assert client.get("/api/admin/policy").status_code == 200
    assert client.patch("/api/admin/policy", json={"expected_version": 1}).status_code == 403
    assert client.patch("/api/admin/policy", headers={"X-Admin-Action": "1", "Origin": "https://evil.test"},
        json={"expected_version": 1}).status_code == 403
    malformed = client.post("/api/admin/session", headers=AUTH, json={"token": SECRET * 500})
    assert malformed.status_code == 422 and SECRET not in malformed.text
    value = client.cookies.get(ADMIN_COOKIE)
    client.cookies.clear()
    client.cookies.set(ADMIN_COOKIE, value[:-6] + "abcdef")
    assert client.get("/api/admin/session").status_code == 401
    secure_client = TestClient(app, base_url="https://testserver")
    secure_login = secure_client.post("/api/admin/session", headers={"X-Admin-Action": "1"}, json={"token": SECRET})
    assert "Secure" in secure_login.headers["set-cookie"]


def test_login_bruteforce_audits_without_credentials(panel):
    database, app, client = panel
    for _ in range(5):
        response = client.post("/api/admin/session", headers={"X-Admin-Action": "1"}, json={"token": "wrong-private-value"})
        assert response.status_code == 401
    response = client.post("/api/admin/session", headers={"X-Admin-Action": "1"}, json={"token": SECRET})
    assert response.status_code == 429 and response.headers["retry-after"]
    audit = client.get("/api/admin/audit", headers=AUTH).text
    assert "LOGIN_FAILED" in audit and "LOGIN_THROTTLED" in audit
    assert "wrong-private-value" not in audit and SECRET not in audit


def test_proxy_chain_is_trusted_only_from_configured_peer_and_secure_proto():
    database = create_database("sqlite:///:memory:")
    settings = Settings(admin_access_token=SECRET, trusted_proxy_cidrs=["10.0.0.0/8"])
    app = create_app(database, settings)
    untrusted = TestClient(app, client=("198.51.100.5", 1))
    untrusted.get("/api/topics", headers={"X-Forwarded-For": "1.1.1.1", "X-Forwarded-Proto": "https"})
    assert first_visitor(untrusted)["ip"] == "198.51.100.5"
    trusted = TestClient(app, client=("10.1.1.5", 1))
    response = trusted.get("/api/topics", headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.8, 10.2.2.2", "X-Forwarded-Proto": "https"})
    assert "Secure" in response.headers["set-cookie"]
    rows = trusted.get("/api/admin/visitors", headers=AUTH).json()["data"]["items"]
    assert {row["ip"] for row in rows} == {"198.51.100.5", "203.0.113.8"}
    response = trusted.post("/api/admin/session", headers={"X-Admin-Action": "1", "X-Forwarded-Proto": "https", "Origin": "https://testserver"}, json={"token": SECRET})
    assert response.status_code == 200 and "Secure" in response.headers["set-cookie"]


def test_signed_device_cookie_and_device_ip_bans_preserve_admin_recovery(panel):
    database, app, client = panel
    client.get("/api/topics")
    visitor = first_visitor(client)
    assert client.patch(f"/api/admin/visitors/{visitor['id']}", headers=AUTH, json={"blocked": True, "reason": "test"}).status_code == 200
    assert client.get("/api/topics").status_code == 403
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/admin/policy", headers=AUTH).status_code == 200
    assert client.patch(f"/api/admin/visitors/{visitor['id']}", headers=AUTH, json={"blocked": False}).status_code == 200
    assert client.get("/api/topics").status_code == 200
    assert client.patch("/api/admin/ips", headers=AUTH, json={"ip": visitor["ip"], "blocked": True}).status_code == 200
    assert client.get("/api/topics").status_code == 403
    assert client.patch("/api/admin/ips", headers=AUTH, json={"ip": visitor["ip"], "blocked": False}).status_code == 200
    assert client.get("/api/topics").status_code == 200
    raw = client.cookies.get(DEVICE_COOKIE)
    client.cookies.clear()
    client.cookies.set(DEVICE_COOKIE, raw[:-6] + "abcdef")
    client.get("/api/topics")
    assert client.get("/api/admin/visitors", headers=AUTH).json()["data"]["total"] == 2


def test_quota_off_counts_all_scopes_and_replay_never_bypasses_bans(panel):
    database, app, client = panel
    first = listing(client, "unique-first")
    assert first.status_code == 200
    assert first_visitor(client)["quota_used"] == 1
    assert configure(client, quota_enabled=True, quota_limit=1).status_code == 200
    assert listing(client, "unique-first").status_code == 200
    blocked = listing(client, "second")
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "QUOTA_EXCEEDED"
    assert blocked.headers["retry-after"]
    assert first_visitor(client)["quota_used"] == 1
    conflict = client.post("/api/query", json={"request_id": "unique-first", "message": "不同的提问"})
    assert conflict.status_code == 409
    visitor = first_visitor(client)
    client.patch(f"/api/admin/visitors/{visitor['id']}", headers=AUTH, json={"blocked": True})
    assert listing(client, "unique-first").status_code == 403
    client.patch(f"/api/admin/visitors/{visitor['id']}", headers=AUTH, json={"blocked": False})
    client.post(f"/api/admin/visitors/{visitor['id']}/reset-quota", headers=AUTH)
    assert listing(client, "third").status_code == 200
    # Lifetime and IP counters were observed even while the switch was off.
    configure(client, quota_period="LIFETIME", quota_scope="IP")
    assert first_visitor(client)["quota_used"] == 2
    assert listing(client, "fourth").status_code == 429


def test_invalid_bodies_not_charged_and_all_expensive_entries_are_admitted(panel):
    database, app, client = panel
    configure(client, quota_enabled=True, quota_limit=1)
    assert client.post("/api/query", json={"message": ""}).status_code == 422
    assert first_visitor(client)["quota_used"] == 0
    assert listing(client).status_code == 200
    for path, body in (("/api/query", {"message": "hi", "request_id": "direct"}),
                       ("/api/questions/query", {"message": "hi", "request_id": "alias"}),
                       ("/api/agent/chat", {"message": "hi"})):
        assert client.post(path, json=body).status_code == 429
    assert client.get("/api/questions/search", params={"query": "test", "pipeline": "BM25"}).status_code == 429
    conversation = client.post("/api/conversations").json()["data"]["conversation_id"]
    assert client.post(f"/api/conversations/{conversation}/messages", json={"message": "hi", "request_id": "async"}).status_code == 429


def test_legacy_get_search_and_chat_reused_headers_never_skip_charges(panel):
    database, app, client = panel
    configure(client, quota_enabled=True, quota_limit=1)
    first = client.get("/api/questions/search", params={"query": "test"}, headers={"X-Request-ID": "repeat"})
    assert first.status_code == 503  # Admission counts even if the provider/index fails.
    second = client.get("/api/questions/search", params={"query": "test"}, headers={"X-Request-ID": "repeat"})
    assert second.status_code == 429
    visitor = first_visitor(client)
    client.post(f"/api/admin/visitors/{visitor['id']}/reset-quota", headers=AUTH)
    # Exercise the actual legacy path, without the fixture's injected planner.
    legacy = TestClient(create_app(database, Settings(admin_access_token=SECRET)), client=("198.51.100.5", 1))
    legacy.cookies.set(DEVICE_COOKIE, client.cookies.get(DEVICE_COOKIE))
    first = legacy.post("/api/agent/chat", json={"message": "hi", "request_id": "repeat"})
    assert first.status_code != 429
    assert legacy.post("/api/agent/chat", json={"message": "hi", "request_id": "repeat"}).status_code == 429


def test_rate_cas_and_restart_durability(tmp_path, monkeypatch):
    # Keep all admissions in one minute: a real wall-clock boundary legitimately
    # opens the next rate bucket and must not make this restart test flaky.
    frozen = now_utc()
    monkeypatch.setattr("interview_intelligence.access.now_utc", lambda: frozen)
    url = f"sqlite:///{(tmp_path / 'access.db').as_posix()}"
    database = create_database(url)
    client = TestClient(create_app(database, Settings(admin_access_token=SECRET), query_planner=OfflinePlanner()), client=("198.51.100.5", 1))
    assert configure(client, rate_enabled=True, rate_per_minute=1, quota_limit=99).status_code == 200
    assert client.patch("/api/admin/policy", headers=AUTH, json={"expected_version": 1, "quota_limit": 5}).status_code == 409
    assert listing(client, "first").status_code == 200
    assert listing(client, "first").status_code == 200
    assert listing(client, "second").status_code == 429
    cookie = client.cookies.get(DEVICE_COOKIE)
    restarted = TestClient(create_app(create_database(url), Settings(admin_access_token=SECRET), query_planner=OfflinePlanner()), client=("198.51.100.5", 1))
    restarted.cookies.set(DEVICE_COOKIE, cookie)
    response = listing(restarted, "third")
    assert response.status_code == 429 and response.json()["error"]["code"] == "RATE_LIMITED"
    assert first_visitor(restarted)["quota_used"] == 1


def test_concurrent_exact_cap_across_separate_sqlite_connections(tmp_path):
    url = f"sqlite:///{(tmp_path / 'concurrent.db').as_posix()}"
    database = create_database(url)
    settings = Settings(admin_access_token=SECRET)
    services = [AccessService(database, settings), AccessService(create_database(url), settings)]
    seed = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "client": ("198.51.100.5", 1), "scheme": "http", "server": ("test", 80)})
    identifier, _, _ = services[0].observe(seed)
    with services[0].transaction() as session:
        policy = services[0].locked_policy(session)
        policy.quota_enabled, policy.quota_limit = True, 3
    def attempt(index):
        request = Request(seed.scope.copy())
        request.state.access_visitor_id, request.state.access_ip = identifier, "198.51.100.5"
        try:
            services[index % 2].admit(request, f"request-{index}", {"message": str(index)})
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(attempt, range(20)))
    assert statuses.count(200) == 3 and statuses.count(429) == 17
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(AccessAdmission)) == 3


def test_metrics_exclusions_retention_and_private_route_values(panel):
    database, app, client = panel
    assert client.get("/").status_code == 200
    client.get("/api/topics")
    client.get("/api/questions/private-user-value")
    client.get("/api/health")
    client.get("/internal/query/private-run/context")
    client.get("/api/admin/policy", headers=AUTH)
    overview = client.get("/api/admin/overview?window=1h", headers=AUTH).json()["data"]
    assert overview["summary"]["visitors"] == 1
    assert overview["summary"]["ips"] == 1
    assert overview["summary"]["page_views"] == 1
    assert overview["summary"]["requests"] == 3
    assert overview["summary"]["current_qps"] == round(3 / 60, 4)
    assert sum(row["requests"] for row in overview["series"]) == 3
    visitor = first_visitor(client)
    with database.session() as session, session.begin():
        paths = list(session.scalars(select(AccessRequest.path)))
        assert "private-user-value" not in json.dumps(paths)
        old_request = AccessRequest(visitor_id=visitor["id"], ip=visitor["ip"], path="/old", status=200,
            duration_ms=1, created_at=now_utc() - timedelta(days=8))
        old_audit = AccessAudit(action="OLD", target="test", created_at=now_utc() - timedelta(days=31))
        session.add_all([old_request, old_audit])
        session.get(AccessCounter, "maintenance").used = 0
    client.get("/api/topics")
    with database.session() as session:
        assert session.get(AccessRequest, old_request.id) is None
        assert session.get(AccessAudit, old_audit.id) is None


def test_trusted_proxy_env_empty_comma_and_json(monkeypatch):
    for value, expected in (("", []), ("10.0.0.0/8, 127.0.0.1/32", ["10.0.0.0/8", "127.0.0.1/32"]),
                            ('["192.0.2.0/24"]', ["192.0.2.0/24"])):
        monkeypatch.setenv("TRUSTED_PROXY_CIDRS", value)
        assert Settings().trusted_proxy_cidrs == expected


def test_admin_template_prefix_and_no_store(panel):
    database, app, client = panel
    for path in ("/admin", "/admin/"):
        response = client.get(path)
        assert response.status_code == 200
        assert '<base href="/">' in response.text
        assert "base-uri 'self'" in response.headers["content-security-policy"]
    prefixed = TestClient(create_app(database, Settings(admin_access_token=SECRET, api_root_path="/interview")))
    assert '<base href="/interview/">' in prefixed.get("/admin").text
    assert "no-store" in client.get("/api/admin/visitors", headers=AUTH).headers["cache-control"]


def test_frozen_migration_upgrades_existing_previous_schema_and_restarts(tmp_path, monkeypatch):
    from alembic.config import Config
    from alembic import command
    from sqlalchemy import inspect
    from interview_intelligence.domain.models import Base, CorpusState
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    database = create_database(url, create_tables=False)
    Base.metadata.create_all(database.engine, tables=[table for table in Base.metadata.sorted_tables
        if not table.name.startswith("access_")])
    with database.session() as session, session.begin():
        session.add(CorpusState(id=1, current_revision=12))
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.stamp(config, "c21d4857a941")
    command.upgrade(config, "head")
    assert "access_request" in inspect(database.engine).get_table_names()
    with database.session() as session:
        assert session.get(CorpusState, 1).current_revision == 12
    client = TestClient(create_app(create_database(url), Settings(admin_access_token=SECRET)))
    assert client.get("/api/admin/policy", headers=AUTH).json()["data"]["quota_enabled"] is False


def test_active_stream_is_counted_on_response_headers_before_completion():
    import asyncio
    database = create_database("sqlite:///:memory:")
    app = FastAPI()
    service = register_access(app, database, Settings(admin_access_token=SECRET))
    hold = asyncio.Event()
    @app.get("/stream")
    def stream():
        async def body():
            yield "first"
            await hold.wait()
        return StreamingResponse(body())
    async def exercise():
        header = asyncio.Event()
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "path": "/stream", "raw_path": b"/stream", "root_path": "",
            "query_string": b"", "headers": [], "client": ("198.51.100.9", 1),
            "server": ("testserver", 80), "scheme": "http"}
        async def receive():
            await hold.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            if message["type"] == "http.response.start":
                header.set()
        task = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(header.wait(), 3)
        assert not task.done()
        result = await asyncio.to_thread(service.overview, "1h")
        assert result["summary"]["requests"] == 1
        hold.set()
        await asyncio.wait_for(task, 3)
    asyncio.run(exercise())


def test_beijing_daily_boundary_shared_ip_scope_and_lifetime_error(panel, monkeypatch):
    from datetime import datetime, timezone
    import interview_intelligence.access as access_module
    database, app, client = panel
    before = datetime(2026, 10, 9, 15, 59, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(access_module, "now_utc", lambda: before)
    configure(client, quota_enabled=True, quota_limit=1, quota_scope="IP", quota_period="DAY")
    assert listing(client, "day-first").status_code == 200
    other_device = TestClient(app, client=("198.51.100.5", 54321))
    assert listing(other_device, "ip-shared").status_code == 429
    denied = listing(client, "day-second")
    assert denied.status_code == 429 and denied.headers["retry-after"] == "1"
    monkeypatch.setattr(access_module, "now_utc", lambda: before + timedelta(seconds=1))
    assert listing(other_device, "day-new").status_code == 200
    configure(client, quota_period="LIFETIME")
    denied = listing(client, "lifetime-denied")
    assert denied.status_code == 429
    assert denied.json()["error"]["code"] == "QUOTA_EXCEEDED"
    assert denied.json()["error"]["retryable"] is False
    assert "累计" in denied.json()["error"]["message"] and "retry-after" not in denied.headers


def test_reset_reason_and_policy_version_are_checked(panel):
    database, app, client = panel
    assert listing(client).status_code == 200
    visitor = first_visitor(client)
    version = client.get("/api/admin/policy", headers=AUTH).json()["data"]["version"]
    configure(client, quota_scope="IP")
    reset_url = f"/api/admin/visitors/{visitor['id']}/reset-quota"
    assert client.post(reset_url, headers=AUTH, json={"reason": "test", "expected_version": version}).status_code == 409
    assert first_visitor(client)["quota_used"] == 1
    assert client.post(reset_url, headers=AUTH, json={"reason": "approved reset", "expected_version": version + 1}).status_code == 200
    audit = client.get("/api/admin/audit", headers=AUTH).json()["data"]["items"]
    event = next(row for row in audit if row["action"] == "QUOTA_RESET")
    assert event["details"]["reason"] == "approved reset"
    assert event["details"]["scope"] == "IP"


def test_page_startup_sql_browsing_does_not_spend_dialogue_or_query_rate_limits(panel):
    database, app, client = panel
    configure(client, quota_enabled=True, quota_limit=1, rate_enabled=True, rate_per_minute=1)
    assert client.get("/").status_code == 200
    for index in range(4):
        assert client.get("/api/questions/list").status_code == 200
        assert client.post("/api/questions/list", json={"request_id": f"passive-{index}", "list_request": {}}).status_code == 200
    visitor = first_visitor(client)
    assert visitor["quota_used"] == 0 and visitor["chat_requests"] == 0
    assert listing(client, "first-user-message").status_code == 200
    assert first_visitor(client)["quota_used"] == 1
    assert listing(client, "second-user-message").status_code == 429
    # Browsing remains available after the conversation allowance is exhausted.
    assert client.post("/api/questions/list", json={"request_id": "browse-at-cap", "list_request": {"page_size": 1}}).status_code == 200
    assert first_visitor(client)["quota_used"] == 1
    overview = client.get("/api/admin/overview", headers=AUTH).json()["data"]["summary"]
    assert overview["requests"] == 12 and overview["chat_requests"] == 1
