from datetime import datetime, timedelta, timezone
import importlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.analytics.usage import UsageRequest, question_usage, record_usage
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import (
    AgentTurn, CanonicalQuestion, CorpusState, QuestionUsageEvent, QuestionUsageRun,
    UsageTrackingState, UserQuestionState,
)
from interview_intelligence.review.service import ReviewService
from interview_intelligence.contracts import ReviewRequest
from test_analytics import seed_corpus


def ready_corpus():
    db, fast, persistence = seed_corpus()
    with db.session() as session, session.begin():
        state = session.get(CorpusState, 1)
        state.current_revision = state.indexed_revision = 1
    return db, fast, persistence


class Retriever:
    def __init__(self, fast, persistence, *, status="COMPLETED", audit=True):
        self.fast, self.persistence, self.status, self.audit = fast, persistence, status, audit
        self.calls = 0

    def retrieve(self, query, eligible, pipeline, top_k):
        self.calls += 1
        meta = {"pipeline": pipeline, "rerank_status": self.status,
                "embedding_version": "fixture-v1", "candidate_count": 2}
        if self.audit:
            meta.update(candidate_ids=[self.fast, self.persistence], rerank_audit={"candidates": [
                {"canonical_question_id": self.fast, "decision": "RETURNED", "relevance_grade": 3, "quote_grounded": True},
                {"canonical_question_id": self.persistence, "decision": "PAGE_CUTOFF", "relevance_grade": 2, "quote_grounded": True}]})
        return {"data": [{"canonical_question_id": self.fast, "score": 1}], "meta": meta}


class Planner:
    def plan(self, run, context):
        return QuerySpec(action="SEARCH", search_query="Redis", pipeline="HYBRID_RERANK", page_size=1)


def client_for(db, retriever=None, user="local", tenant=False):
    return TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:",
        local_user_id=user, multi_tenant_enabled=tenant), retriever=retriever, query_planner=Planner()))


def read_usage(db, **kwargs):
    with db.session() as session:
        return question_usage(session, UsageRequest(**kwargs))


def by_id(result):
    return {item["canonical_question_id"]: item for item in result["data"]}


def test_replay_requery_and_review_have_separate_counts():
    db, fast, persistence = ready_corpus()
    retriever = Retriever(fast, persistence)
    client = client_for(db, retriever)
    body = {"message": "Redis问题", "request_id": "usage-first"}
    first = client.post("/api/query", json=body)
    assert first.status_code == 200, first.text
    assert client.post("/api/query", json=body).json() == first.json()
    assert retriever.calls == 1
    rows = by_id(client.get("/api/questions/usage").json())
    assert (rows[fast]["candidate_count"], rows[fast]["accepted_count"], rows[fast]["returned_count"]) == (1, 1, 1)
    assert (rows[persistence]["candidate_count"], rows[persistence]["accepted_count"], rows[persistence]["returned_count"]) == (1, 1, 0)
    assert rows[fast]["review_count"] == 0
    again = client.post("/api/query", json={**body, "request_id": "usage-again",
        "requery_of_run_id": first.json()["meta"]["run_id"]})
    assert again.status_code == 200, again.text
    listed = client.post("/api/questions/list", json={"list_request": {}, "request_id": "usage-list"})
    assert listed.status_code == 200, listed.text
    assert client.post("/api/questions/list", json={"list_request": {}, "request_id": "usage-list"}).status_code == 200
    rows = by_id(read_usage(db))
    assert (rows[fast]["returned_count"], rows[fast]["listed_count"], rows[fast]["review_count"]) == (2, 1, 0)
    assert rows[persistence]["listed_count"] == 1
    assert rows[persistence]["usage_hint"] == "CANDIDATE_NOT_RETURNED"
    recorded = client.post("/api/review", json={"idempotency_key": "usage-review",
        "items": [{"canonical_question_id": fast, "status": "WEAK"}]})
    assert recorded.status_code == 201
    row = by_id(read_usage(db))[fast]
    assert row["review_count"] == 1 and row["last_reviewed_at"]
    detail = client.get(f"/api/questions/{fast}").json()["data"]
    assert detail["usage"]["returned_count"] == 2
    assert client.get("/api/review/state", params={"canonical_question_ids": fast}).json()["data"]["states"][fast]["last_reviewed_at"]


