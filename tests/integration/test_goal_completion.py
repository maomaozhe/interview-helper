"""Goals, authoritative completion and fresh evidence across real host turns."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from interview_intelligence.agent.goal_contract import UserGoal
from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.domain.models import AgentTurn, CorpusState, ReviewEvent, ToolInvocation
from test_analytics import seed_corpus


GOAL = UserGoal(objective="核查 Redis 题目覆盖情况并解释判断依据", response_mode="SYNTHESIS")
SCOPE = {"company": "字节", "topic_l1": "Redis"}


def make_service(*, retriever=None):
    database, fast_id, persistence_id = seed_corpus()
    return QueryService(database, retriever, signing_key="goal-test"), fast_id, persistence_id


def begin(service, request_id, *, message="核查字节 Redis 题目覆盖情况并解释依据", previous=None):
    return service.begin(QueryRequest(message=message, request_id=request_id,
        **({"conversation_id": previous.conversation_id, "expected_version": previous.version + 1}
           if previous else {})))


def answer(*, references=(), ids=(), goal=GOAL, filters=SCOPE, basis="CORPUS"):
    return QuerySpec(action="ANSWER", user_goal=goal, filters=filters, answer_basis=basis,
        answer_kind="EXPLAIN", question_ids=list(ids), evidence_tool_ordinals=list(references),
        answer_text="当前范围已确认两道去重题。题数、提问次数与候选覆盖范围分别判断。")


def test_synthesis_count_is_evidence_even_when_final_was_proposed_and_finish_cannot_commit_it():
    service, _, _ = make_service()
    run = begin(service, "synthesis-final-proposal")
    result = service.execute(run, "get_question_count", QuerySpec(action="COUNT", user_goal=GOAL,
        filters=SCOPE, final=True))
    assert result["planning"]["spec"]["final"] is True
    assert result["facts"]["meta"]["completion"]["status"] == "NEEDS_RESPONSE"
    assert not run.terminal
    with pytest.raises(ValueError, match="QUERY_RESPONSE_INCOMPLETE"):
        service.finish(run, provider="fixture")
    with service.database.session() as session:
        assert session.get(AgentTurn, run.id).status == "RUNNING"
    service.execute(run, "answer_question", answer(references=[1]))
    finished = service.finish(run, provider="fixture")
    assert finished["facts"]["meta"]["completion"]["answer_completed"] is True


def test_three_independent_reads_feed_one_answer_with_the_same_goal_and_durable_receipts():
    service, _, _ = make_service()
    run = begin(service, "three-reads")
    for name, plan in [
        ("get_question_count", QuerySpec(action="COUNT", user_goal=GOAL, filters=SCOPE)),
        ("get_question_stats", QuerySpec(action="STATS", user_goal=GOAL, filters=SCOPE, group_by="topic")),
        ("list_questions", QuerySpec(action="LIST", user_goal=GOAL, filters=SCOPE, page_size=1)),
    ]:
        service.execute(run, name, plan)
        assert not run.terminal
    context = service.model_context(run)
    assert [part["ordinal"] for part in context["completed_tools"]] == [1, 2, 3]
    assert context["completed_tools"][0]["meta"]["counts"]["canonical_questions"] == 2
    assert context["user_goal"]["objective"] == GOAL.objective
    assert context["completion"]["status"] == "NEEDS_RESPONSE"
    assert context["user_goal"]["run_id"] == run.id
    service.execute(run, "answer_question", answer(references=[1, 2, 3]))
    finished = service.finish(run, provider="fixture")
    assert [part["intent"] for part in finished["facts"]["components"]] == ["COUNT", "STATS", "LIST"]
    assert [trace["name"] for trace in finished["tool_trace"]] == [
        "get_question_count", "get_question_stats", "list_questions", "answer_question"]
    assert all(part["facts"]["meta"]["user_goal"] == finished["facts"]["meta"]["user_goal"]
               for part in finished["facts"]["components"])
    with service.database.session() as session:
        assert session.get(AgentTurn, run.id).status == "SUCCEEDED"
        assert session.scalar(select(func.count()).select_from(ToolInvocation).where(
            ToolInvocation.run_id == run.id, ToolInvocation.status == "COMPLETED")) == 4


@pytest.mark.parametrize("proposal", [
    UserGoal(objective=GOAL.objective, response_mode="DIRECT_RESULT"),
    UserGoal(objective="只返回统计表", response_mode="SYNTHESIS"),
])
def test_later_tools_cannot_downgrade_or_replace_the_bound_user_goal(proposal):
    service, _, _ = make_service()
    run = begin(service, "goal-conflict")
    service.execute(run, "get_question_count", QuerySpec(action="COUNT", user_goal=GOAL, filters=SCOPE))
    with pytest.raises(ValueError, match="QUERY_GOAL_CONFLICT"):
        service.execute(run, "list_questions", QuerySpec(action="LIST", user_goal=proposal, filters=SCOPE))
    assert run.tool_calls == 1 and not run.terminal
    assert run.user_goal["response_mode"] == "SYNTHESIS"
    service.execute(run, "answer_question", answer(references=[1]))
    service.finish(run, provider="fixture")


def test_historical_evidence_ordinal_is_not_current_turn_authority():
    service, _, _ = make_service()
    counted = begin(service, "previous-count")
    service.execute(counted, "get_question_count", QuerySpec(action="COUNT", filters=SCOPE))
    service.finish(counted, provider="fixture")
    current = begin(service, "fresh-answer", previous=counted)
    with pytest.raises(ValueError, match="QUERY_ANSWER_TOOL_EVIDENCE_INVALID"):
        service.execute(current, "answer_question", answer(references=[1]))
    assert not current.parts
    service.execute(current, "get_question_count", QuerySpec(action="COUNT", user_goal=GOAL, filters=SCOPE))
    service.execute(current, "answer_question", answer(references=[1]))
    service.finish(current, provider="fixture")


def test_answer_references_must_identify_completed_read_receipts():
    service, _, _ = make_service()
    run = begin(service, "future-reference")
    service.execute(run, "get_question_count", QuerySpec(action="COUNT", user_goal=GOAL, filters=SCOPE))
    with pytest.raises(ValueError, match="QUERY_ANSWER_TOOL_EVIDENCE_INVALID"):
        service.execute(run, "answer_question", answer(references=[2]))
    with pytest.raises(ValueError, match="QUERY_ANSWER_BASIS_CONFLICT"):
        service.execute(run, "answer_question", answer(references=[1], basis="GENERAL_KNOWLEDGE"))
    service.execute(run, "answer_question", answer(references=[1]))
    service.finish(run, provider="fixture")


def install_zero_search(service, monkeypatch, *, eligible):
    with service.database.session() as session, session.begin():
        corpus = session.get(CorpusState, 1)
        corpus.current_revision = corpus.indexed_revision = 1
    service.retriever = SimpleNamespace()
    monkeypatch.setattr("interview_intelligence.agent.query_service.search_questions", lambda *args, **kwargs: {
        "data": [], "meta": {"pipeline": "HYBRID_RERANK", "eligible_count": eligible,
            "candidate_count": min(eligible, 50), "candidate_verification_status": "COMPLETE",
            "rerank_status": "COMPLETED", "invalid_candidate_count": 0}})


@pytest.mark.parametrize("eligible", [0, 80])
def test_zero_search_can_ground_an_id_free_answer_without_claiming_unknown_corpus_coverage(monkeypatch, eligible):
    service, _, _ = make_service()
    install_zero_search(service, monkeypatch, eligible=eligible)
    run = begin(service, f"zero-search-{eligible}")
    result = service.execute(run, "search_questions", QuerySpec(action="SEARCH", user_goal=GOAL,
        filters=SCOPE, search_query="Redis 业务题", final=True))
    coverage = result["facts"]["meta"]["result_completeness"]
    assert not result["facts"]["data"] and not run.terminal
    assert coverage["corpus_complete"] is (eligible == 0)
    assert coverage["verification_complete"] is True
    context = service.model_context(run)
    assert context["completed_tools"][0]["meta"]["eligible_count"] == eligible
    assert context["completed_tools"][0]["meta"]["result_completeness"] == coverage
    service.execute(run, "answer_question", answer(references=[1]))
    assert run.result["planning"]["spec"]["question_ids"] == []
    finished = service.finish(run, provider="fixture")
    assert finished["facts"]["components"][0]["facts"]["meta"]["result_completeness"] == coverage


def test_malformed_search_coverage_cannot_authorize_an_id_free_corpus_answer(monkeypatch):
    service, _, _ = make_service()
    install_zero_search(service, monkeypatch, eligible=80)
    monkeypatch.setattr("interview_intelligence.agent.query_service.search_completeness", lambda *args: {
        "scope": "retrieval_candidates", "corpus_complete": "false", "verification_complete": True,
        "returned_count": 0})
    run = begin(service, "malformed-search-coverage")
    service.execute(run, "search_questions", QuerySpec(action="SEARCH", user_goal=GOAL,
        filters=SCOPE, search_query="Redis 业务题"))
    with pytest.raises(ValueError, match="QUERY_ANSWER_TOOL_EVIDENCE_INVALID"):
        service.execute(run, "answer_question", answer(references=[1]))
    assert not run.terminal
    with pytest.raises(ValueError, match="QUERY_RESPONSE_INCOMPLETE"):
        service.finish(run, provider="fixture")


def test_direct_result_completes_in_one_read_without_requiring_answer_generation():
    service, _, _ = make_service()
    run = begin(service, "direct-count", message="字节 Redis 有多少题")
    direct = UserGoal(objective="统计字节 Redis 的题数和提问次数", response_mode="DIRECT_RESULT")
    service.execute(run, "get_question_count", QuerySpec(action="COUNT", user_goal=direct, filters=SCOPE))
    assert run.terminal and run.completion["status"] == "COMPLETE"
    finished = service.finish(run, provider="fixture")
    assert finished["intent"] == "COUNT" and len(finished["tool_trace"]) == 1
    assert finished["facts"]["meta"]["counts"]["canonical_questions"] == 2
    assert finished["facts"]["meta"]["counts"]["occurrences"] == 2


def test_clarification_and_committed_review_are_distinct_from_a_completed_answer():
    service, _, _ = make_service()
    unclear = begin(service, "waiting-for-scope", message="分析一下题目情况")
    service.execute(unclear, "list_questions", QuerySpec(action="CLARIFY", user_goal=GOAL,
        clarification="请明确公司或主题。"))
    assert unclear.terminal and unclear.completion["status"] == "CLARIFICATION"
    assert unclear.completion["answer_completed"] is False
    service.finish(unclear, provider="fixture")

    listing = begin(service, "review-source-page", message="列出字节 Redis 题目")
    service.execute(listing, "list_questions", QuerySpec(action="LIST", filters=SCOPE, page_size=1))
    service.finish(listing, provider="fixture")
    write = begin(service, "review-commit", message="把当前页题目标记为已掌握", previous=listing)
    write_goal = UserGoal(objective="保存用户当前页复习状态", response_mode="SYNTHESIS")
    identity = write.state["current_page_ids"][0]
    service.execute(write, "record_review", QuerySpec(action="RECORD_REVIEW", user_goal=write_goal,
        review_items=[{"canonical_question_id": identity, "status": "MASTERED", "expected_version": 0}]))
    assert write.terminal and write.completion["status"] == "WRITE_COMMITTED"
    assert write.completion["answer_completed"] is False
    service.finish(write, provider="fixture")
    with service.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ReviewEvent)) == 1


def test_recovery_restores_nonterminal_completion_even_when_the_saved_plan_was_final():
    service, _, _ = make_service()
    request = QueryRequest(message="核查字节 Redis 题目覆盖并解释", request_id="recover-evidence")
    run = service.begin(request)
    service.execute(run, "get_question_count", QuerySpec(action="COUNT", user_goal=GOAL, filters=SCOPE, final=True))
    saved_goal = deepcopy(run.user_goal)
    service.fail(run, ValueError("QUERY_INTERRUPTED"))
    restarted = QueryService(service.database, signing_key="goal-test")
    restored = restarted.begin(request)
    assert restored.id == run.id and restored.tool_calls == 1
    assert restored.result["planning"]["spec"]["final"] is True
    assert restored.completion["status"] == "NEEDS_RESPONSE" and not restored.terminal
    assert restored.user_goal == saved_goal
    assert restarted.model_context(restored)["completed_tools"][0]["ordinal"] == 1
    restarted.execute(restored, "answer_question", answer(references=[1]))
    finished = restarted.finish(restored, provider="fixture-recovered")
    assert len(finished["tool_trace"]) == 2
    with service.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ToolInvocation).where(
            ToolInvocation.run_id == restored.id)) == 2


def test_multi_read_answer_preserves_the_list_page_and_next_turn_scope():
    service, fast_id, persistence_id = make_service()
    run = begin(service, "answer-before-next")
    service.execute(run, "list_questions", QuerySpec(action="LIST", user_goal=GOAL, filters=SCOPE, page_size=1))
    saved_page = deepcopy(run.state["list_request"])
    first_ids = list(run.state["current_page_ids"])
    service.execute(run, "get_question_count", QuerySpec(action="COUNT", user_goal=GOAL, filters=SCOPE))
    service.execute(run, "answer_question", answer(references=[1, 2]))
    assert run.state["list_request"] == saved_page
    assert run.state["current_page_ids"] == first_ids
    service.finish(run, provider="fixture")
    next_turn = begin(service, "next-after-synthesis", message="下一页", previous=run)
    direct = UserGoal(objective="继续浏览原列表下一页", response_mode="DIRECT_RESULT")
    result = service.execute(next_turn, "list_questions", QuerySpec(action="NEXT", user_goal=direct))
    assert {row["canonical_question_id"] for row in result["facts"]["data"]} == {fast_id, persistence_id} - set(first_ids)
    assert result["facts"]["meta"]["applied_filters"]["company"] == "字节"
    assert result["facts"]["meta"]["applied_filters"]["topic_l1"] == "Redis"
    assert next_turn.user_goal["run_id"] == next_turn.id and next_turn.user_goal["run_id"] != run.id
    service.finish(next_turn, provider="fixture")


def test_details_read_is_a_valid_current_turn_reference_for_an_answer_about_its_question():
    service, _, _ = make_service()
    listing = begin(service, "details-page", message="列出 Redis 题目")
    service.execute(listing, "list_questions", QuerySpec(action="LIST", filters=SCOPE, page_size=1))
    service.finish(listing, provider="fixture")
    run = begin(service, "details-evidence", message="解释第一题，并说明它的面经来源", previous=listing)
    identity = run.state["current_page_ids"][0]
    service.execute(run, "get_question_details", QuerySpec(action="DETAILS", user_goal=GOAL,
        filters=SCOPE, question_ids=[identity]))
    service.execute(run, "answer_question", answer(references=[1], ids=[identity], basis="MIXED"))
    assert run.completion["status"] == "COMPLETE"
    service.finish(run, provider="fixture")


def test_reference_uses_the_committed_tool_ordinal_after_an_earlier_plan_was_rejected():
    service, _, _ = make_service()
    run = service.begin(QueryRequest(message="核查字节 Redis 题目并解释", request_id="ordinal-gap", filters=SCOPE))
    with pytest.raises(ValueError, match="EXPLICIT_FILTER_CONFLICT"):
        service.execute(run, "get_question_count", QuerySpec(action="COUNT", user_goal=GOAL))
    service.execute(run, "get_question_count", QuerySpec(action="COUNT", user_goal=GOAL, filters=SCOPE))
    assert len(run.parts) == 1 and run.parts[0]["facts"]["meta"]["completion"]["tool_ordinal"] == 2
    assert service.model_context(run)["completed_tools"][0]["ordinal"] == 2
    with pytest.raises(ValueError, match="QUERY_ANSWER_TOOL_EVIDENCE_INVALID"):
        service.execute(run, "answer_question", answer(references=[1]))
    service.execute(run, "answer_question", answer(references=[2]))
    result = service.finish(run, provider="fixture")
    assert [row["parameters"]["action"] for row in result["tool_trace"]] == ["COUNT", "ANSWER"]
