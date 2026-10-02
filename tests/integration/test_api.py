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
from openai import APITimeoutError, APIConnectionError


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
    state = client.get("/api/review/state", params={"canonical_question_ids": fast_id})
    assert state.json()["data"]["states"][fast_id]["status"] == "WEAK"


def test_invalid_filter_is_structured_error():
    db, _, _ = seed_corpus()
    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:")))
    response = client.get("/api/questions/stats", params={"round": "FIFTH"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


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
    response = client.get(f"/api/sources/{revision_id}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SOURCE_HASH_MISMATCH"


def test_missing_snapshot_is_a_structured_unavailable_response(tmp_path):
    db, _, _ = seed_corpus()
    with db.session() as session:
        revision_id = session.scalar(select(SourceRevision.id))
    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:",
                                                snapshot_root=tmp_path)))
    response = client.get(f"/api/sources/{revision_id}")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SOURCE_SNAPSHOT_MISSING"


@pytest.mark.parametrize("error_type,code", [(APITimeoutError, "MODEL_PROVIDER_TIMEOUT"),
                                            (APIConnectionError, "MODEL_PROVIDER_UNAVAILABLE")])
def test_chat_provider_failure_is_retryable_json_with_request_id(error_type, code):
    db, _, _ = seed_corpus()
    with db.session() as session, session.begin():
        session.get(CorpusState, 1).current_revision = 1
        session.get(CorpusState, 1).indexed_revision = 1
    class Retriever:
        def retrieve(self, *args):
            raise error_type(request=httpx.Request("POST", "https://model.example/v1"))
    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:"), Retriever()),
                        raise_server_exceptions=False)
    response = client.post("/api/agent/chat", json={"message": "内存泄漏的类似问法"},
                           headers={"x-request-id": "provider-failure-test"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["retryable"] is True
    assert response.json()["request_id"] == "provider-failure-test"
    assert "model.example" not in response.text
