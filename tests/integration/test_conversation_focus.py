"""Host state preserves completed goals separately from reusable list pagination.

These tests execute explicit plans. Natural-language correction accuracy needs
separate real-model verification and is not claimed by these fixtures.
"""
from copy import deepcopy

import pytest

from interview_intelligence.agent.harness import ContextCompiler
from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import AgentConversation
from test_analytics import seed_corpus


def begin_follow_up(service, previous, message, request_id, version):
    return service.begin(QueryRequest(message=message, request_id=request_id,
        conversation_id=previous.conversation_id, expected_version=version))


def list_redis(service, *, action="LIST"):
    message = "Redis 高频问题有哪些？" if action == "LIST" else "Redis 各知识点的分布"
    run = service.begin(QueryRequest(message=message, request_id="initial-goal"))
    plan = QuerySpec(action=action, filters={"topic_l1": "Redis"}, page_size=1,
        group_by="topic" if action == "STATS" else "question", topic_level="L2")
    service.execute(run, "get_question_stats" if action == "STATS" else "list_questions", plan)
    service.finish(run, provider="fixture")
    return run


def assert_focus(focus, **expected):
    assert isinstance(focus, dict)
    assert {key: focus[key] for key in expected} == expected


def test_count_focus_has_actual_scope_and_retains_the_old_list_cursor_after_restart():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    listing = list_redis(service)
    old_listing = deepcopy(listing.state["list_request"])
    old_page = deepcopy(listing.state["current_page_ids"])
    assert old_listing["cursor"]

    run = begin_follow_up(service, listing, "其中腾讯未掌握的有多少", "count-goal", 1)
    plan = QuerySpec(action="COUNT", filters={"topic_l1": "Redis", "company": "腾讯"},
        review_statuses=["UNSEEN", "WEAK", "REVIEWED"], page_size=1)
    result = service.execute(run, "get_question_count", plan)
    assert result["facts"]["meta"]["counts"]["canonical_questions"] == 1
    assert_focus(run.state["conversation_focus"], intent="COUNT", source_action="COUNT",
        message=run.request.message, filters=result["facts"]["meta"]["applied_filters"],
        sort="frequency", top_n=None, review_statuses=plan.review_statuses,
        review_order="BEFORE_TOP_N", run_id=run.id)
    assert run.state["list_request"] == old_listing
    assert run.state["current_page_ids"] == old_page
    service.finish(run, provider="fixture")

    restarted = QueryService(db, signing_key="test")
    follow_up = begin_follow_up(restarted, run, "那二面呢", "restart-count-goal", 2)
    context = restarted.model_context(follow_up)
    assert_focus(context["session"]["conversation_focus"], intent="COUNT",
        filters=plan.filters.model_dump(mode="json"), run_id=run.id)
    assert context["session"]["list_request"]["topic_l1"] == "Redis"
    assert context["session"]["list_request"]["company"] is None
    assert context["session"]["has_next_page"] is True
    assert follow_up.state["list_request"] == old_listing
    assert "record_review" not in context["tool_policy"]["allowed_tools"]
    restarted.fail(follow_up)


def test_new_list_goal_takes_focus_from_older_count_without_discarding_count_receipt():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    run = service.begin(QueryRequest(message="Redis 有多少题", request_id="old-redis-count"))
    service.execute(run, "get_question_count", QuerySpec(action="COUNT", filters={"topic_l1": "Redis"}))
    service.finish(run, provider="fixture")
    old_count = deepcopy(run.state["last_count"])

    listing = begin_follow_up(service, run, "Java 高频题有哪些", "new-java-goal", 1)
    plan = QuerySpec(action="LIST", filters={"topic_l1": "Java"}, top_n=5, page_size=1)
    service.execute(listing, "list_questions", plan)
    service.finish(listing, provider="fixture")
    assert_focus(listing.state["conversation_focus"], intent="LIST", message=listing.request.message,
        filters=plan.filters.model_dump(mode="json"), top_n=5, run_id=listing.id)
    assert listing.state["last_count"] == old_count

    follow_up = begin_follow_up(service, listing, "一共有多少道题", "count-new-focus", 2)
    context = service.model_context(follow_up)
    assert context["session"]["conversation_focus"]["filters"]["topic_l1"] == "Java"
    assert context["session"]["last_count"]["filters"]["topic_l1"] == "Redis"
    assert context["context_contract"]["sources"]["conversation_focus"] == "completed_user_goal_independent_of_pagination"
    service.fail(follow_up)


