"""Advertised and executable tools leave room to finish the user's response."""
import time

import pytest
from sqlalchemy import func, select

from interview_intelligence.agent.goal_contract import UserGoal, bind_goal
from interview_intelligence.agent.harness import ToolPolicy
from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.domain.models import ToolInvocation, create_database
from interview_intelligence.providers.runtime import RequestLimits, current_call_reserve


def policy():
    return ToolPolicy.compile("根据题库和复习状态分析", {
        "current_page_ids": ["q1"], "list_request": {"cursor": "host-only-cursor"}})


def allows(value, name, action):
    return action in value.actions.get(name, [])


@pytest.mark.parametrize("remaining,pending,reserve,expected", [
    (5, 1, 1, True), (4, 1, 1, False),
    (4, 0, 1, True), (3, 0, 1, False),
    (4, 1, 0, True), (3, 1, 0, False),
    (3, 0, 0, True), (2, 0, 0, False),
])
def test_search_admission_accounts_for_pending_planning_tool_cost_and_completion(remaining, pending, reserve, expected):
    scoped = policy().with_budget(remaining_shared_calls=remaining, pending_planning_calls=pending,
                                  completion_reserve_calls=reserve, search_model_calls=3)
    assert allows(scoped, "search_questions", "SEARCH") is expected
    assert allows(scoped, "answer_question", "ANSWER")
    assert allows(scoped, "list_questions", "CLARIFY")


def test_final_synthesis_planning_call_cannot_choose_even_a_zero_provider_read():
    scoped = policy().with_budget(remaining_shared_calls=1, pending_planning_calls=1,
                                  completion_reserve_calls=1, search_model_calls=0)
    for name, action in [("list_questions", "LIST"), ("list_questions", "NEXT"),
                         ("get_question_count", "COUNT"), ("get_question_stats", "STATS"),
                         ("get_question_details", "DETAILS"), ("get_review_state", "REVIEW_STATE"),
                         ("search_questions", "SEARCH")]:
        assert not allows(scoped, name, action), f"{action} would require another planning call to answer"
    assert allows(scoped, "answer_question", "ANSWER")
    assert allows(scoped, "list_questions", "CLARIFY")


def test_multiple_zero_provider_reads_remain_available_before_the_response_reserve():
    before_planning = policy().with_budget(remaining_shared_calls=2, remaining_planning_calls=2,
        pending_planning_calls=1, completion_reserve_calls=1)
    executing = policy().with_budget(remaining_shared_calls=1, remaining_planning_calls=1,
        pending_planning_calls=0, completion_reserve_calls=1)
    for scoped in [before_planning, executing]:
        for name, action in [("get_question_count", "COUNT"), ("get_question_stats", "STATS"),
                             ("get_question_details", "DETAILS"), ("get_review_state", "REVIEW_STATE")]:
            assert allows(scoped, name, action), "provider-free evidence must not be restricted to a single read"


def test_planning_quota_can_exhaust_before_the_shared_provider_quota():
    scoped = policy().with_budget(remaining_shared_calls=6, remaining_planning_calls=1,
        pending_planning_calls=1, completion_reserve_calls=1)
    assert not allows(scoped, "get_question_count", "COUNT")
    assert not allows(scoped, "search_questions", "SEARCH")
    assert allows(scoped, "answer_question", "ANSWER")


@pytest.mark.parametrize("cost,remaining,expected", [(0, 2, True), (1, 2, False), (1, 3, True), (3, 4, False), (3, 5, True)])
def test_explicit_pipeline_cost_distinguishes_bm25_hybrid_and_verified_search(cost, remaining, expected):
    scoped = policy().with_budget(remaining_shared_calls=remaining, pending_planning_calls=1,
        completion_reserve_calls=1, search_model_calls=cost)
    assert allows(scoped, "search_questions", "SEARCH") is expected
    assert scoped.model_dump()["model_budget"]["search_model_calls"] == cost


def test_next_page_processing_cost_does_not_hide_cached_sql_listing_or_clarification():
    expensive = policy().with_budget(remaining_shared_calls=4, pending_planning_calls=1,
        completion_reserve_calls=1, next_model_calls=3)
    cached = policy().with_budget(remaining_shared_calls=4, pending_planning_calls=1,
        completion_reserve_calls=1, next_model_calls=0)
    assert not allows(expensive, "list_questions", "NEXT")
    assert allows(cached, "list_questions", "NEXT")
    assert allows(expensive, "list_questions", "LIST")
    assert allows(expensive, "list_questions", "CLARIFY")


