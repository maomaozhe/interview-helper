"""The Pi HTTP boundary must carry a soft hint into retrieval and durable receipts."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QueryRequest
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    AgentConversation, AgentTurn, CanonicalQuestion, CorpusState,
    QuestionOccurrence, ToolInvocation,
)
from test_analytics import seed_corpus


def test_internal_search_http_preserves_soft_preference_without_reducing_scope(tmp_path):
    database, project_id, design_id = seed_corpus()
    with database.session() as session, session.begin():
        corpus = session.get(CorpusState, 1)
        corpus.current_revision = corpus.indexed_revision = 1
        for identity, kind in ((project_id, "PROJECT"), (design_id, "SYSTEM_DESIGN")):
            session.get(CanonicalQuestion, identity).question_type = kind
            for occurrence in session.scalars(select(QuestionOccurrence).where(
                    QuestionOccurrence.canonical_question_id == identity)):
                occurrence.question_type = kind

    class Retriever:
        supports_question_type_preference = True

        def __init__(self):
            self.calls = []

        def retrieve(self, query, eligible, pipeline, top_k, **options):
            self.calls.append((query, eligible, pipeline, options))
            return {"data": [{"canonical_question_id": identity} for identity in eligible],
                    "meta": {"pipeline": pipeline, "rerank_status": "COMPLETED"}}

    retriever = Retriever()
    settings = Settings(database_url="sqlite+pysqlite:///:memory:",
                        snapshot_root=tmp_path / "snapshots", internal_agent_token="bridge-test")
    app = create_app(database, settings, retriever=retriever)
    service = app.state.query_service
    run = service.begin(QueryRequest(message="工程系统设计题", request_id="soft-preference-http"))
    filters = FilterSpec().model_dump(mode="json")
    # Send wire JSON, rather than constructing QuerySpec or calling execute directly.
    payload = {"action": "SEARCH", "filters": filters, "sort": "frequency", "top_n": None,
               "page_size": 20, "search_query": "如何 设计 一个 系统",
               "relevance_query": "传统后端独立系统设计题", "pipeline": "HYBRID_RERANK",
               "preferred_question_type": "SYSTEM_DESIGN", "final": True}
    with TestClient(app) as client:
        response = client.post(f"/internal/agent/runs/{run.id}/tools/search_questions",
                               headers={"Authorization": "Bearer bridge-test"}, json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(retriever.calls) == 1
    query, eligible, pipeline, options = retriever.calls[0]
    assert query == payload["search_query"] and pipeline == "HYBRID_RERANK"
    assert set(eligible) == {project_id, design_id}
    assert options == {"preferred_question_type": "SYSTEM_DESIGN", "preferred_eligible_ids": [design_id]}
    assert {row["canonical_question_id"] for row in result["facts"]["data"]} == {project_id, design_id}
    assert result["facts"]["meta"]["applied_filters"] == filters
    assert result["planning"]["spec"]["preferred_question_type"] == "SYSTEM_DESIGN"
    assert result["planning"]["spec"]["filters"]["question_type"] is None

    service.finish(run, provider="pi-http-test")
    with database.session() as session:
        invocation = session.scalar(select(ToolInvocation).where(ToolInvocation.run_id == run.id))
        assert invocation.tool_name == "search_questions" and invocation.status == "COMPLETED"
        assert invocation.arguments["preferred_question_type"] == "SYSTEM_DESIGN"
        assert invocation.arguments["filters"] == filters
        assert invocation.result["planning"]["spec"]["preferred_question_type"] == "SYSTEM_DESIGN"
        receipt = session.get(AgentTurn, run.id)
        assert receipt.status == "SUCCEEDED"
        assert receipt.response["planning"]["spec"]["preferred_question_type"] == "SYSTEM_DESIGN"
        assert receipt.state_after["last_plan"]["preferred_question_type"] == "SYSTEM_DESIGN"
        conversation = session.get(AgentConversation, run.conversation_id)
        assert conversation.state["last_plan"]["preferred_question_type"] == "SYSTEM_DESIGN"
        assert conversation.state["filters"]["question_type"] is None