@pytest.mark.parametrize("action", ["LIST", "STATS"])
def test_next_uses_original_paging_goal_even_after_an_independent_count(action):
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    listing = list_redis(service, action=action)
    assert listing.state["list_request"]["cursor"]
    original_message = listing.request.message
    count = begin_follow_up(service, listing, "其中腾讯共有多少题", "count-between-pages", 1)
    service.execute(count, "get_question_count", QuerySpec(action="COUNT",
        filters={"topic_l1": "Redis", "company": "腾讯"}))
    service.finish(count, provider="fixture")
    assert count.state["conversation_focus"]["intent"] == "COUNT"

    next_page = begin_follow_up(service, count, "下一页", "resume-original-goal", 2)
    service.execute(next_page, "list_questions", QuerySpec(action="NEXT"))
    assert_focus(next_page.state["conversation_focus"], intent=action, source_action="NEXT",
        message=original_message, run_id=next_page.id,
        filters=FilterSpec(topic_l1="Redis").model_dump(mode="json"), top_n=None)
    if action == "STATS":
        assert_focus(next_page.state["conversation_focus"], group_by="topic", topic_level="L2")
    assert next_page.state["last_count"]["filters"]["company"] == "腾讯"
    service.finish(next_page, provider="fixture")


def test_details_preserves_explicit_goal_until_final_answer_records_its_actual_scope():
    db, fast_id, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    listing = list_redis(service)
    assert listing.state["current_page_ids"] == [fast_id]
    previous_focus = deepcopy(listing.state["conversation_focus"])
    old_listing = deepcopy(listing.state["list_request"])
    run = begin_follow_up(service, listing, "解释腾讯 Redis 第一题", "answer-target", 1)
    scope = {"company": "腾讯", "topic_l1": "Redis"}
    service.execute(run, "get_question_details", QuerySpec(action="DETAILS", filters=scope,
        question_ids=[fast_id], final=False))
    assert run.state["conversation_focus"] == previous_focus
    assert service.model_context(run)["session"]["conversation_focus"]["message"] == listing.request.message

    plan = QuerySpec(action="ANSWER", filters=scope, question_ids=[fast_id], answer_basis="MIXED",
        answer_kind="EXPLAIN", answer_text="这道题关注内存存储和事件驱动的网络处理。")
    service.execute(run, "answer_question", plan)
    assert_focus(run.state["conversation_focus"], intent="ANSWER", message=run.request.message,
        filters=plan.filters.model_dump(mode="json"), run_id=run.id,
        answer_kind="EXPLAIN", answer_basis="MIXED", question_ids=[fast_id])
    assert run.state["list_request"] == old_listing
    assert run.state["current_page_ids"] == [fast_id]
    service.finish(run, provider="fixture")


@pytest.mark.parametrize("action", ["COUNT", "LIST", "STATS"])
def test_intermediate_reads_do_not_replace_goal_before_the_final_answer(action):
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    listing = list_redis(service)
    previous_focus = deepcopy(listing.state["conversation_focus"])
    run = begin_follow_up(service, listing, "分析 Java 题库覆盖情况", "read-then-answer", 1)
    plan = QuerySpec(action=action, filters={"topic_l1": "Java"}, final=False, page_size=1)
    tool = {"COUNT": "get_question_count", "LIST": "list_questions", "STATS": "get_question_stats"}[action]
    service.execute(run, tool, plan)
    assert run.state["conversation_focus"] == previous_focus
    assert service.model_context(run)["session"]["conversation_focus"]["message"] == listing.request.message
    answer = QuerySpec(action="ANSWER", filters={"topic_l1": "Java"}, answer_basis="CORPUS",
        answer_kind="STUDY_PLAN", answer_text="当前 Java 范围未查到题目，尚不能据此排序知识点。")
    service.execute(run, "answer_question", answer)
    assert_focus(run.state["conversation_focus"], intent="ANSWER", message=run.request.message,
        filters=answer.filters.model_dump(mode="json"), answer_basis="CORPUS", run_id=run.id)
    service.finish(run, provider="fixture")


