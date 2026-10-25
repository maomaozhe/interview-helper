"""Public retrieval must keep provider work inside one bounded request."""
from fastapi.testclient import TestClient

from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import CorpusState
from interview_intelligence.providers.runtime import current_limits
from test_analytics import seed_corpus


def search_client(retriever):
    database, canonical_id, _ = seed_corpus()
    with database.session() as session, session.begin():
        state = session.get(CorpusState, 1)
        state.current_revision = state.indexed_revision = 1
    retriever.canonical_id = canonical_id
    return TestClient(create_app(database,
        Settings(database_url="sqlite+pysqlite:///:memory:", query_max_tokens=16000,
                 query_max_total_model_calls=6), retriever))


def test_public_search_establishes_fresh_limits_and_reports_conservative_charges():
    class Retriever:
        observed = []

        def retrieve(self, query, eligible_ids, pipeline, top_k):
            limits = current_limits.get()
            assert limits is not None
            assert limits.calls == limits.tokens == 0
            self.observed.append(limits)
            limits.attempt()
            limits.settle_tokens(limits.reserve_tokens(4096))
            return {"data": [{"canonical_question_id": self.canonical_id}], "meta": {}}

    retriever = Retriever()
    with search_client(retriever) as client:
        for _ in range(2):
            response = client.get("/api/questions/search", params={"query": "Redis", "pipeline": "BM25"},
                headers={"X-Usage-Purpose": "evaluation"})
            assert response.status_code == 200, response.text
            meta = response.json()["meta"]
            assert meta["relevance_status"] == "UNVERIFIED"
            assert meta["request_budget"] == {"max_model_calls": 6, "charged_model_calls": 1,
                "max_tokens": 16000, "charged_tokens": 4096,
                "token_charge_basis": "CONSERVATIVE_RESERVATIONS"}
    assert retriever.observed[0] is not retriever.observed[1]
    assert current_limits.get() is None


def test_public_search_rejects_budget_exhaustion_without_pipeline_fallback():
    class Retriever:
        pipelines = []

        def retrieve(self, query, eligible_ids, pipeline, top_k):
            self.pipelines.append(pipeline)
            limits = current_limits.get()
            for _ in range(7):
                limits.attempt()
            raise AssertionError("budget check must reject the seventh provider admission")

    retriever = Retriever()
    with search_client(retriever) as client:
        response = client.get("/api/questions/search", params={"query": "Redis", "pipeline": "HYBRID_RERANK"})
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "QUERY_MODEL_BUDGET_EXCEEDED"
    assert retriever.pipelines == ["HYBRID_RERANK"]
    assert current_limits.get() is None
