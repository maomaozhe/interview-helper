"""Collectors must execute host logic and retain snapshot/actor boundaries."""
from types import SimpleNamespace
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from eval.budget import EvaluationBudget
from eval.collect import HttpQueryCollector, ProviderCollector, RetrievalCollector, SQLCollector, collect_rows
from eval.common import digest, read_json
from eval.oracle import frequency_list
from eval.prepare import prepare
from eval.serve import evaluation_app
from eval.snapshot import current_snapshot, export_snapshot
from eval.validate_gold import GoldValidationError
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import QuestionOccurrence, SourceRevision
from interview_intelligence.providers.gate import ModelCallGate
from test_analytics import seed_corpus


def test_evaluation_replay_freezes_plan_and_sql_date_and_records_candidate_version():
    from datetime import date
    from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec

    db, _, _ = seed_corpus()
    settings = Settings(local_user_id="eval-frozen-clock", query_prompt_version="query_agent_v7",
                        model_api_key=None, elasticsearch_url=None)
    frozen = date(2026, 9, 29)
    app = evaluation_app(db, settings, as_of=frozen)
    service = app.state.query_service
    run = service.begin(QueryRequest(message="列出题目", request_id="clock-replay"))
    assert service.model_context(run)["today"] == frozen.isoformat()
    result = service.execute(run, "list_questions", QuerySpec(action="LIST"))
    assert result["planning"]["version"] == "query_agent_v7"
    assert result["facts"]["meta"]["as_of"] == frozen.isoformat()
    with TestClient(app) as client:
        context = client.get("/api/evaluation/context").json()
        assert context["snapshot"]["as_of"] == frozen.isoformat()
        assert context["query_prompt_version"] == "query_agent_v7"
        assert context["as_of_frozen"] is True
        # The generic HTTP envelope must agree with SQL/model/context clocks.
        response = client.get("/api/conversations").json()
        assert response["meta"]["as_of"] == frozen.isoformat()


def test_live_api_response_clock_keeps_the_current_date():
    from datetime import date
    from interview_intelligence.api import create_app
    db, _, _ = seed_corpus()
    settings = Settings(local_user_id="eval-live-clock", model_api_key=None, elasticsearch_url=None)
    with TestClient(create_app(db, settings)) as client:
        assert client.get("/api/conversations").json()["meta"]["as_of"] == date.today().isoformat()


def test_provider_extraction_runs_ingestion_validation_and_normalization(monkeypatch, tmp_path):
    text = "# 字节一面\nRedis为什么快？"
    source = tmp_path / "source.md"
    source.write_text(text, encoding="utf-8")
    payload = {"document_kind": "INTERVIEW_REPORT", "exclusion_reason": None, "interviews": [{
        "local_id": "s1", "session_kind": "SINGLE", "metadata": {
            "company_raw": "字节", "position_raw": None, "round_raw": "一面",
            "interview_date_raw": None, "publish_date_raw": None}, "questions": [{
                "local_id": "q1", "raw_quote": "Redis为什么快？", "quote_index": None,
                "normalized_question": "Redis 为什么快？", "topic_l1": "Redis", "topic_l2": "性能优化",
                "question_type": "PRINCIPLE", "evidence_kind": "INTERVIEW_QUESTION", "confidence": .99}],
        "followups": []}]}
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs:
        SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20), model="test"))))
    monkeypatch.setattr("eval.collect.capped_client", lambda settings: client)
    settings = Settings(model_api_key="test", model_base_url="http://model.test/v1", extraction_stream=False)
    collector = ProviderCollector("extraction", tmp_path, settings,
        ModelCallGate(tmp_path / "gate", minimum_interval_seconds=0), EvaluationBudget(1, 100000),
        extraction_prompt_version="extract_question_v3")
    calls = []
    result = collector({"source_path": source.name, "source_hash": digest(source.read_bytes())}, calls)
    assert result["result"]["decision"] == "INCLUDED"
    assert result["result"]["sessions"][0]["metadata"]["round"] == "FIRST"
    assert result["result"]["questions"][0]["source_spans"][0]["quote"] == "Redis为什么快？"
    assert calls[0]["status"] == "SUCCEEDED" and calls[0]["prompt_version"] == "extract_question_v3"