@pytest.mark.parametrize("action", ["CLARIFY", "DETAILS", "REVIEW_STATE", "RECORD_REVIEW"])
def test_clarification_details_and_review_tools_do_not_replace_the_completed_goal(action):
    db, fast_id, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    listing = list_redis(service)
    previous_focus = deepcopy(listing.state["conversation_focus"])
    messages = {"CLARIFY": "查看后端题", "DETAILS": "查看第一题来源",
        "REVIEW_STATE": "查看第一题复习状态", "RECORD_REVIEW": "把第一题标记为已掌握"}
    run = begin_follow_up(service, listing, messages[action], "preserve-goal", 1)
    kwargs = {"clarification": "请选择具体岗位。"} if action == "CLARIFY" else (
        {"review_items": [{"canonical_question_id": fast_id, "status": "MASTERED"}]}
        if action == "RECORD_REVIEW" else {"question_ids": [fast_id]})
    name = {"CLARIFY": "list_questions", "DETAILS": "get_question_details",
        "REVIEW_STATE": "get_review_state", "RECORD_REVIEW": "record_review"}[action]
    service.execute(run, name, QuerySpec(action=action, filters={"topic_l1": "Redis"}, **kwargs))
    assert run.state["conversation_focus"] == previous_focus
    service.finish(run, provider="fixture")
    with db.session() as session:
        assert session.get(AgentConversation, run.conversation_id).state["conversation_focus"] == previous_focus


def legacy_state(intent):
    redis = FilterSpec(topic_l1="Redis", company="腾讯").model_dump(mode="json")
    java = FilterSpec(topic_l1="Java").model_dump(mode="json")
    plan = QuerySpec(action="STATS" if intent == "STATS" else "LIST", filters=java,
        group_by="topic" if intent == "STATS" else "question", topic_level="L2").model_dump(mode="json")
    if intent == "NEXT":
        plan.update(action="NEXT")
    return {"filters": java, "sort": "frequency", "last_plan": plan,
        "list_request": {**java, "page_size": 1, "cursor": "OPAQUE_LEGACY_CURSOR"},
        "current_page_ids": [], "last_response": {"intent": intent,
            "message": "上轮明确的目标", "answer": "上轮回答"},
        "last_count": {"filters": redis, "review_statuses": ["UNSEEN"],
            "message": "更早的 Redis 总数", "counts": {"canonical_questions": 1}}}


@pytest.mark.parametrize("compact_context", [True, False])
@pytest.mark.parametrize("intent", ["COUNT", "LIST", "STATS", "NEXT", "ANSWER"])
def test_legacy_focus_is_projected_after_restart_without_writing_back_or_inventing_answer_scope(intent, compact_context):
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    first = service.begin(QueryRequest(message="建立旧会话", request_id="legacy-session"))
    service.execute(first, "answer_question", QuerySpec(action="ANSWER", answer_text="可以继续提问。"))
    service.finish(first, provider="fixture")
    saved = legacy_state(intent)
    with db.session() as session, session.begin():
        session.get(AgentConversation, first.conversation_id).state = deepcopy(saved)

    restarted = QueryService(db, signing_key="test", compact_context=compact_context)
    run = begin_follow_up(restarted, first, "继续刚才的话题", "legacy-projection", 1)
    context = restarted.model_context(run)
    focus = context["session"]["conversation_focus"]
    assert_focus(focus, intent="LIST" if intent == "NEXT" else intent,
        message=saved["last_response"]["message"], provenance="legacy_saved_state")
    if intent == "ANSWER":
        assert not focus.get("filters")
    else:
        expected_scope = saved["last_count"] if intent == "COUNT" else saved["last_plan"]
        assert focus["filters"] == expected_scope["filters"]
    assert "conversation_focus" not in run.state
    assert ContextCompiler.state_projection(saved)["conversation_focus"] == focus
    with db.session() as session:
        assert session.get(AgentConversation, first.conversation_id).state == saved
    restarted.fail(run)
