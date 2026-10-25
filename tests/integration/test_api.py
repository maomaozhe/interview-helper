from fastapi.testclient import TestClient

from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from test_analytics import seed_corpus
from interview_intelligence.domain.models import CorpusState
from interview_intelligence.domain.models import SourceRevision
from sqlalchemy import select
import hashlib
import httpx
import pytest
from openai import APITimeoutError, APIConnectionError, APIStatusError


def test_stats_detail_and_review_api_share_one_database():
    db, fast_id, _ = seed_corpus()
    app = create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:"))
    client = TestClient(app)
    stats = client.get("/api/questions/stats", params={"company": "字节"})
    assert stats.status_code == 200
    assert stats.json()["meta"]["request_id"]
    sources = client.get(stats.json()["data"][0]["sources_url"])
    assert sources.status_code == 200
    assert sources.json()["meta"]["pagination"]["total"] >= 1
    detail = client.get(f"/api/questions/{fast_id}", params={"company": "字节"})
    assert detail.status_code == 200
    assert detail.json()["data"]["occurrence_count"] == 1
    recorded = client.post("/api/review", json={"idempotency_key": "api-test-1", "items": [
        {"canonical_question_id": fast_id, "status": "WEAK", "score": 2}
    ]})
    assert recorded.status_code == 201
    assert client.post("/api/review", json={"idempotency_key": "api-test-1", "items": [
        {"canonical_question_id": fast_id, "status": "WEAK", "score": 2}
    ]}).json()["data"] == recorded.json()["data"]
    conflict = client.post("/api/review", headers={"x-request-id": "review-conflict"},
                           json={"idempotency_key": "api-test-1", "items": [
                               {"canonical_question_id": fast_id, "status": "MASTERED", "score": 5}]})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert conflict.json()["error"]["retryable"] is False
    assert conflict.json()["request_id"] == "review-conflict"
    state = client.get("/api/review/state", params={"canonical_question_ids": fast_id})
    assert state.json()["data"]["states"][fast_id]["status"] == "WEAK"