def published_fixture(tmp_path):
    db, _, _ = seed_corpus()
    settings = Settings(snapshot_root=tmp_path / "sources", model_api_key=None, elasticsearch_url=None)
    settings.snapshot_root.mkdir()
    with db.session() as session, session.begin():
        for index, revision in enumerate(session.scalars(select(SourceRevision))):
            text = f"面经 {index}\nRedis为什么快？\nRedis持久化方式？\n"
            raw = text.encode("utf-8")
            revision.raw_file_hash = digest(raw)
            (settings.snapshot_root / (revision.raw_file_hash + ".md")).write_bytes(raw)
        for q in session.scalars(select(QuestionOccurrence)):
            # Associate spans through the source's actual interview/build.
            from interview_intelligence.domain.models import Interview, DocumentBuild
            build = session.get(DocumentBuild, session.get(Interview, q.interview_id).build_id)
            revision = session.get(SourceRevision, build.source_revision_id)
            text = (settings.snapshot_root / (revision.raw_file_hash + ".md")).read_text(encoding="utf-8")
            start = text.index(q.raw_question)
            q.source_spans = [{"revision_id": revision.id, "start_char": start, "end_char": start+len(q.raw_question), "quote": q.raw_question}]
    return db, settings


def test_exported_oracle_and_actual_sql_agree_on_same_occurrence_and_all_pages(tmp_path):
    db, settings = published_fixture(tmp_path)
    facts = export_snapshot(db, settings, tmp_path / "snapshot", as_of="2026-10-05")
    assert read_json(tmp_path / "snapshot" / "manifest.json")["facts_sha256"] == digest(facts)
    collector = SQLCollector(db, settings, facts["snapshot"])
    for request in ({"page_size": 1}, {"company": "字节", "round": "SECOND", "page_size": 1},
                    {"start_date": "2026-08-02", "end_date": "2026-08-03", "page_size": 1},
                    {"annotation_status": "UNKNOWN", "page_size": 1}, {"coding_focus": "ENGINEERING", "page_size": 1}):
        expected = frequency_list(facts, request)
        result = collector({"request": request, "all_pages": True}, [])
        rows = result["result"]["rows"]
        assert [r["canonical_question_id"] for r in rows] == expected["ids"]
        assert [r["occurrence_count"] for r in rows] == expected["counts"]
        assert result["model_calls"] == []
    counts = prepare(tmp_path / "workbench", snapshot_root=tmp_path / "snapshot", count=2)
    assert counts["extraction"] == 2 and counts["task_labels"] == 3
    from eval.common import read_jsonl
    assert all(not row["human_verified"] for row in read_jsonl(tmp_path / "workbench" / "task_labels.jsonl"))


def test_rerank_gets_the_exact_hybrid_ids_texts_and_one_shared_query_vector(tmp_path):
    db, settings = published_fixture(tmp_path)
    class Encoder:
        version = "fixture-encoder"
        count = 0
        def embed_query(self, query):
            self.count += 1
            return [1.0, 0.0]
    class Reranker:
        version = "fixture-reranker"
        seen = None
        def rerank(self, query, candidates):
            self.seen = candidates
            return list(reversed(candidates))
    encoder, reranker = Encoder(), Reranker()
    collector = RetrievalCollector(db, settings.model_copy(update={"elasticsearch_url": "http://fixture"}), {"physical_indices": ["immutable-index"]}, encoder, reranker)
    def retrieve(query, eligible, pipeline, top_k):
        assert collector.retriever.alias == "immutable-index" and top_k == 50
        assert collector.retriever.encoder.embed_query(query) == [1.0, 0.0]
        return {"data": [{"canonical_question_id": key, "stage_scores": {"x": 1}} for key in eligible], "meta": {"pipeline": pipeline}}
    collector.retriever.retrieve = retrieve
    result = collector({"query": "Redis", "filters": {}}, [])
    hybrid = result["pipelines"]["HYBRID"]
    rerank = result["pipelines"]["HYBRID_RERANK"]
    assert encoder.count == 1
    assert hybrid["candidate_ids"] == rerank["candidate_ids"]
    assert hybrid["candidate_sha256"] == rerank["candidate_sha256"] == digest(reranker.seen)
    assert all(c["canonical_text"] for c in reranker.seen)
    assert rerank["ranked"] == list(reversed(hybrid["ranked"]))


