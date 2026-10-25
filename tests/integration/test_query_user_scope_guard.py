"""User scope, not an inferred taxonomy or relevance rewrite, owns retrieval."""
import pytest
from uuid import uuid4

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec, ListQueryRequest
from interview_intelligence.analytics.listing import ListRequest
from test_query_question_type_scope import fixture
from test_full_algorithm_query import service_fixture, algorithm_plan, assert_complete_scope


def test_api_stops_when_host_changes_a_nonfinal_plan_to_terminal_clarification(tmp_path, monkeypatch):
    from test_conversation_quality import app_fixture
    from interview_intelligence.agent.model_gateway import ModelGateway

    gateway_calls = []

    async def forbidden_gateway(*args, **kwargs):
        gateway_calls.append(True)
        raise AssertionError("A host terminal result must not trigger another planning call")

    monkeypatch.setattr(ModelGateway, "plan", forbidden_gateway)

    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="COUNT", final=False, filters={"question_type": "SCENARIO"})

    client, _, _, _ = app_fixture(tmp_path, Planner())
    response = client.post("/api/query", json={"message": "数据库场景题总共有多少", "request_id": "host-terminal"})
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["route"] == "CLARIFY"
    assert gateway_calls == []


def execute(service, message, plan, *, filters=None, conversation_id=None, requery_of_run_id=None):
    request = QueryRequest(message=message, request_id=str(uuid4()),
        filters=filters or {}, conversation_id=conversation_id, requery_of_run_id=requery_of_run_id)
    run = service.begin(request)
    tool = {"SEARCH": "search_questions", "COUNT": "get_question_count",
            "STATS": "get_question_stats"}.get(plan.action, "list_questions")
    service.execute(run, tool, plan)
    service.finish(run, provider="fixture")
    return run


@pytest.mark.parametrize("message", ["缓存的场景题", "消息队列场景题目", "数据库设计题", "工程场景设计题"])
def test_natural_task_list_is_semantic_and_does_not_exclude_other_labels(message):
    _, project, scenario, service = fixture()
    run = execute(service, message, QuerySpec(action="LIST",
        filters={"question_type": "SCENARIO", "topic_l1": "Redis", "topic_l2": "缓存问题"}))
    assert run.plan.action == "SEARCH"
    assert set(service.retriever.calls[-1]["eligible"]) == {project, scenario}
    assert service.retriever.calls[-1]["query"] == message
    assert service.retriever.calls[-1]["relevance_query"] == message
    meta = run.result["facts"]["meta"]
    assert meta["user_scope_policy"]["planned_action"] == "LIST"
    assert meta["user_scope_policy"]["route_decision"] == "natural_task_requires_semantic_search"
    assert meta["applied_filters"]["question_type"] is None
    assert meta["applied_filters"]["topic_l1"] is None
    assert "list_request" not in run.state


def test_relevance_rewrite_cannot_add_domain_exclusions_or_answer_components():
    _, _, _, service = fixture()
    message = "工程场景设计题"
    rewrite = "传统后端独立业务系统，排除Agent/AI，必须用Redis和MQ"
    run = execute(service, message, QuerySpec(action="SEARCH", search_query="工程 设计 后端",
        relevance_query=rewrite))
    assert service.retriever.calls[-1]["relevance_query"] == message
    policy = run.result["facts"]["meta"]["user_scope_policy"]["relevance"]
    assert policy == {"planned": rewrite, "applied": message, "source": "user_messages"}


@pytest.mark.parametrize("message,filters", [
    ("只看题型标签SCENARIO的场景题", {}),
    ("按分类列出场景题", {}),
    ("场景题", {"question_type": "SCENARIO"}),
])
def test_explicit_taxonomy_remains_exact_sql(message, filters):
    _, _, scenario, service = fixture()
    run = execute(service, message, QuerySpec(action="LIST", filters={"question_type": "SCENARIO"}), filters=filters)
    assert run.plan.action == "LIST"
    assert [row["canonical_question_id"] for row in run.result["facts"]["data"]] == [scenario]
    assert service.retriever.calls == []


def test_explicit_topic_does_not_authorize_inferred_question_type_and_ui_cannot_be_removed():
    _, project, scenario, service = fixture()
    run = execute(service, "按主题标签Redis找场景设计题", QuerySpec(action="LIST",
        filters={"topic_l1": "Redis", "question_type": "SCENARIO"}))
    assert run.plan.action == "SEARCH"
    assert run.plan.filters.topic_l1 == "Redis" and run.plan.filters.question_type is None
    assert set(service.retriever.calls[-1]["eligible"]) == {project, scenario}
    assert run.result["facts"]["meta"]["search_filter_policy"]["fields"]["topic_l1"]["source"] == "explicit_text"
    request = QueryRequest(message="场景设计题", request_id="conflict", filters={"topic_l1": "Redis"})
    pending = service.begin(request)
    with pytest.raises(ValueError, match="EXPLICIT_FILTER_CONFLICT"):
        service.execute(pending, "list_questions", QuerySpec(action="LIST", filters={"question_type": "SCENARIO"}))
    service.fail(pending)