def test_evaluation_and_usage_reads_do_not_pollute_personal_coverage():
    db, fast, persistence = ready_corpus()
    client = client_for(db, Retriever(fast, persistence))
    response = client.post("/api/query", json={"message": "评测", "request_id": "eval", "usage_purpose": "evaluation"})
    assert response.status_code == 200, response.text
    assert client.get("/api/questions/list", headers={"x-usage-purpose": "evaluation"}).status_code == 200
    for _ in range(2):
        result = client.get("/api/questions/usage").json()
        assert result["meta"]["summary"]["not_returned_questions"] == 2
        assert result["meta"]["observation"]["completed_runs"] == 0
        assert all(row["listed_count"] == 0 for row in result["data"])
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(QuestionUsageRun)) == 2
    # A purpose change under the same idempotency key must never replay/overwrite.
    conflict = client.post("/api/query", json={"message": "评测", "request_id": "eval"})
    assert conflict.status_code == 409


def test_evaluation_header_marks_sync_and_async_receipts_and_rejects_conflicts():
    import time
    db, fast, persistence = ready_corpus()
    with client_for(db, Retriever(fast, persistence)) as client:
        headers = {"X-Usage-Purpose": "evaluation"}
        listed = client.post("/api/questions/list", headers=headers,
            json={"list_request": {}, "request_id": "header-list"})
        assert listed.status_code == 200, listed.text
        conversation = client.post("/api/conversations").json()["data"]["conversation_id"]
        accepted = client.post(f"/api/conversations/{conversation}/messages", headers=headers,
            json={"message": "Redis", "request_id": "header-async"})
        assert accepted.status_code == 202, accepted.text
        run_id = accepted.json()["data"]["run_id"]
        for _ in range(50):
            view = client.get(f"/api/runs/{run_id}").json()["data"]
            if view["status"] == "SUCCEEDED":
                break
            time.sleep(.01)
        assert view["status"] == "SUCCEEDED", view
        assert read_usage(db)["meta"]["observation"]["completed_runs"] == 0
        with db.session() as session:
            assert set(session.scalars(select(QuestionUsageRun.purpose))) == {"evaluation"}
        conflict = client.post("/api/query", headers=headers,
            json={"message": "Redis", "request_id": "conflicting-purpose", "usage_purpose": "practice"})
        assert conflict.status_code == 400


def test_multi_search_tools_count_one_run_and_cancelled_run_counts_nothing():
    db, fast, persistence = ready_corpus()
    service = QueryService(db, Retriever(fast, persistence), signing_key="test")
    run = service.begin(QueryRequest(message="Redis", request_id="multi"))
    for final in (False, True):
        service.execute(run, "search_questions", QuerySpec(action="SEARCH", search_query="Redis", page_size=1, final=final))
    service.finish(run, provider="fixture")
    assert by_id(read_usage(db))[fast]["returned_count"] == 1
    failed = service.begin(QueryRequest(message="Redis", request_id="cancelled"))
    service.execute(failed, "search_questions", QuerySpec(action="SEARCH", search_query="Redis", page_size=1))
    service.fail(failed, ValueError("QUERY_CANCELLED"))
    assert by_id(read_usage(db))[fast]["returned_count"] == 1
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(QuestionUsageEvent)) == 2


def test_missing_candidate_audit_is_reported_as_incomplete():
    db, fast, persistence = ready_corpus()
    client = client_for(db, Retriever(fast, persistence, audit=False))
    assert client.post("/api/query", json={"message": "Redis", "request_id": "no-audit"}).status_code == 200
    result = read_usage(db, usage_state="NOT_CANDIDATE")
    assert [row["canonical_question_id"] for row in result["data"]] == [persistence]
    assert result["meta"]["observation"]["complete_candidate_runs"] == 0
    assert len(result["warnings"]) == 2