def test_failed_retrieval_pipeline_marks_the_complete_sample_failed(tmp_path):
    db, settings = published_fixture(tmp_path)
    class Encoder:
        version = "fixture-encoder"
        def embed_query(self, query): raise ValueError("EVALUATION_BUDGET_EXCEEDED")
    collector = RetrievalCollector(db, settings.model_copy(update={"elasticsearch_url":"http://fixture"}),
        {"physical_indices":["immutable-index"]}, Encoder(), None)
    collector.retriever.retrieve = lambda *args: {"data":[],"meta":{}}
    result = collector({"query":"Redis"}, [])
    assert result["failed"] is True
    assert result["pipelines"]["BM25"]["failed"] is False
    assert all(result["pipelines"][name]["failed"] for name in ("DENSE","HYBRID","HYBRID_RERANK"))


def test_evaluation_context_rejects_real_actor_and_reports_only_own_state(tmp_path):
    db, settings = published_fixture(tmp_path)
    with pytest.raises(GoldValidationError, match="ACTOR"):
        evaluation_app(db, settings.model_copy(update={"local_user_id": "local"}))
    app = evaluation_app(db, settings.model_copy(update={"local_user_id": "eval-fixture"}))
    result = TestClient(app).get("/api/evaluation/context").json()
    assert result["evaluation_host"] and result["state_count"] == result["event_count"] == 0
    assert result["model_calls"] == []
    assert result["query_planning_max_calls"] == settings.query_max_model_calls
    assert result["request_max_model_calls"] == settings.query_max_total_model_calls
    assert result["request_deadline_seconds"] == settings.query_deadline_seconds
    assert result["request_max_tokens"] == settings.query_max_tokens


def test_http_collection_preserves_conversation_version_and_replay_usage():
    snapshot = {"corpus_revision": 1, "indexed_revision": 1, "task_annotation_revision": 2, "as_of": "2026-10-05"}
    requests, stored, event_count, versions = [], {}, 0, []
    def handle(request):
        nonlocal event_count
        if request.url.path == "/api/evaluation/context":
            request_id = request.url.params.get("request_id")
            return httpx.Response(200, json={"evaluation_host": True, "user_id": "eval-fixture", "snapshot": snapshot,
                "state_count": 0, "conversation_count": 0, "event_count": event_count, "states": [], "write_events": [],
                "query_max_model_calls": 3, "query_max_tokens": 65536, "query_planning_max_calls": 3,
                "request_max_model_calls": 6, "request_max_tokens": 65536, "model_gate_shared": True,
                "run_status": "SUCCEEDED" if request_id in stored else None,
                "model_attempts": 1 if request_id in stored else None,
                "model_calls": [{"id": request_id, "input_tokens": 10, "output_tokens": 20}] if request_id in stored else []})
        import json
        payload = json.loads(request.content)
        requests.append(payload)
        replay = payload["request_id"] in stored
        if not replay:
            versions.append(payload.get("expected_version"))
            event_count += int(payload["message"] == "write")
            stored[payload["request_id"]] = {"data": [], "meta": {"conversation_id": "conversation", "conversation_version": len(versions),
                "planning": {"spec": {}}, "tool_trace": [{"name": "record_review" if payload["message"] == "write" else "list_questions"}]}}
        return httpx.Response(200, json=stored[payload["request_id"]])
    with httpx.Client(base_url="http://evaluation", transport=httpx.MockTransport(handle)) as client:
        budget = EvaluationBudget(10, 250000)
        collector = HttpQueryCollector(client, {"snapshot": snapshot}, budget, allow_writes=True)
        calls = []
        result = collector({"id": "scene", "turns": [{"message": "read"}, {"message": "write", "allow_write": True},
            {"message": "replay", "allow_write": True, "replay_previous": True}]}, calls)
    assert versions == [None, 1]
    assert requests[-1] == requests[-2]
    assert [t["result"]["write_event_delta"] for t in result["turns"]] == [0, 1, 0]
    assert result["turns"][-1]["model_calls"] == [] and len(calls) == 2
    assert budget.used_calls == 2 and budget.used_tokens == 60