@pytest.mark.parametrize("message,action", [
    ("数据库场景题总共有多少", "COUNT"), ("场景题按公司分布", "STATS"),
    ("所有数据库设计题", "LIST"), ("场景题全库频次前5", "LIST"),
])
def test_natural_complete_scope_does_not_masquerade_as_finite_search(message, action):
    _, _, _, service = fixture()
    run = execute(service, message, QuerySpec(action=action, filters={"question_type": "SCENARIO"}))
    assert run.plan.action == "CLARIFY"
    assert service.retriever.calls == []
    assert "counts" not in run.result["facts"]["meta"]


def test_explicit_domain_in_user_language_survives_relevance_guard():
    _, _, _, service = fixture()
    message = "只找传统后端设计题，排除Agent和AI应用"
    execute(service, message, QuerySpec(action="SEARCH", search_query="系统设计", relevance_query="所有工程设计"))
    assert service.retriever.calls[-1]["relevance_query"] == message


def test_clarification_cannot_invent_algorithm_scope_for_company_frequency_list(tmp_path):
    service = service_fixture(tmp_path)
    original = execute(service, "字节频次前5", QuerySpec(action="CLARIFY",
        clarification="是按算法题频次还是所有题？", clarification_options=["前5", "全部"]))
    run = execute(service, "前5", algorithm_plan().model_copy(update={"top_n": 5}),
        conversation_id=original.conversation_id)
    assert run.plan.semantic_task is None
    assert run.plan.action == "LIST"
    assert run.result["facts"]["meta"]["pagination"]["total"] == 3
    assert run.result["facts"]["meta"]["user_scope_policy"]["semantic_task"]["decision"] == "unsupported_model_scope_removed"
    assert run.result["facts"]["meta"]["user_scope_policy"]["user_messages"] == ["字节频次前5", "前5"]


def test_algorithm_scope_survives_continuation_count_and_next_beyond_fifty(tmp_path):
    service = service_fixture(tmp_path, extra=63)
    first = execute(service, "字节的算法题", algorithm_plan(page_size=1))
    assert_complete_scope(first.result, canonical=65, occurrences=66, returned=1)
    counted = execute(service, "这些一共多少", algorithm_plan(action="COUNT"), conversation_id=first.conversation_id)
    assert counted.plan.semantic_task == "ALGORITHM_PROBLEM"
    assert counted.result["facts"]["meta"]["counts"]["canonical_questions"] == 65
    page = execute(service, "下一页", QuerySpec(action="NEXT"), conversation_id=first.conversation_id)
    assert_complete_scope(page.result, canonical=65, occurrences=66, returned=1)
    assert page.result["facts"]["data"][0]["canonical_question_id"] != first.result["facts"]["data"][0]["canonical_question_id"]


def test_old_model_semantic_task_does_not_authorize_new_company_query(tmp_path):
    service = service_fixture(tmp_path)
    first = execute(service, "字节算法题", algorithm_plan())
    switched = execute(service, "换为字节高频题", algorithm_plan(), conversation_id=first.conversation_id)
    assert switched.plan.semantic_task is None
    assert switched.result["facts"]["meta"]["pagination"]["total"] == 3


def test_user_goal_survives_search_follow_up_and_historical_requery():
    _, _, _, service = fixture()
    first = execute(service, "Redis故障排查场景题", QuerySpec(action="SEARCH", search_query="Redis 故障",
        relevance_query="Redis概念和优缺点"))
    follow = execute(service, "这些再找一些", QuerySpec(action="SEARCH", search_query="Redis 场景",
        relevance_query="纯知识"), conversation_id=first.conversation_id)
    assert "Redis故障排查场景题" in service.retriever.calls[-1]["relevance_query"]
    assert "纯知识" not in service.retriever.calls[-1]["relevance_query"]
    refresh = execute(service, "重新检索", QuerySpec(action="SEARCH", search_query="Redis",
        relevance_query="别的范围"), requery_of_run_id=follow.id)
    assert "Redis故障排查场景题" in service.retriever.calls[-1]["relevance_query"]
    assert "别的范围" not in service.retriever.calls[-1]["relevance_query"]
    switched = execute(service, "换为消息队列场景题", QuerySpec(action="LIST",
        filters={"question_type": "SCENARIO"}), conversation_id=first.conversation_id)
    assert service.retriever.calls[-1]["relevance_query"] == "换为消息队列场景题"


def test_structured_algorithm_scope_needs_no_natural_language_authorization(tmp_path):
    service = service_fixture(tmp_path)
    request = ListQueryRequest(list_request=ListRequest(company="字节", semantic_task="ALGORITHM_PROBLEM"),
        request_id="structured-algorithm").as_query()
    run = service.begin(request)
    service.execute(run, "list_questions", algorithm_plan())
    service.finish(run, provider="fixture")
    assert_complete_scope(run.result, canonical=2, occurrences=3, returned=2)


def test_negative_algorithm_request_does_not_run_positive_scope(tmp_path):
    service = service_fixture(tmp_path)
    run = execute(service, "字节题，不要算法题", algorithm_plan())
    assert run.plan.action == "CLARIFY"
    assert run.plan.semantic_task is None


