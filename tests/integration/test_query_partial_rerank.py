"""Partial candidate schema failures cannot masquerade as a verified empty set."""
import pytest
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.domain.models import AgentConversation, AgentTurn, ToolInvocation
from test_conversation_quality import app_fixture


@pytest.mark.parametrize("has_results,invalid_count,status,verification,executed,expected", [
    (True, 2, "COMPLETED_PARTIAL", "PARTIAL", "HYBRID_RERANK", "VERIFIED"),
    (False, 2, "COMPLETED_PARTIAL", "PARTIAL", "HYBRID_RERANK", "UNAVAILABLE"),
    (False, 0, "COMPLETED", "COMPLETE", "HYBRID_RERANK", "VERIFIED"),
    (True, 2, "FAILED", "PARTIAL", "HYBRID", "UNAVAILABLE"),
    (True, 0, "COMPLETED_PARTIAL", "PARTIAL", "HYBRID_RERANK", "UNAVAILABLE"),
    (True, 2, "COMPLETED", "PARTIAL", "HYBRID_RERANK", "UNAVAILABLE"),
])
def test_partial_schema_delivery_and_empty_verification(tmp_path, has_results, invalid_count, status,
                                                        verification, executed, expected):
    class Retriever:
        calls = 0

        def retrieve(self, query, eligible, pipeline, top_k):
            self.calls += 1
            return {"data": [{"canonical_question_id": eligible[0]}] if has_results else [],
                    "meta": {"pipeline": executed, "rerank_status": status,
                             "candidate_verification_status": verification, "invalid_candidate_count": invalid_count}}

    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="SEARCH", search_query="系统设计")

    retriever = Retriever()
    client, db, _, _ = app_fixture(tmp_path, Planner(), retriever)
    body = {"message": "找系统设计相关题", "request_id": "partial-schema-result"}
    response = client.post("/api/query", json=body)
    if expected == "UNAVAILABLE":
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "QUERY_RELEVANCE_UNAVAILABLE"
        assert response.json()["error"]["retryable"] is True
        with db.session() as session:
            receipt = session.scalar(select(AgentTurn).where(AgentTurn.request_id == body["request_id"]))
            assert receipt.status == "FAILED"
            assert receipt.error_code == "QUERY_RELEVANCE_UNAVAILABLE"
            facts = receipt.response["facts"]
            result = {**facts, "meta": {**facts["meta"], "answer": receipt.response["answer"]},
                      "warnings": receipt.response["warnings"]}
    else:
        assert response.status_code == 200, response.text
        result = response.json()
    assert result["meta"]["relevance_status"] == expected
    assert len(result["data"]) == int(has_results and expected == "VERIFIED")
    assert result["meta"]["invalid_candidate_count"] == invalid_count
    assert result["meta"]["candidate_verification_status"] == verification
    warnings = result["warnings"]
    if status == "COMPLETED_PARTIAL" and invalid_count:
        assert any("2 个结构异常候选" in warning and "可能不完整" in warning for warning in warnings)
    if expected == "UNAVAILABLE":
        assert "核验未完成" in result["meta"]["answer"]
    elif not has_results:
        assert not warnings
    repeated = client.post("/api/query", json=body)
    if expected == "UNAVAILABLE":
        assert repeated.status_code == 503
        assert repeated.json()["error"]["code"] == "QUERY_RELEVANCE_UNAVAILABLE"
    else:
        assert repeated.json() == result
    assert retriever.calls == 1


def test_unavailable_verification_persists_failure_and_partial_audit_without_advancing_conversation(tmp_path):
    audit = {"candidates": [{"canonical_question_id": "diagnostic-candidate",
                             "decision": "VERIFICATION_UNAVAILABLE"}]}

    class Retriever:
        def retrieve(self, query, eligible, pipeline, top_k):
            return {"data": [{"canonical_question_id": eligible[0]}],
                    "meta": {"pipeline": pipeline, "rerank_status": "SKIPPED_BUDGET",
                             "candidate_verification_status": "UNAVAILABLE", "rerank_audit": audit}}

    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="SEARCH", search_query="系统设计")

    client, db, _, service = app_fixture(tmp_path, Planner(), Retriever())
    response = client.post("/api/query", json={"message": "找系统设计相关题",
                                               "request_id": "unavailable-persisted"})
    assert response.status_code == 503, response.text
    with db.session() as session:
        turn = session.scalar(select(AgentTurn).where(AgentTurn.request_id == "unavailable-persisted"))
        assert turn.status == "FAILED"
        assert turn.error_code == "QUERY_RELEVANCE_UNAVAILABLE"
        assert turn.finished_at is not None
        assert turn.response["facts"]["data"] == []
        assert turn.response["facts"]["meta"]["relevance_status"] == "UNAVAILABLE"
        assert turn.response["facts"]["meta"]["rerank_audit"] == audit
        assert session.get(AgentConversation, turn.conversation_id).version == 0
        invocation = session.scalar(select(ToolInvocation).where(ToolInvocation.run_id == turn.id))
        assert invocation.status == "COMPLETED"
        assert invocation.result["facts"]["meta"]["rerank_audit"] == audit
        run_id = turn.id
    status = client.get(f"/api/runs/{run_id}").json()["data"]
    assert status["status"] == "FAILED"
    assert status["error_code"] == "QUERY_RELEVANCE_UNAVAILABLE"
    assert status["result"] is None
    assert status["partial_result"]["facts"]["meta"]["rerank_audit"] == audit
    assert status["events"][-1]["type"] == "failed"
    assert status["events"][-1]["data"]["partial"] is True
    assert not any(event["type"] == "completed" for event in status["events"])
    assert run_id not in service.runs


def test_finish_rejects_unavailable_result_before_creating_success_receipt(tmp_path):
    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="SEARCH", search_query="系统设计")

    class Retriever:
        def retrieve(self, query, eligible, pipeline, top_k):
            return {"data": [], "meta": {"pipeline": pipeline, "rerank_status": "FAILED"}}

    _, db, _, service = app_fixture(tmp_path, Planner(), Retriever())
    run = service.begin(QueryRequest(message="找系统设计相关题", request_id="finish-unavailable"))
    result = service.execute(run, "search_questions", QuerySpec(action="SEARCH", search_query="系统设计"))
    assert result["facts"]["meta"]["relevance_status"] == "UNAVAILABLE"
    with pytest.raises(ValueError, match="QUERY_RELEVANCE_UNAVAILABLE") as failure:
        service.finish(run, provider="fixture")
    with db.session() as session:
        turn = session.get(AgentTurn, run.id)
        assert turn.status == "RUNNING"
        assert turn.response["facts"]["meta"]["relevance_status"] == "UNAVAILABLE"
        assert session.get(AgentConversation, run.conversation_id).version == 0
    service.fail(run, failure.value)
    with db.session() as session:
        assert session.get(AgentTurn, run.id).status == "FAILED"
