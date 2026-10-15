from fastapi.testclient import TestClient
from types import SimpleNamespace
import httpx
from openai import APIError
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import AgentTurn, CorpusState
from interview_intelligence.search.elasticsearch import ElasticsearchRetriever
from interview_intelligence.tenant_retriever import PersonalReranker
from test_analytics import seed_corpus


class Response:
    def __init__(self, data): self.data = data
    def raise_for_status(self): pass
    def json(self): return self.data


class FakeClient:
    def post(self, path, **kwargs):
        assert path.endswith("/_pit")
        return Response({"id": "fixture-pit"})
    def delete(self, path, **kwargs): return Response({"succeeded": True})


class Encoder:
    dimension, version = 2, "fixture:2"
    def embed(self, text): return [0.5, 0.5]


class Client:
    def __init__(self):
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
    def create(self, **kwargs):
        self.requests.append(kwargs)
        raise APIError("injected transient stream failure", request=httpx.Request("POST", "https://fixture.invalid/v1"), body=None)


def test_clarified_business_query_finishes_with_explicit_failure_without_replanning(tmp_path):
    database, first_id, second_id = seed_corpus()
    with database.session() as session, session.begin():
        session.get(CorpusState, 1).current_revision = 1
        session.get(CorpusState, 1).indexed_revision = 1

    class SearchClient(FakeClient):
        def post(self, path, **kwargs):
            if path == "/_search":
                return Response({"hits": {"hits": [{"_id": first_id, "_score": 1.0,
                    "_source": {"canonical_text": "如何设计缓存服务？"}}, {"_id": second_id, "_score": 0.9,
                    "_source": {"canonical_text": "如何设计排行榜服务？"}}]}})
            return super().post(path, **kwargs)

    class Planner:
        calls = 0
        def plan(self, run, context):
            self.calls += 1
            if context["message"] == "场景设计题":
                return QuerySpec(action="CLARIFY", clarification="选择方向", clarification_options=["业务系统设计"])
            assert context["session"]["pending_clarification"]["original_message"] == "场景设计题"
            return QuerySpec(action="SEARCH", search_query="业务系统设计", relevance_query="独立业务系统设计题")

    model, planner = Client(), Planner()
    retriever = ElasticsearchRetriever("http://unused", Encoder(), client=SearchClient(),
        reranker=PersonalReranker(client=model, model="fixture"))
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", snapshot_root=tmp_path,
                        query_router_enabled=True, model_lock_path=tmp_path/"lock")
    client = TestClient(create_app(database, settings, retriever, query_planner=planner))
    first = client.post("/api/query", json={"message": "场景设计题", "request_id": "design-clarify"}).json()
    body = {"message": "业务系统设计", "request_id": "design-choice", "expected_version": 1,
            "conversation_id": first["meta"]["conversation_id"]}
    response = client.post("/api/query", json=body)
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "QUERY_RELEVANCE_UNAVAILABLE"
    with database.session() as session:
        receipt = session.scalar(select(AgentTurn).where(AgentTurn.request_id == "design-choice"))
        run_id = receipt.id
        assert receipt.status == "FAILED"
    view = client.get("/api/runs/"+run_id).json()["data"]
    partial = view["partial_result"]
    assert partial["facts"]["data"] == [] and partial["facts"]["meta"]["relevance_status"] == "UNAVAILABLE"
    assert partial["facts"]["meta"]["rerank_status"] == "FAILED"
    assert partial["facts"]["meta"]["rerank_audit"]["verification_failure"]["transport_retry_count"] == 1
    assert len(model.requests) == 2 and planner.calls == 2
    assert view["status"] == "FAILED" and view["result"] is None
    assert "核验未完成" in partial["answer"]
    assert view["events"][-1]["type"] == "failed"
    conversation = client.get("/api/conversations/"+body["conversation_id"]).json()["data"]
    assert "pending_clarification" in conversation["state"]
    assert conversation["version"] == 1
    repeated = client.post("/api/query", json=body)
    assert repeated.status_code == 503
    assert repeated.json()["error"]["code"] == "QUERY_RELEVANCE_UNAVAILABLE"
    assert len(model.requests) == 2 and planner.calls == 2