def test_exact_text_type_survives_search_and_is_auditable():
    _, _, scenario, service = fixture()
    run = execute(service, "题型标签只选SCENARIO，查支付相关题", QuerySpec(action="SEARCH",
        search_query="支付", filters={"question_type": "SCENARIO"}))
    assert service.retriever.calls[-1]["eligible"] == [scenario]
    assert run.result["facts"]["meta"]["question_type_filter_policy"]["source"] == "explicit_text"


def test_fresh_goal_does_not_inherit_an_old_exact_label_scope():
    _, project, scenario, service = fixture()
    first = execute(service, "按题型分类列出场景题", QuerySpec(action="LIST", filters={"question_type": "SCENARIO"}))
    new = execute(service, "工程设计题", QuerySpec(action="SEARCH", search_query="设计",
        filters={"question_type": "SCENARIO"}), conversation_id=first.conversation_id)
    assert set(service.retriever.calls[-1]["eligible"]) == {project, scenario}
    assert new.plan.filters.question_type is None


def test_long_original_scope_is_never_truncated_and_requery_remains_valid():
    _, _, _, service = fixture()
    message = "查找工程场景设计题。" + "要求保留原始约束。" * 55 + "仅限金融业务。"
    assert len(message) > 500
    first = execute(service, message, QuerySpec(action="SEARCH", search_query="工程设计",
        relevance_query="无约束系统设计"))
    assert service.retriever.calls[-1]["relevance_query"] == message
    assert first.plan.relevance_query is None
    QuerySpec.model_validate(first.result["planning"]["spec"])
    refresh = execute(service, "重新检索", QuerySpec(action="SEARCH", search_query="工程设计"),
        requery_of_run_id=first.id)
    actual = service.retriever.calls[-1]["relevance_query"]
    assert message in actual and "仅限金融业务。" in actual
    assert refresh.result["facts"]["meta"]["user_scope_policy"]["relevance"]["applied"] == actual


def test_all_directions_clarification_does_not_mean_complete_corpus_or_algorithm():
    _, project, scenario, service = fixture()
    original = execute(service, "场景设计题", QuerySpec(action="CLARIFY",
        clarification="准备哪个方向？", clarification_options=["算法设计", "业务系统", "全部方向"]))
    selected = execute(service, "全部方向", QuerySpec(action="LIST", filters={"question_type": "SCENARIO"}),
        conversation_id=original.conversation_id)
    assert selected.plan.action == "SEARCH"
    assert set(service.retriever.calls[-1]["eligible"]) == {project, scenario}
    assert "算法设计" not in service.retriever.calls[-1]["relevance_query"]
    assert "场景设计题" in service.retriever.calls[-1]["relevance_query"]


def test_accepting_candidate_search_after_complete_scope_clarification_can_progress():
    _, _, _, service = fixture()
    first = execute(service, "所有数据库设计题", QuerySpec(action="LIST", filters={"question_type": "SYSTEM_DESIGN"}))
    assert first.plan.action == "CLARIFY"
    selected = execute(service, "按题意查找候选", QuerySpec(action="LIST", filters={"question_type": "SYSTEM_DESIGN"}),
        conversation_id=first.conversation_id)
    assert selected.plan.action == "SEARCH"


def test_fragment_answer_without_options_keeps_only_original_user_goal():
    _, _, _, service = fixture()
    first = execute(service, "准备面试题", QuerySpec(action="CLARIFY",
        clarification="准备哪个方向？可以选择算法或传统后端。"))
    answered = execute(service, "Agent 应用设计", QuerySpec(action="SEARCH", search_query="Agent",
        relevance_query="传统后端算法设计"), conversation_id=first.conversation_id)
    assert answered.result["facts"]["meta"]["user_scope_policy"]["user_messages"] == ["准备面试题", "Agent 应用设计"]
    actual = service.retriever.calls[-1]["relevance_query"]
    assert "准备面试题" in actual and "Agent 应用设计" in actual
    assert "算法" not in actual and "传统后端" not in actual
    continued = execute(service, "相关的再来一些", QuerySpec(action="SEARCH", search_query="Agent"),
        conversation_id=first.conversation_id)
    assert continued.result["facts"]["meta"]["user_scope_policy"]["user_messages"] == [
        "准备面试题", "Agent 应用设计", "相关的再来一些"]


@pytest.mark.parametrize("new_goal", ["换为Redis场景题", "Redis场景题", "解释Redis原理"])
def test_pending_clarification_does_not_capture_a_new_self_contained_goal(new_goal):
    _, _, _, service = fixture()
    first = execute(service, "算法设计题", QuerySpec(action="CLARIFY", clarification="准备哪个算法方向？"))
    changed = execute(service, new_goal, QuerySpec(action="SEARCH", search_query="Redis"),
        conversation_id=first.conversation_id)
    assert changed.result["facts"]["meta"]["user_scope_policy"]["user_messages"] == [new_goal]
    assert service.retriever.calls[-1]["relevance_query"] == new_goal