def test_interrupted_tool_result_is_counted_only_after_resume_finishes():
    db, fast, persistence = ready_corpus()
    retriever = Retriever(fast, persistence)
    service = QueryService(db, retriever, signing_key="test")
    body = QueryRequest(message="Redis", request_id="resume-usage")
    interrupted = service.begin(body)
    service.execute(interrupted, "search_questions", QuerySpec(action="SEARCH", search_query="Redis", page_size=1))
    service.fail(interrupted, RuntimeError("transport interrupted"))
    assert read_usage(db)["meta"]["observation"]["completed_runs"] == 0
    resumed = service.begin(body)
    assert resumed.result and resumed.terminal
    service.finish(resumed, provider="recovered_read")
    assert by_id(read_usage(db))[fast]["returned_count"] == 1
    assert retriever.calls == 1
    assert isinstance(service.begin(body), dict)
    with db.session() as session:
        corpus = session.get(CorpusState, 1)
        assert corpus.current_revision == corpus.indexed_revision == 1


def test_unavailable_relevance_and_partial_invalid_rows_do_not_count_accepted():
    db, fast, persistence = ready_corpus()
    client = client_for(db, Retriever(fast, persistence, status="SKIPPED_BUDGET"))
    response = client.post("/api/query", json={"message": "Redis", "request_id": "unavailable"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUERY_RELEVANCE_UNAVAILABLE"
    with db.session() as session:
        assert session.scalar(select(AgentTurn).where(AgentTurn.request_id == "unavailable")).status == "FAILED"
        assert session.scalar(select(func.count()).select_from(QuestionUsageRun)) == 0
    rows = by_id(read_usage(db))
    assert all(row["candidate_count"] == 0 and row["accepted_count"] == 0 and row["returned_count"] == 0 for row in rows.values())
    part = {"intent": "SEARCH", "facts": {"data": [{"canonical_question_id": fast}], "meta": {
        "candidate_ids": [fast, persistence], "candidate_count": 2, "relevance_status": "VERIFIED",
        "candidate_verification_status": "PARTIAL", "invalid_candidate_count": 1, "rerank_audit": {"candidates": [
            {"canonical_question_id": fast, "decision": "RETURNED", "relevance_grade": 3, "quote_grounded": True},
            {"canonical_question_id": persistence, "decision": "INVALID_EVIDENCE", "relevance_grade": 3, "quote_grounded": False}]}}}}
    with db.session() as session, session.begin():
        record_usage(session, user_id="local", request_key="partial", parts=[part])
    rows = by_id(read_usage(db))
    assert (rows[fast]["candidate_count"], rows[fast]["accepted_count"], rows[fast]["returned_count"]) == (1, 1, 1)
    assert (rows[persistence]["candidate_count"], rows[persistence]["accepted_count"], rows[persistence]["returned_count"]) == (1, 0, 0)


def test_direct_raw_search_tracks_candidate_return_and_listing_separately():
    db, fast, persistence = ready_corpus()
    client = client_for(db, Retriever(fast, persistence, status="NOT_REQUESTED"))
    assert client.get("/api/questions/search", params={"query": "Redis", "pipeline": "BM25"}).status_code == 200
    assert client.get("/api/questions/list").status_code == 200
    row = by_id(read_usage(db))[fast]
    assert (row["candidate_count"], row["accepted_count"], row["returned_count"], row["listed_count"]) == (1, 0, 1, 1)
    assert client.get("/api/questions/search", params={"query": "Redis", "pipeline": "BM25", "usage_purpose": "evaluation"}).status_code == 200
    assert client.get("/api/questions/search", headers={"X-Usage-Purpose": "evaluation"},
        params={"query": "Redis", "pipeline": "BM25"}).status_code == 200
    assert client.get("/api/questions/search", headers={"X-Usage-Purpose": "evaluation"},
        params={"query": "Redis", "pipeline": "BM25", "usage_purpose": "practice"}).status_code == 400
    assert by_id(read_usage(db))[fast]["returned_count"] == 1


def test_user_scope_active_corpus_time_window_and_review_queue():
    db, fast, persistence = ready_corpus()
    end = datetime(2026, 10, 9, 8, tzinfo=timezone.utc)
    with db.session() as session, session.begin():
        session.get(UsageTrackingState, 1).started_at = end - timedelta(days=60)
        # Even with identical request keys, accounts have distinct observations.
        part = {"intent": "SEARCH", "facts": {"data": [{"canonical_question_id": fast}],
            "meta": {"candidate_ids": [fast], "candidate_count": 1}}}
        record_usage(session, user_id="local", request_key="same", parts=[part], occurred_at=end - timedelta(days=20))
        record_usage(session, user_id="other", request_key="same", parts=[part], occurred_at=end - timedelta(days=2))
        session.add(UserQuestionState(user_id="local", canonical_question_id=fast, status="REVIEWED",
            review_count=2, last_reviewed_at=end - timedelta(days=10)))
        session.add(UserQuestionState(user_id="other", canonical_question_id=persistence, status="MASTERED", review_count=4))
    with db.session() as session:
        recent = question_usage(session, UsageRequest(usage_days=7), now=end)
        assert all(row["returned_count"] == 0 for row in recent["data"])
        lifetime = question_usage(session, UsageRequest(usage_state="REVIEW_DUE"), now=end)
        assert {row["canonical_question_id"] for row in lifetime["data"]} == {fast, persistence}
        assert by_id(lifetime)[persistence]["review_count"] == 0
        later = question_usage(session, UsageRequest(usage_state="REVIEW_DUE", review_after_days=14), now=end)
        assert [row["canonical_question_id"] for row in later["data"]] == [persistence]
        scoped = question_usage(session, UsageRequest(company="字节", round="SECOND"), now=end)
        assert [row["canonical_question_id"] for row in scoped["data"]] == [persistence]
        other = question_usage(session, UsageRequest(), user_id="other", now=end)
        assert by_id(other)[fast]["returned_count"] == 1


def test_canonical_redirect_deduplicates_observations_and_empty_corpus_has_zero_summary():
    db, fast, persistence = ready_corpus()
    with db.session() as session, session.begin():
        part = {"intent": "SEARCH", "facts": {"data": [{"canonical_question_id": identity} for identity in (fast, persistence)],
            "meta": {"candidate_ids": [fast, persistence], "candidate_count": 2}}}
        record_usage(session, user_id="local", request_key="merge", parts=[part])
        session.get(CanonicalQuestion, persistence).lifecycle = "REDIRECT"
        session.get(CanonicalQuestion, persistence).redirect_to_id = fast
    result = read_usage(db)
    assert result["meta"]["summary"]["active_questions"] == 1
    assert by_id(result)[fast]["returned_count"] == 1
    assert read_usage(db, company="不存在")["meta"]["summary"]["active_questions"] == 0


def test_migration_creates_usage_tables_without_changing_corpus_or_reviews():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    db, fast, _ = ready_corpus()
    module = importlib.import_module("migrations.versions.f047ac821369_question_usage")
    with db.engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.downgrade()
        module.upgrade()
        module.upgrade()
        assert connection.scalar(select(func.count()).select_from(UsageTrackingState)) == 1
        assert connection.scalar(select(CorpusState.current_revision)) == 1
        assert connection.scalar(select(func.count()).select_from(CanonicalQuestion)) == 2
    assert by_id(read_usage(db))[fast]["returned_count"] == 0


def test_usage_pagination_is_stable_and_validation_rejects_invalid_window():
    db, fast, persistence = ready_corpus()
    client = client_for(db)
    first = client.get("/api/questions/usage", params={"limit": 1}).json()
    second = client.get("/api/questions/usage", params={"limit": 1, "offset": first["meta"]["pagination"]["next_offset"]}).json()
    assert {first["data"][0]["canonical_question_id"], second["data"][0]["canonical_question_id"]} == {fast, persistence}
    for params in ({"usage_days": 0}, {"usage_state": "UNSUPPORTED"}, {"limit": 101}):
        assert client.get("/api/questions/usage", params=params).status_code == 422


def test_tenant_cookie_scope_ignores_browser_user_header():
    db, fast, persistence = ready_corpus()
    with db.session() as session, session.begin():
        record_usage(session, user_id="local", request_key="private", parts=[{"intent": "SEARCH", "facts": {
            "data": [{"canonical_question_id": fast}], "meta": {"candidate_ids": [fast], "candidate_count": 1}}}])
    client = client_for(db, tenant=True)
    assert client.get("/api/questions/usage").status_code == 401
    registered = client.post("/api/account/register", headers={"X-Tenant-Action": "1"}, json={"username": "usage-user", "password": "test-pass-123"})
    assert registered.status_code == 200, registered.text
    response = client.get("/api/questions/usage", headers={"x-user-id": "local", "x-tenant-id": "local"})
    assert response.status_code == 200, response.text
    assert all(row["returned_count"] == 0 for row in response.json()["data"])