def test_invalid_filter_is_structured_error():
    db, _, _ = seed_corpus()
    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:")))
    response = client.get("/api/questions/stats", params={"round": "FIFTH"},
                          headers={"x-request-id": "invalid-filter"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["retryable"] is False
    assert response.json()["request_id"] == "invalid-filter"
    errors = response.json()["error"]["details"]["errors"]
    assert any(error["loc"] == ["query", "round"] and error["input"] == "FIFTH" for error in errors)


def test_missing_question_is_a_traceable_not_found_response():
    db, _, _ = seed_corpus()
    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:")))
    response = client.get("/api/questions/not-a-question", headers={"x-request-id": "missing-question"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.json()["error"]["retryable"] is False
    assert response.json()["request_id"] == "missing-question"


def test_topics_are_available_from_installed_app():
    db, _, _ = seed_corpus()
    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:")))
    response = client.get("/api/topics")
    assert response.status_code == 200
    assert response.json()["data"]["taxonomy_version"] == "v1"


def test_reverse_proxy_prefix_is_preserved_in_source_links():
    db, fast_id, _ = seed_corpus()
    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:",
                                                api_root_path="/interview")))
    stats = client.get("/api/questions/stats")
    assert stats.status_code == 200
    assert stats.json()["data"][0]["sources_url"].startswith("/interview/api/occurrences")
    detail = client.get(f"/api/questions/{fast_id}")
    assert detail.json()["data"]["sources"][0]["source_api_url"].startswith("/interview/api/sources/")


def test_interactive_search_explicitly_reports_dense_degradation():
    db, fast_id, _ = seed_corpus()
    with db.session() as session:
        with session.begin():
            session.get(CorpusState, 1).current_revision = 1
            session.get(CorpusState, 1).indexed_revision = 1

    class Retriever:
        def retrieve(self, query, eligible_ids, pipeline, top_k):
            if pipeline == "HYBRID":
                raise ValueError("EMBEDDING_NOT_READY")
            return {"data": [{"canonical_question_id": fast_id, "score": 1.0}],
                    "meta": {"pipeline": pipeline}}

    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:"), Retriever()))
    response = client.get("/api/questions/search", params={"query": "Redis", "company": "字节", "pipeline": "HYBRID"})
    assert response.status_code == 200
    assert response.json()["meta"]["requested_pipeline"] == "HYBRID"
    assert response.json()["meta"]["executed_pipeline"] == "BM25"
    assert response.json()["meta"]["degraded"] is True
    assert response.json()["meta"]["result_completeness"]["corpus_complete"] is False
    assert any("不代表题库全部匹配题" in warning for warning in response.json()["warnings"])


@pytest.mark.parametrize("has_results", [True, False])
def test_legacy_search_surfaces_partial_evidence_verification(has_results):
    db, fast_id, _ = seed_corpus()
    with db.session() as session, session.begin():
        session.get(CorpusState, 1).current_revision = 1
        session.get(CorpusState, 1).indexed_revision = 1

    class Retriever:
        def retrieve(self, query, eligible_ids, pipeline, top_k):
            return {"data": [{"canonical_question_id": fast_id}] if has_results else [], "meta": {
                "pipeline": pipeline, "rerank_status": "COMPLETED_PARTIAL",
                "candidate_verification_status": "PARTIAL", "candidate_count": 50,
                "invalid_candidate_count": 1, "invalid_evidence_count": 1, "invalid_schema_count": 0}}

    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:"), Retriever()))
    response = client.get("/api/questions/search", params={"query": "Redis", "pipeline": "HYBRID_RERANK"})
    if not has_results:
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "QUERY_RELEVANCE_UNAVAILABLE"
        return
    assert response.status_code == 200, response.text
    result = response.json()
    completeness = result["meta"]["result_completeness"]
    assert completeness["verification_complete"] is False
    assert completeness["corpus_complete"] is False
    assert completeness["invalid_evidence_count"] == 1
    assert any("1 个证据异常候选" in warning and "可能不完整" in warning for warning in result["warnings"])


def test_source_snapshot_survives_a_host_path_change_and_normalizes_lines(tmp_path):
    db, _, _ = seed_corpus()
    raw = b"# Interview\r\nRedis question\r\n"
    digest = hashlib.sha256(raw).hexdigest()
    (tmp_path / f"{digest}.md").write_bytes(raw)
    with db.session() as session:
        with session.begin():
            revision = session.scalar(select(SourceRevision))
            revision.raw_file_hash = digest
            revision.snapshot_path = r"Z:\old-host\snapshots\unavailable.md"
        revision_id = revision.id
    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:",
                                                snapshot_root=tmp_path)))
    response = client.get(f"/api/sources/{revision_id}", params={"line_start": 2, "line_end": 2})
    assert response.status_code == 200
    assert response.json()["data"]["markdown"] == "Redis question"
    (tmp_path / f"{digest}.md").write_bytes(b"changed")
    response = client.get(f"/api/sources/{revision_id}", headers={"x-request-id": "changed-snapshot"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SOURCE_HASH_MISMATCH"
    assert response.json()["error"]["retryable"] is False
    assert response.json()["request_id"] == "changed-snapshot"


def test_missing_snapshot_is_a_structured_unavailable_response(tmp_path):
    db, _, _ = seed_corpus()
    with db.session() as session:
        revision_id = session.scalar(select(SourceRevision.id))
    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:",
                                                snapshot_root=tmp_path)))
    response = client.get(f"/api/sources/{revision_id}", headers={"x-request-id": "missing-snapshot"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SOURCE_SNAPSHOT_MISSING"
    assert response.json()["error"]["retryable"] is True
    assert response.json()["request_id"] == "missing-snapshot"


@pytest.mark.parametrize("error_type,code", [(APITimeoutError, "MODEL_PROVIDER_TIMEOUT"),
                                            (APIConnectionError, "MODEL_PROVIDER_UNAVAILABLE"),
                                            (httpx.ReadTimeout, "MODEL_PROVIDER_TIMEOUT"),
                                            (httpx.ConnectError, "MODEL_PROVIDER_UNAVAILABLE")])
def test_chat_provider_failure_is_retryable_json_with_request_id(error_type, code):
    db, _, _ = seed_corpus()
    with db.session() as session, session.begin():
        session.get(CorpusState, 1).current_revision = 1
        session.get(CorpusState, 1).indexed_revision = 1
    class Retriever:
        def retrieve(self, *args):
            args = ("provider echoed secret",) if issubclass(error_type, httpx.HTTPError) else ()
            raise error_type(*args, request=httpx.Request("POST", "https://model.example/v1"))
    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:"), Retriever()),
                        raise_server_exceptions=False)
    response = client.post("/api/agent/chat", json={"message": "内存泄漏的类似问法"},
                           headers={"x-request-id": "provider-failure-test"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["retryable"] is True
    assert response.json()["request_id"] == "provider-failure-test"
    assert "model.example" not in response.text
    assert "provider echoed secret" not in response.text


@pytest.mark.parametrize("upstream_status,retryable", [(401, False), (429, True), (500, True)])
def test_chat_provider_status_failure_controls_retry_without_exposing_upstream(upstream_status, retryable):
    db, _, _ = seed_corpus()
    with db.session() as session, session.begin():
        session.get(CorpusState, 1).current_revision = 1
        session.get(CorpusState, 1).indexed_revision = 1

    class Retriever:
        def retrieve(self, *args):
            request = httpx.Request("POST", "https://model.example/v1")
            raise APIStatusError("provider echoed secret", response=httpx.Response(upstream_status, request=request),
                                 body={"api_key": "private-provider-secret"})

    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:"), Retriever()),
                        raise_server_exceptions=False)
    response = client.post("/api/agent/chat", json={"message": "内存泄漏的类似问法"},
                           headers={"x-request-id": "upstream-status"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_PROVIDER_UNAVAILABLE"
    assert response.json()["error"]["retryable"] is retryable
    assert response.json()["request_id"] == "upstream-status"
    assert "secret" not in response.text and "model.example" not in response.text
