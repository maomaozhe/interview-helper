"""Raw intent evidence stays separate from optional model recall expansions."""
from copy import deepcopy

import pytest

from interview_intelligence.agent.harness import ContextCompiler
from interview_intelligence.agent.query_contract import QuerySpec
from test_conversation_quality import app_fixture


def resolve(context, path):
    value = context
    for key in path.split("."):
        value = value[key]
    return value


@pytest.mark.parametrize("message", ["agent场景设计题", "Agent harness 相关问题",
    "线上服务内存一直涨，如何定位且不影响业务，有哪些真实问法？"])
def test_compiler_projects_user_evidence_and_moves_expansions_without_mutating_state(message):
    state = {"last_plan": {"action": "SEARCH", "relevance_query": "上一轮明确目标",
             "search_query": "召回扩展：架构 记忆 工具 恢复 权限",
             "lexical_facets": ["补充分支一", "补充分支二"]},
             "recent_messages": ["上一轮用户原话", message],
             "pending_clarification": {"original_message": "场景设计题", "question": "准备哪个方向？"}}
    original = deepcopy(state)
    context = ContextCompiler().compile(message=message, today="2026-10-08", explicit_filters={},
        preferences={}, state=state, default_page_size=20, requested_pipeline="HYBRID_RERANK",
        query_feedback=[{"note": "只看应用设计"}])
    contract = context["relevance_intent"]
    assert [resolve(context, path) for path in contract["user_sources"]] == [
        message, "场景设计题", ["上一轮用户原话"]]
    assert resolve(context, contract["previous_target_source"]) == "上一轮明确目标"
    assert contract["previous_target_kind"] == "model_reformulation"
    assert contract["allow_inferred_requirements"] is False
    assert contract["correction_sources"] == ["query_feedback"]
    assert context["retrieval_expansions"]["previous_plan"] == {
        "search_query": original["last_plan"]["search_query"],
        "lexical_facets": original["last_plan"]["lexical_facets"]}
    assert "search_query" not in context["session"]["last_plan"]
    assert "lexical_facets" not in context["session"]["last_plan"]
    assert state == original


@pytest.mark.parametrize("message", ["agent场景设计题", "Agent harness 相关问题",
    "线上服务内存一直涨，如何定位且不影响业务，有哪些真实问法？"])
def test_service_forwards_user_intent_independently_of_model_expansion(tmp_path, message):
    expansion = "Agent 运行框架 上下文维护 记忆管理 工具编排 恢复 权限"
    facets = ["Agent 上下文维护", "Agent 记忆管理"]

    class Retriever:
        supports_relevance_query = supports_lexical_facets = True

        def __init__(self):
            self.calls = []

        def retrieve(self, query, eligible, pipeline, top_k, *, relevance_query=None, lexical_facets=None):
            self.calls.append((query, relevance_query, lexical_facets))
            return {"data": [], "meta": {"pipeline": pipeline, "rerank_status": "COMPLETED"}}

    class Planner:
        def plan(self, run, context):
            target = resolve(context, context["relevance_intent"]["user_sources"][0])
            return QuerySpec(action="SEARCH", search_query=expansion, relevance_query=target,
                             lexical_facets=facets)

    retriever = Retriever()
    client, _, _, _ = app_fixture(tmp_path, Planner(), retriever)
    response = client.post("/api/query", json={"message": message, "request_id": "intent-evidence"})
    assert response.status_code == 200, response.text
    assert retriever.calls == [(expansion, message, facets)]
    assert response.json()["meta"]["planning"]["spec"]["relevance_query"] == message


def test_short_answer_and_followup_keep_original_goal_separate_from_recall_terms(tmp_path):
    expansion = "Agent 运行框架 上下文 记忆 工具编排 恢复 权限"

    class Retriever:
        supports_relevance_query = True

        def __init__(self):
            self.calls = []

        def retrieve(self, query, eligible, pipeline, top_k, *, relevance_query=None):
            self.calls.append((query, relevance_query))
            return {"data": [], "meta": {"pipeline": pipeline, "rerank_status": "COMPLETED"}}

    class Planner:
        def __init__(self):
            self.turn = 0

        def plan(self, run, context):
            self.turn += 1
            if self.turn == 1:
                return QuerySpec(action="CLARIFY", clarification="准备哪个方向？")
            if self.turn == 2:
                original = resolve(context, "session.pending_clarification.original_message")
                assert "session.pending_clarification.original_message" in context["relevance_intent"]["user_sources"]
                target = original + "：" + context["message"]
            else:
                assert context["session"]["last_plan"].get("search_query") is None
                assert context["retrieval_expansions"]["previous_plan"]["search_query"] == expansion
                target = resolve(context, context["relevance_intent"]["previous_target_source"])
            return QuerySpec(action="SEARCH", search_query=expansion, relevance_query=target)

    retriever = Retriever()
    client, _, _, _ = app_fixture(tmp_path, Planner(), retriever)
    conversation = None
    for index, message in enumerate(["场景设计题", "Agent 应用设计", "这类题再找一些"]):
        body = {"message": message, "request_id": f"intent-turn-{index}"}
        if conversation:
            body.update(conversation_id=conversation["conversation_id"], expected_version=conversation["conversation_version"])
        response = client.post("/api/query", json=body)
        assert response.status_code == 200, response.text
        conversation = response.json()["meta"]
    assert retriever.calls == [(expansion, "场景设计题：Agent 应用设计")] * 2