def test_direct_result_may_execute_provider_free_result_after_its_final_planning_call():
    scoped = policy().with_budget(remaining_shared_calls=0, remaining_planning_calls=0,
        pending_planning_calls=0, completion_reserve_calls=0)
    assert allows(scoped, "get_question_count", "COUNT")
    assert allows(scoped, "list_questions", "LIST")


def test_budget_projection_never_mutates_base_permissions_or_grants_denied_writes():
    original = policy()
    before = original.model_dump()
    scoped = original.with_budget(remaining_shared_calls=1, pending_planning_calls=1, completion_reserve_calls=1)
    assert original.model_dump() == before
    assert "record_review" not in scoped.actions
    assert not allows(scoped, "search_questions", "SEARCH")


def test_read_provider_attempts_cannot_spend_the_reserved_response_call():
    limits = RequestLimits(deadline=time.monotonic() + 30, max_calls=6, calls=4)
    token = current_call_reserve.set(1)
    try:
        limits.attempt()
        assert limits.calls == 5
        with pytest.raises(ValueError, match="QUERY_MODEL_BUDGET_EXCEEDED"):
            limits.attempt()
        assert limits.calls == 5, "a pre-provider rejection must not consume the response reservation"
    finally:
        current_call_reserve.reset(token)
    limits.attempt()
    assert limits.calls == 6, "the planner may use the reserved call after read-tool scope exits"
    with pytest.raises(ValueError, match="QUERY_MODEL_BUDGET_EXCEEDED"):
        limits.attempt()


def service_run(tmp_path, *, pipeline="HYBRID_RERANK", retriever=None, max_planning_calls=4,
                message="统计并解释题库情况"):
    service = QueryService(create_database("sqlite+pysqlite:///:memory:"), retriever,
        signing_key="budget-fixture", feedback_root=tmp_path / "feedback", max_planning_calls=max_planning_calls,
        max_total_model_calls=6)
    run = service.begin(QueryRequest(message=message, request_id="budget-fixture", pipeline=pipeline))
    return service, run


def test_unbound_initial_goal_keeps_direct_tools_until_the_model_declares_the_goal(tmp_path):
    service, run = service_run(tmp_path)
    run.limits.calls = 4
    run.model_calls = 1
    context = service.model_context(run)
    assert context.get("user_goal") is None
    assert context["budget"]["completion_reserve_calls"] == 0
    assert context["budget"]["pending_planning_calls"] == 1
    assert "SEARCH" not in context["tool_policy"]["allowed_actions"].get("search_questions", [])
    assert "COUNT" in context["tool_policy"]["allowed_actions"]["get_question_count"]


def test_one_planning_call_initial_menu_can_declare_a_direct_count_goal(tmp_path):
    service, run = service_run(tmp_path, max_planning_calls=1, message="题库有多少题")
    context = service.model_context(run)
    assert context["budget"]["completion_reserve_calls"] == 0
    assert "COUNT" in context["tool_policy"]["allowed_actions"]["get_question_count"]
    run.model_calls = 1
    run.limits.calls = 1
    result = service.execute(run, "get_question_count", QuerySpec(action="COUNT",
        user_goal=UserGoal(objective=run.request.message, response_mode="DIRECT_RESULT")))
    assert result["intent"] == "COUNT" and run.terminal
    assert run.limits.calls == 1


def test_one_planning_call_cannot_start_synthesis_evidence_without_a_future_answer_call(tmp_path):
    service, run = service_run(tmp_path, max_planning_calls=1)
    assert allows(service.tool_policy(run, planning_pending=True), "get_question_count", "COUNT")
    run.model_calls = 1
    run.limits.calls = 1
    with pytest.raises(ValueError, match="QUERY_TOOL_NOT_ALLOWED"):
        service.execute(run, "get_question_count", QuerySpec(action="COUNT",
            user_goal=UserGoal(objective=run.request.message, response_mode="SYNTHESIS")))
    assert run.user_goal["response_mode"] == "SYNTHESIS"
    assert service.tool_policy(run).model_budget["completion_reserve_calls"] == 1
    assert run.tool_calls == 0 and run.limits.calls == 1 and not run.terminal


@pytest.mark.parametrize("pipeline,cost,allowed", [("BM25", 0, True), ("HYBRID", 1, False),
                                                 ("DENSE", 1, False), ("HYBRID_RERANK", 3, False)])