def test_evaluation_context_requires_exact_authenticated_eval_account(tmp_path):
    from interview_intelligence.tenant import password_hash
    from interview_intelligence.tenant_models import TenantAccount
    db, settings = published_fixture(tmp_path)
    settings = settings.model_copy(update={"multi_tenant_enabled": True, "local_user_id": "eval-owner",
                                          "app_signing_key": "test-only-signing-key"})
    app = evaluation_app(db, settings)
    with db.session() as session, session.begin():
        for identifier in ("eval-owner", "eval-other"):
            session.add(TenantAccount(id=identifier, username=identifier, password_hash=password_hash("test-password"),
                provider_key="encrypted-fixture-no-decryption", provider_base_url="https://fixture.example/v1",
                provider_model="personal-query", provider_reranker_model="personal-rerank", provider_version=3))
    with TestClient(app) as client:
        assert client.get("/api/evaluation/context").status_code == 401
        for identifier, expected in (("eval-other", 403), ("eval-owner", 200)):
            response = client.post("/api/account/login", headers={"X-Tenant-Action": "1"},
                                   json={"username": identifier, "password": "test-password"})
            assert response.status_code == 200
            response = client.get("/api/evaluation/context")
            assert response.status_code == expected
        profile = response.json()["provider_profile"]
        assert profile["source"] == "personal" and profile["model"] == "personal-query"
        assert profile["reranker_model"] == "personal-rerank" and profile["embedding_model"] == settings.embedding_model
        assert "encrypted-fixture-no-decryption" not in response.text and "fixture.example" not in response.text


def test_evaluation_ledger_detects_turn_changes_without_exposing_conversation_text(tmp_path):
    from interview_intelligence.domain.models import AgentConversation, AgentTurn
    db, settings = published_fixture(tmp_path)
    settings = settings.model_copy(update={"local_user_id": "eval-ledger"})
    private = "private-conversation-text-must-not-appear"
    with db.session() as session, session.begin():
        session.add(AgentConversation(id="conversation", user_id="eval-ledger", version=1, state={"text": private}))
    with TestClient(evaluation_app(db, settings)) as client:
        first = client.get("/api/evaluation/context").json()
        with db.session() as session, session.begin():
            session.add(AgentTurn(id="turn", conversation_id="conversation", user_id="eval-ledger",
                request_id="external-request", payload_hash=digest(private), message=private, status="RUNNING"))
        second = client.get("/api/evaluation/context").json()
        assert first["conversation_count"] == second["conversation_count"] == 1
        assert first["conversation_ledger_sha256"] != second["conversation_ledger_sha256"]
        with db.session() as session, session.begin():
            turn = session.get(AgentTurn, "turn")
            turn.status = "SUCCEEDED"
            turn.response = {"facts": {"meta": {"timings": {"model_attempts": 3}}}}
        third = client.get("/api/evaluation/context")
        assert third.json()["conversation_ledger_sha256"] != second["conversation_ledger_sha256"]
        assert third.json()["conversation_ledger_sha256"] == client.get("/api/evaluation/context").json()["conversation_ledger_sha256"]
        assert private not in third.text and "external-request" not in third.text
        receipt = client.get("/api/evaluation/context", params={"request_id": "external-request"}).json()
        assert receipt["model_attempts"] == 3 and receipt["model_calls"] == []


def test_failed_http_request_retains_error_trajectory_and_write_evidence():
    snapshot={"corpus_revision":1,"indexed_revision":1,"task_annotation_revision":1,"as_of":"2026-10-05"}
    def handle(request):
        if request.url.path=="/api/evaluation/context":
            requested=request.url.params.get("request_id")
            return httpx.Response(200,json={"evaluation_host":True,"user_id":"eval-failed","snapshot":snapshot,
                "state_count":0,"conversation_count":0,"event_count":0,"states":[],"write_events":[],"model_calls":[],
                "query_max_model_calls":3,"query_max_tokens":65536,"query_planning_max_calls":3,
                "request_max_model_calls":6,"request_max_tokens":65536,"model_gate_shared":True,
                "run_status":"FAILED" if requested else None,"tool_invocations":[],
                "trajectory":[{"type":"error","data":{"error_code":"QUERY_TOOL_NOT_ALLOWED"}}] if requested else []})
        return httpx.Response(400,json={"error":{"code":"QUERY_TOOL_NOT_ALLOWED","message":"unsafe proposal"}})
    with httpx.Client(base_url="http://fixture",transport=httpx.MockTransport(handle)) as client:
        collector=HttpQueryCollector(client,{"snapshot":snapshot},EvaluationBudget(10,250000))
        result=collector({"id":"write-without-page","message":"mark this"},[])
    failed=result["turns"][0]
    assert failed["failed"] and failed["error_code"]=="QUERY_TOOL_NOT_ALLOWED"
    assert failed["result"]["write_event_delta"]==0 and failed["run_status"]=="FAILED"
    assert failed["trajectory"][0]["type"]=="error" and failed["tool_trace"]==[]