def test_service_respects_exact_explicit_pipeline_cost_when_offering_tools(tmp_path, pipeline, cost, allowed):
    service, run = service_run(tmp_path, pipeline=pipeline)
    bind_goal(run, UserGoal(objective=run.request.message, response_mode="SYNTHESIS"))
    run.limits.calls = 4
    run.model_calls = 1
    projected = service.tool_policy(run, planning_pending=True)
    assert projected.model_dump()["model_budget"]["search_model_calls"] == cost
    assert allows(projected, "search_questions", "SEARCH") is allowed
    assert service._requested_pipeline(run) == pipeline


def test_legacy_typed_direct_result_uses_no_synthesis_reserve(tmp_path):
    service, run = service_run(tmp_path)
    run.limits.calls = run.limits.max_calls
    run.model_calls = service.max_planning_calls
    result = service.execute(run, "get_question_count", QuerySpec(action="COUNT"))
    assert result["facts"]["meta"]["user_goal"]["source"] == "legacy_typed_plan"
    assert result["facts"]["meta"]["user_goal"]["response_mode"] == "DIRECT_RESULT"
    assert service.tool_policy(run).model_budget["completion_reserve_calls"] == 0
    assert run.terminal is True
    assert run.limits.calls == run.limits.max_calls


def test_stale_search_is_rejected_before_provider_and_reserved_planner_can_still_answer(tmp_path):
    class ForbiddenRetriever:
        calls = 0

        def retrieve(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("unaffordable evidence must be rejected before retrieval or embedding")

    retriever = ForbiddenRetriever()
    service, run = service_run(tmp_path, retriever=retriever)
    goal = UserGoal(objective=run.request.message, response_mode="SYNTHESIS")
    service.execute(run, "get_question_count", QuerySpec(action="COUNT", user_goal=goal))
    run.limits.calls = 5
    run.model_calls = 2
    before_tools = run.tool_calls
    with pytest.raises(ValueError, match="QUERY_TOOL_NOT_ALLOWED"):
        service.execute(run, "search_questions", QuerySpec(action="SEARCH", search_query="题库主题", user_goal=goal))
    assert retriever.calls == 0
    assert run.limits.calls == 5 and run.tool_calls == before_tools
    with service.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ToolInvocation)) == 1
    run.limits.attempt()
    service.execute(run, "answer_question", QuerySpec(action="ANSWER", user_goal=goal,
        answer_text="当前查询范围的完整计数已取得；分析只使用这次计数证据。",
        answer_basis="CORPUS", evidence_tool_ordinals=[1]))
    assert run.limits.calls == 6 and run.terminal is True
    assert run.result["facts"]["meta"]["completion"]["status"] == "COMPLETE"


@pytest.mark.parametrize("page_offset,cost,allowed", [(0, 0, True), (15, 3, False)])
def test_signed_cached_workset_next_is_free_but_a_new_batch_needs_budget(tmp_path, page_offset, cost, allowed):
    from interview_intelligence.agent import complete_workset as pages
    from interview_intelligence.relevance_scope import compile_relevance_scope

    service, run = service_run(tmp_path)
    bind_goal(run, UserGoal(objective=run.request.message, response_mode="SYNTHESIS"))
    run.state["relevance_scope"] = compile_relevance_scope(["所有工程系统设计题 具体业务系统设计"], candidate_mode=True)
    plan = QuerySpec(action="SEARCH", search_query="具体业务系统设计", page_size=5)
    ids = [f"q-{index}" for index in range(40)]
    pages.create(run, {"candidate_ids": ids, "manifest_hash": "budget-manifest"}, plan, {},
        signing_key=service.signing_key, user_id=service.user_id)
    pages.apply_batch(run, ids[:20], {"data": [{"canonical_question_id": identity} for identity in ids[:20]],
        "outcomes": [{"canonical_question_id": identity, "status": "CONFIRMED"} for identity in ids[:20]], "meta": {}},
        signing_key=service.signing_key, user_id=service.user_id)
    pages.page(run, page_offset, signing_key=service.signing_key, user_id=service.user_id)
    run.limits.calls, run.model_calls = 4, 1
    policy = service.tool_policy(run, planning_pending=True)
    assert policy.model_budget["next_model_calls"] == cost
    assert allows(policy, "list_questions", "NEXT") is allowed