def test_http_200_does_not_hide_a_failed_persisted_query_run():
    snapshot = {"corpus_revision": 1, "indexed_revision": 1, "task_annotation_revision": 1,
                "as_of": "2026-10-05"}
    posts = []
    def handle(request):
        if request.url.path == "/api/evaluation/context":
            requested = request.url.params.get("request_id")
            return httpx.Response(200, json={"evaluation_host": True, "user_id": "eval-status",
                "snapshot": snapshot, "state_count": 0, "event_count": 0, "conversation_count": 0,
                "states": [], "write_events": [], "model_calls": [], "model_gate_shared": True,
                "query_max_model_calls": 3, "query_max_tokens": 65536, "query_planning_max_calls": 3,
                "request_max_model_calls": 6, "request_max_tokens": 65536,
                "run_status": "FAILED" if requested else None})
        posts.append(request)
        return httpx.Response(200, json={"data": [], "meta": {"conversation_id": "case",
            "conversation_version": 1, "planning": {"spec": {"action": "SEARCH"}}}})
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handle)) as client:
        collector = HttpQueryCollector(client, {"snapshot": snapshot}, EvaluationBudget(10, 250000))
        result = collector({"id": "case", "turns": [{"message": "first"}, {"message": "next"}]}, [])
    assert result["failed"] and len(posts) == 1
    assert result["turns"][0]["error_code"] == "QUERY_RUN_NOT_SUCCEEDED"
    assert result["turns"][1]["error_code"] == "PREVIOUS_TURN_FAILED"


def resume_client(context_overrides=None):
    snapshot = {"corpus_revision": 1, "indexed_revision": 1, "task_annotation_revision": 1,
                "as_of": "2026-10-05"}
    context = {"evaluation_host": True, "user_id": "eval-resume", "snapshot": snapshot,
               "state_count": 0, "event_count": 0, "conversation_count": 2, "model_gate_shared": True,
               "query_planning_max_calls": 4, "request_max_model_calls": 6, "request_max_tokens": 96000}
    context.update(context_overrides or {})
    def handle(request):
        assert request.method == "GET" and request.url.path == "/api/evaluation/context"
        return httpx.Response(200, json=context)
    return httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handle)), {"snapshot": snapshot}


def test_default_http_collector_still_rejects_existing_conversations():
    client, manifest = resume_client()
    with client, pytest.raises(GoldValidationError, match="FRESH_EVALUATION_ACTOR_REQUIRED"):
        HttpQueryCollector(client, manifest, EvaluationBudget(10, 250000))


def test_explicit_checkpoint_allows_exact_actor_and_conversation_count_with_no_review_state():
    client, manifest = resume_client()
    with client:
        collector = HttpQueryCollector(client, manifest, EvaluationBudget(10, 250000),
            resume_checkpoint={"actor": "eval-resume", "conversation_count": 2})
        assert collector.context["conversation_count"] == 2
        assert collector.context["state_count"] == collector.context["event_count"] == 0


@pytest.mark.parametrize("checkpoint,error", [
    ({"actor": "eval-other", "conversation_count": 2}, "RESUME_ACTOR_MISMATCH"),
    ({"actor": "eval-resume", "conversation_count": 1}, "RESUME_CONVERSATION_COUNT_MISMATCH"),
    ({"actor": "eval-resume", "conversation_count": 3}, "RESUME_CONVERSATION_COUNT_MISMATCH"),
    ({"actor": "eval-resume"}, "INVALID_RESUME_CHECKPOINT"),
    ({"actor": "eval-resume", "conversation_count": -1}, "INVALID_RESUME_CHECKPOINT"),
    ({"actor": "eval-resume", "conversation_count": None}, "INVALID_RESUME_CHECKPOINT"),
    ({"actor": "eval-resume", "conversation_count": True}, "INVALID_RESUME_CHECKPOINT"),
    ({"actor": "eval-resume", "conversation_count": "2"}, "INVALID_RESUME_CHECKPOINT"),
])
def test_resume_cannot_adopt_another_actor_or_an_unknown_or_wrong_count(checkpoint, error):
    client, manifest = resume_client()
    with client, pytest.raises(GoldValidationError, match=error):
        HttpQueryCollector(client, manifest, EvaluationBudget(10, 250000), resume_checkpoint=checkpoint)


@pytest.mark.parametrize("field,value,error", [
    ("conversation_count", None, "RESUME_CONTEXT_COUNTS_INVALID"),
    ("conversation_count", -1, "RESUME_CONTEXT_COUNTS_INVALID"),
    ("conversation_count", 2.0, "RESUME_CONTEXT_COUNTS_INVALID"),
    ("state_count", None, "RESUME_CONTEXT_COUNTS_INVALID"),
    ("state_count", -1, "RESUME_CONTEXT_COUNTS_INVALID"),
    ("event_count", None, "RESUME_CONTEXT_COUNTS_INVALID"),
    ("event_count", -1, "RESUME_CONTEXT_COUNTS_INVALID"),
    ("event_count", False, "RESUME_CONTEXT_COUNTS_INVALID"),
    ("state_count", 1, "RESUME_REVIEW_STATE_NOT_EMPTY"),
    ("event_count", 1, "RESUME_REVIEW_STATE_NOT_EMPTY"),
])
def test_resume_refuses_dirty_or_unmeasured_service_state(field, value, error):
    client, manifest = resume_client({field: value})
    with client, pytest.raises(GoldValidationError, match=error):
        HttpQueryCollector(client, manifest, EvaluationBudget(10, 250000),
            resume_checkpoint={"actor": "eval-resume", "conversation_count": 2})


@pytest.mark.parametrize("field", ["state_count", "event_count", "conversation_count"])
@pytest.mark.parametrize("value", [None, -1, 1, False, 0.0, "0"])
def test_fresh_actor_requires_three_explicit_integer_zero_counts(field, value):
    client, manifest = resume_client({"conversation_count": 0, field: value})
    with client, pytest.raises(GoldValidationError, match="FRESH_EVALUATION_ACTOR_REQUIRED"):
        HttpQueryCollector(client, manifest, EvaluationBudget(10, 250000))


@pytest.mark.parametrize("status,unknown", [
    ("FAILED", True), ("CANCELLED", True), ("SKIPPED", True), ("SKIPPED_BUDGET", True),
    ("INTERRUPTED", True), ("RUNNING", True), ("UNKNOWN", True), ("QUEUED", True), (None, True),
])
@pytest.mark.parametrize("has_observed_call", [False, True])
def test_failed_or_incomplete_http_receipt_cannot_prove_call_ledger_complete(status, unknown, has_observed_call):
    snapshot = {"corpus_revision": 1, "indexed_revision": 1, "task_annotation_revision": 1,
                "as_of": "2026-10-05"}
    posts = []
    recorded_call = {"id": "paid-call", "operation_type": "QUERY_PLAN", "input_tokens": 7, "output_tokens": 5}
    def handle(request):
        if request.url.path == "/api/evaluation/context":
            requested = request.url.params.get("request_id")
            return httpx.Response(200, json={"evaluation_host": True, "user_id": "eval-exact-status", "snapshot": snapshot,
                "state_count": 0, "event_count": 0, "conversation_count": 0, "states": [], "write_events": [],
                "model_gate_shared": True, "query_max_model_calls": 4, "query_max_tokens": 96000, "query_planning_max_calls": 4,
                "request_max_model_calls": 6, "request_max_tokens": 96000,
                "run_status": status if requested else None,
                "model_calls": [recorded_call] if requested and has_observed_call else []})
        posts.append(request)
        return httpx.Response(200, json={"data": [], "meta": {"conversation_id": "conversation",
            "conversation_version": 1, "planning": {"spec": {"action": "SEARCH"}}}})
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handle)) as client:
        budget = EvaluationBudget(10, 250000)
        collector = HttpQueryCollector(client, {"snapshot": snapshot}, budget)
        calls = []
        result = collector({"id": "case", "turns": [{"message": "first"}, {"message": "next"}]}, calls)
    assert result["failed"] and len(posts) == 1
    assert result["turns"][0]["error_code"] == ("QUERY_STILL_RUNNING" if status == "RUNNING" else "QUERY_RUN_NOT_SUCCEEDED")
    assert result["turns"][1]["error_code"] == "PREVIOUS_TURN_FAILED"
    assert bool(result["telemetry_unavailable"]) is unknown
    assert calls == ([recorded_call] if has_observed_call else [])
    assert budget.used_calls == (6 if unknown else int(has_observed_call))
    assert budget.used_tokens == (96000 if unknown else 12 * int(has_observed_call))
