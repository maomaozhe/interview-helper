"""Complete engineering collections execute independently of candidate search."""
from copy import deepcopy
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import AgentTurn, OccurrenceTaskAnnotation
from test_engineering_scope_listing import engineering_corpus


class NoCandidateSearch:
    def retrieve(self, *args, **kwargs):
        raise AssertionError("An audited complete collection must not use Top50 retrieval")


class NoPlanner:
    def plan(self, *args, **kwargs):
        raise AssertionError("An explicit broad collection does not need a model call")


def fixture(tmp_path, *, extra=0, complete=True):
    db = engineering_corpus(extra=extra)
    if complete:
        with db.session() as session, session.begin():
            row = session.get(OccurrenceTaskAnnotation, "o-006")
            evidence = deepcopy(row.evidence)
            evidence["semantic_scopes"]["ENGINEERING_DESIGN"] = {
                **evidence["semantic_scopes"]["ALGORITHM_PROBLEM"],
                "version": "engineering_design_scope_v1", "decision": "ACCEPT",
                "reason": "The source independently asks for a new message system."}
            row.evidence = evidence
    service = QueryService(db, NoCandidateSearch(), signing_key="engineering-pages",
        today=lambda: date(2026, 10, 16), feedback_root=tmp_path / "feedback")
    return db, service


def execute(service, message, *, plan=None, **request):
    run = service.begin(QueryRequest(message=message, request_id=str(uuid4()), **request))
    plan = plan or service.complete_collection_plan(run)
    assert plan is not None
    name = {"SEARCH": "search_questions", "COUNT": "get_question_count",
            "STATS": "get_question_stats"}.get(plan.action, "list_questions")
    service.execute(run, name, plan)
    response = service.finish(run, provider="fixture")
    return run, response


@pytest.mark.parametrize("message", [
    "列出所有工程系统设计题", "请列举全部工程场景设计题", "查看完整系统设计题",
])
def test_broad_collection_overrides_candidate_plan_and_inferred_restrictions(tmp_path, message):
    _, service = fixture(tmp_path)
    plan = QuerySpec(action="SEARCH", search_query="traditional backend",
        relevance_query="排除AI，只要完整后端系统", preferred_question_type="SYSTEM_DESIGN",
        filters={"question_type": "SYSTEM_DESIGN", "topic_l1": "Redis",
                 "job_family": "BACKEND", "response_form": "CODE", "coding_focus": "ENGINEERING",
                 "company": "字节", "position": "Java后端", "start_date": "2026-01-01", "round": "FIRST"})
    run, result = execute(service, message, plan=plan)
    assert run.plan.action == "LIST" and run.plan.semantic_task == "ENGINEERING_DESIGN"
    assert all(value is None for key, value in run.plan.filters.model_dump().items() if key != "date_basis")
    assert {row["canonical_question_id"] for row in result["facts"]["data"]} == {"cache", "orders", "agent", "other"}
    assert "4 道去重工程系统设计题" in result["answer"]
    assert "5 次工程系统设计提问" in result["answer"]
    assert result["facts"]["meta"]["semantic_scope"]["complete"] is True


def test_next_and_count_cover_more_than_fifty_without_retrieval_or_scope_loss(tmp_path):
    _, service = fixture(tmp_path, extra=61)
    run, result = execute(service, "列出所有工程系统设计题", page_size=13)
    ids, ranks = [], []
    while True:
        meta = result["facts"]["meta"]
        assert meta["pagination"]["result_total"] == 65
        assert result["planning"]["spec"]["semantic_task"] == "ENGINEERING_DESIGN"
        ids.extend(row["canonical_question_id"] for row in result["facts"]["data"])
        ranks.extend(row["rank"] for row in result["facts"]["data"])
        if not meta["pagination"]["next_cursor"]:
            break
        run, result = execute(service, "下一页", conversation_id=run.conversation_id,
            expected_version=result["conversation_version"])
    assert len(ids) == len(set(ids)) == 65
    assert ranks == list(range(1, 66))
    assert "agent" in ids and "project" not in ids
    counted, response = execute(service, "这些题一共有多少题", conversation_id=run.conversation_id,
        expected_version=result["conversation_version"])
    assert counted.plan.action == "COUNT" and counted.plan.semantic_task == "ENGINEERING_DESIGN"
    assert response["facts"]["meta"]["counts"]["canonical_questions"] == 65
    assert response["facts"]["meta"]["counts"]["occurrences"] == 66
    ended, last = execute(service, "下一页", conversation_id=counted.conversation_id,
        expected_version=response["conversation_version"])
    assert ended.plan.action == "CLARIFY"
    assert last["facts"]["data"] == []


def test_explicit_ui_and_company_filter_intersect_same_audited_occurrence(tmp_path):
    _, service = fixture(tmp_path)
    _, result = execute(service, "列出所有工程系统设计题", filters={"company": "字节", "round": "SECOND"})
    assert [(row["canonical_question_id"], row["occurrence_count"])
            for row in result["facts"]["data"]] == [("cache", 2)]
    assert result["facts"]["meta"]["applied_filters"]["round"] == "SECOND"


@pytest.mark.parametrize("filters", [
    {"company": "字节", "round": "SECOND"},
    {"question_type": "KNOWLEDGE", "coding_focus": "UNKNOWN"},
    {"start_date": "2026-07-01", "end_date": "2026-08-31", "date_basis": "INTERVIEW"},
])
def test_collection_count_retains_trusted_ui_filters_across_repeated_followups(tmp_path, filters):
    _, service = fixture(tmp_path)
    run, response = execute(service, "列出所有工程系统设计题", filters=filters)
    expected_total = response["facts"]["meta"]["pagination"]["result_total"]
    expected_occurrences = response["facts"]["meta"]["sample_counts"]["selected_occurrences"]
    for message in ("这些题一共有多少", "一共有多少"):
        run, response = execute(service, message, conversation_id=run.conversation_id,
            expected_version=response["conversation_version"])
        assert run.plan.action == "COUNT" and run.plan.semantic_task == "ENGINEERING_DESIGN"
        assert all(run.plan.filters.model_dump(mode="json")[key] == value for key, value in filters.items())
        assert response["facts"]["meta"]["counts"]["canonical_questions"] == expected_total
        assert response["facts"]["meta"]["counts"]["occurrences"] == expected_occurrences
        assert run.state["query_scope_intent"]["explicit_filters"] == filters


def test_count_with_inherited_ui_preserves_the_original_next_page(tmp_path):
    _, service = fixture(tmp_path)
    first, page = execute(service, "列出所有工程系统设计题", page_size=1,
        filters={"company": "字节", "round": "FIRST"})
    counted, count = execute(service, "这些题一共有多少", conversation_id=first.conversation_id,
        expected_version=page["conversation_version"])
    assert count["facts"]["meta"]["counts"]["canonical_questions"] == 2
    next_run, next_page = execute(service, "下一页", conversation_id=counted.conversation_id,
        expected_version=count["conversation_version"])
    assert next_run.plan.filters.company == "字节" and next_run.plan.filters.round == "FIRST"
    assert next_page["facts"]["meta"]["pagination"]["offset"] == 1
    assert {row["canonical_question_id"] for row in page["facts"]["data"]}.isdisjoint(
        row["canonical_question_id"] for row in next_page["facts"]["data"])


@pytest.mark.parametrize("model_filters", [{}, {"company": "腾讯", "round": "FIRST"}])
def test_stats_cannot_drop_or_change_trusted_ui_without_current_user_evidence(tmp_path, model_filters):
    _, service = fixture(tmp_path)
    first, page = execute(service, "列出所有工程系统设计题",
        filters={"company": "字节", "round": "SECOND"})
    run, response = execute(service, "这些题按公司统计", plan=QuerySpec(action="STATS",
        semantic_task="ENGINEERING_DESIGN", group_by="company", filters=model_filters), conversation_id=first.conversation_id,
        expected_version=page["conversation_version"])
    assert run.plan.filters.company == "字节" and run.plan.filters.round == "SECOND"
    assert [(row["key"], row["occurrence_count"]) for row in response["facts"]["data"]] == [("字节", 2)]


def test_legacy_list_explicit_filters_remain_trusted_without_new_provenance_field(tmp_path):
    _, service = fixture(tmp_path)
    first, page = execute(service, "列出所有工程系统设计题",
        filters={"company": "字节", "round": "SECOND"})
    run = service.begin(QueryRequest(message="这些题一共有多少", request_id=str(uuid4()),
        conversation_id=first.conversation_id, expected_version=page["conversation_version"]))
    run.state["query_scope_intent"].pop("explicit_filters")
    service.execute(run, "get_question_count", service.complete_collection_plan(run))
    assert run.plan.filters.company == "字节" and run.plan.filters.round == "SECOND"
    assert run.result["facts"]["meta"]["counts"]["canonical_questions"] == 1
    service.finish(run, provider="fixture")


def test_explicit_top_n_precedes_all_and_survives_pagination_and_sort_followup(tmp_path):
    _, service = fixture(tmp_path)
    first, page = execute(service, "按频次列出所有工程系统设计题前2道", page_size=1)
    assert first.plan.top_n == 2
    assert page["facts"]["meta"]["pagination"]["result_total"] == 2
    next_run, next_page = execute(service, "下一页", conversation_id=first.conversation_id,
        expected_version=page["conversation_version"])
    assert next_run.plan.top_n == 2
    assert next_page["facts"]["meta"]["pagination"]["next_cursor"] is None
    sorted_run, sorted_page = execute(service, "按重要性排序", conversation_id=next_run.conversation_id,
        expected_version=next_page["conversation_version"])
    assert sorted_run.plan.top_n == 2 and sorted_run.plan.sort == "importance"
    assert sorted_page["facts"]["meta"]["pagination"]["result_total"] == 2


def test_text_page_size_controls_first_and_next_page_without_losing_the_collection(tmp_path):
    _, service = fixture(tmp_path, extra=21)
    first, page = execute(service, "列出所有工程系统设计题，每页10道")
    assert first.plan.action == "LIST" and first.plan.semantic_task == "ENGINEERING_DESIGN"
    assert first.plan.page_size == 10 and len(page["facts"]["data"]) == 10
    next_run, next_page = execute(service, "下一页", conversation_id=first.conversation_id,
        expected_version=page["conversation_version"])
    assert next_run.plan.semantic_task == "ENGINEERING_DESIGN" and next_run.plan.page_size == 10
    assert next_page["facts"]["meta"]["pagination"]["offset"] == 10
    assert len(next_page["facts"]["data"]) == 10
    assert {row["canonical_question_id"] for row in page["facts"]["data"]}.isdisjoint(
        row["canonical_question_id"] for row in next_page["facts"]["data"])


@pytest.mark.parametrize("page_size", [0, 101])
def test_invalid_text_page_size_is_not_silently_clamped(tmp_path, page_size):
    _, service = fixture(tmp_path)
    message = f"列出所有工程系统设计题，每页{page_size}道"
    run = service.begin(QueryRequest(message=message, request_id=str(uuid4())))
    assert service.complete_collection_plan(run) is None
    service.execute(run, "list_questions", QuerySpec(action="LIST"))
    assert run.plan.action == "CLARIFY" and run.result["facts"]["data"] == []
    service.finish(run, provider="fixture")


@pytest.mark.parametrize("limit", [0, 1001])
def test_out_of_range_user_top_n_is_not_silently_discarded_or_truncated(tmp_path, limit):
    _, service = fixture(tmp_path)
    message = f"列出所有工程系统设计题前{limit}道"
    run = service.begin(QueryRequest(message=message, request_id=str(uuid4())))
    assert service.complete_collection_plan(run) is None
    service.execute(run, "list_questions", QuerySpec(action="LIST"))
    assert run.plan.action == "CLARIFY"
    assert run.result["facts"]["data"] == []
    service.finish(run, provider="fixture")


@pytest.mark.parametrize("message", ["列出所有二面的工程系统设计题", "列出所有第二轮的工程系统设计题"])
def test_typed_text_round_qualifier_uses_the_audited_intersection(tmp_path, message):
    _, service = fixture(tmp_path)
    run, response = execute(service, message, plan=QuerySpec(action="LIST",
        semantic_task="ENGINEERING_DESIGN", filters={"round": "SECOND"}))
    assert run.plan.action == "LIST" and run.plan.filters.round == "SECOND"
    assert {row["canonical_question_id"] for row in response["facts"]["data"]} == {"cache", "other"}


def test_typed_stats_dimension_is_not_mistaken_for_an_unaudited_business_qualifier(tmp_path):
    _, service = fixture(tmp_path)
    first, page = execute(service, "列出所有工程系统设计题")
    run, response = execute(service, "这些题按公司统计", plan=QuerySpec(action="STATS",
        semantic_task="ENGINEERING_DESIGN", group_by="company"), conversation_id=first.conversation_id,
        expected_version=page["conversation_version"])
    assert run.plan.action == "STATS" and run.plan.group_by == "company"
    assert {row["key"] for row in response["facts"]["data"]} == {"字节", "腾讯"}


@pytest.mark.parametrize("message,plan", [
    ("列出所有二面的工程系统设计题", QuerySpec(action="LIST", semantic_task="ENGINEERING_DESIGN",
        filters={"round": "FIRST"})),
    ("所有工程系统设计题按公司统计", QuerySpec(action="STATS", semantic_task="ENGINEERING_DESIGN", group_by="round")),
    ("所有工程系统设计题按公司统计，只要排行榜", QuerySpec(action="STATS", semantic_task="ENGINEERING_DESIGN", group_by="company")),
    ("所有公司工程系统设计题", QuerySpec(action="STATS", semantic_task="ENGINEERING_DESIGN", group_by="company")),
])
def test_typed_admission_never_swallows_mismatched_fields_or_unknown_business_limits(tmp_path, message, plan):
    _, service = fixture(tmp_path)
    run, response = execute(service, message, plan=plan)
    assert run.plan.action == "CLARIFY" and response["facts"]["data"] == []


@pytest.mark.parametrize("message", [
    "列出所有传统后端工程系统设计题，排除AI", "列出所有工程系统设计题，只要排行榜",
    "列出所有Agent系统设计题", "工程系统设计题如何解答？",
    "列出所有后端系统设计题", "列出所有业务系统设计题",
])
def test_unknown_qualifier_or_explanation_does_not_take_broad_sql_shortcut(tmp_path, message):
    _, service = fixture(tmp_path)
    run = service.begin(QueryRequest(message=message, request_id=str(uuid4())))
    proposal = service.complete_collection_plan(run)
    if proposal is None:
        service.fail(run)
    else:
        # A host-grounded semantic target can now be admitted without allowing
        # the broad SQL shortcut. Complete unaudited qualifiers still clarify.
        assert proposal.action == "SEARCH" and proposal.semantic_task is None
        service.execute(run, "search_questions", proposal)
        assert run.plan.action == "CLARIFY" and run.result["facts"]["data"] == []
        service.finish(run, provider="fixture")


def test_qualified_full_request_retains_limits_instead_of_returning_broad_collection(tmp_path):
    _, service = fixture(tmp_path)
    run, response = execute(service, "列出所有工程系统设计题，只要排行榜", plan=QuerySpec(
        action="SEARCH", search_query="排行榜"))
    assert run.plan.action == "CLARIFY"
    assert response["facts"]["data"] == []
    assert "限定条件" in response["answer"]


@pytest.mark.parametrize("selection", ["按题意查找候选", "查看完整工程设计集合"])
def test_qualified_collection_clarification_selection_does_not_loop(tmp_path, selection):
    _, service = fixture(tmp_path)
    first, response = execute(service, "列出所有工程系统设计题，只要排行榜", plan=QuerySpec(
        action="SEARCH", search_query="排行榜"))
    next_run = service.begin(QueryRequest(message=selection, request_id=str(uuid4()),
        conversation_id=first.conversation_id, expected_version=response["conversation_version"]))
    from interview_intelligence.agent.query_scope import guard_plan
    name, plan, _ = guard_plan(next_run, "list_questions", QuerySpec(action="LIST"), {})
    assert plan.action == ("SEARCH" if selection == "按题意查找候选" else "LIST")
    assert plan.semantic_task == (None if selection == "按题意查找候选" else "ENGINEERING_DESIGN")
    service.fail(next_run)


@pytest.mark.parametrize("order,expected", [("重要性", "importance"), ("薄弱", "gap"), ("频次", "frequency")])
def test_collection_preserves_requested_order(tmp_path, order, expected):
    _, service = fixture(tmp_path)
    run, _ = execute(service, f"按{order}排序列出所有工程系统设计题")
    assert run.plan.sort == expected


def test_explicit_task_metadata_is_a_same_occurrence_intersection(tmp_path):
    _, service = fixture(tmp_path)
    run, response = execute(service, "列出所有工程系统设计题", filters={"coding_focus": "UNKNOWN"})
    assert run.plan.filters.coding_focus == "UNKNOWN"
    assert {row["canonical_question_id"] for row in response["facts"]["data"]} == {"cache", "orders"}


def test_current_ui_company_replaces_prior_company_without_a_qualifier_loop(tmp_path):
    _, service = fixture(tmp_path)
    first, response = execute(service, "列出所有腾讯工程系统设计题", filters={"company": "腾讯"})
    current, result = execute(service, "一共有多少", filters={"company": "字节跳动"},
        conversation_id=first.conversation_id, expected_version=response["conversation_version"])
    assert current.plan.action == "COUNT"
    assert current.plan.filters.company == "字节"
    assert result["facts"]["meta"]["counts"]["canonical_questions"] == 3


def test_group_stats_next_keeps_group_meaning_and_pending_originals(tmp_path):
    _, service = fixture(tmp_path, complete=False)
    run, first = execute(service, "列出所有工程系统设计题，按公司统计", plan=QuerySpec(
        action="STATS", group_by="company", page_size=1))
    assert "1 个统计分组" in first["answer"]
    assert first["facts"]["data"][0]["key"] == "字节"
    run, second = execute(service, "下一页", conversation_id=run.conversation_id,
        expected_version=first["conversation_version"])
    assert run.plan.group_by == "company"
    assert "1 个统计分组" in second["answer"]
    assert "完整总数尚未确定" in second["answer"]
    pending = second["facts"]["data"][0]
    assert pending["key"] == "腾讯" and pending["semantic_verification"] == "PENDING"
    assert pending["confirmed_occurrence_count"] == 0 and pending["pending_occurrence_count"] == 1
    assert pending["pending_sources"][0]["raw_question"] == "设计一个消息系统"
    assert second["facts"]["meta"]["pagination"]["next_cursor"] is None


def test_missing_audit_is_incomplete_with_coverage_instead_of_successful_empty_list(tmp_path):
    _, service = fixture(tmp_path, complete=False)
    run, result = execute(service, "列出所有工程系统设计题", page_size=2)
    rows = []
    while True:
        rows.extend(result["facts"]["data"])
        assert result["facts"]["meta"]["semantic_scope"]["complete"] is False
        assert result["facts"]["meta"]["semantic_scope"]["missing"] == 1
        assert "完整总数尚未确定" in result["answer"]
        if not result["facts"]["meta"]["pagination"]["next_cursor"]:
            break
        run, result = execute(service, "下一页", conversation_id=run.conversation_id,
            expected_version=result["conversation_version"])
    pending = next(row for row in rows if row["canonical_question_id"] == "other")
    assert pending["semantic_verification"] == "PENDING"
    assert pending["confirmed_occurrence_count"] == 0 and pending["pending_occurrence_count"] == 1
    assert pending["pending_sources"][0]["raw_question"] == "设计一个消息系统"
    counted, count = execute(service, "这些题一共有多少题", conversation_id=run.conversation_id,
        expected_version=result["conversation_version"])
    assert count["facts"]["meta"]["counts"]["canonical_questions"] == 3
    assert count["facts"]["meta"]["pending_counts"]["canonical_questions"] == 1
    assert "已确认 3 道工程系统设计题" in count["answer"]
    assert "完整总数尚未确定" in count["answer"]


def test_actual_api_uses_audited_sql_with_pagination_and_idempotent_receipts(tmp_path):
    db, _ = fixture(tmp_path, extra=61)
    app = create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:", query_prompt_version="query_agent_v15",
        snapshot_root=tmp_path / "snapshots", query_router_enabled=True),
        NoCandidateSearch(), query_planner=NoPlanner())
    client = TestClient(app)
    request = {"message": "列出所有工程系统设计题", "request_id": "api-all", "page_size": 13}
    first = client.post("/api/query", json=request)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["meta"]["pagination"]["result_total"] == 65
    assert body["meta"]["timings"]["model_attempts"] == 0
    assert body["meta"]["semantic_task"] == "ENGINEERING_DESIGN"
    assert client.post("/api/query", json=request).json() == body
    second = client.post("/api/query", json={"message": "下一页", "request_id": "api-next",
        "conversation_id": body["meta"]["conversation_id"], "expected_version": 1})
    assert second.status_code == 200, second.text
    assert second.json()["meta"]["pagination"]["offset"] == 13
    assert second.json()["meta"]["semantic_task"] == "ENGINEERING_DESIGN"
    assert not ({row["canonical_question_id"] for row in body["data"]}
                & {row["canonical_question_id"] for row in second.json()["data"]})
    with db.session() as session:
        turns = list(session.scalars(select(AgentTurn)))
        assert len(turns) == 2 and all(t.status == "SUCCEEDED" for t in turns)


@pytest.mark.parametrize("group_by", [None, "company"])
def test_structured_api_retains_semantic_task_and_grouping_through_next(tmp_path, group_by):
    db, _ = fixture(tmp_path, complete=False)
    app = create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:", query_prompt_version="query_agent_v15",
        snapshot_root=tmp_path / "snapshots", query_router_enabled=True),
        NoCandidateSearch(), query_planner=NoPlanner())
    client = TestClient(app)
    params = {"semantic_task": "ENGINEERING_DESIGN", "page_size": 1}
    if group_by:
        params["group_by"] = group_by
    response = client.post("/api/questions/list", json={"list_request": params, "request_id": "typed-all"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["planning"]["spec"]["semantic_task"] == "ENGINEERING_DESIGN"
    assert body["meta"]["planning"]["spec"]["group_by"] == (group_by or "question")
    assert body["meta"]["semantic_scope"]["complete"] is False
    next_page = client.post("/api/query", json={"message": "下一页", "request_id": "typed-next",
        "conversation_id": body["meta"]["conversation_id"], "expected_version": body["meta"]["conversation_version"]})
    assert next_page.status_code == 200, next_page.text
    meta = next_page.json()["meta"]
    assert meta["planning"]["spec"]["semantic_task"] == "ENGINEERING_DESIGN"
    assert meta["planning"]["spec"]["group_by"] == (group_by or "question")
    assert meta["pagination"]["offset"] == 1
    assert "完整总数尚未确定" in meta["answer"]


def test_api_missing_audit_reports_coverage_and_never_invokes_provider(tmp_path):
    db, _ = fixture(tmp_path, complete=False)
    app = create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:", query_prompt_version="query_agent_v15",
        snapshot_root=tmp_path / "snapshots", query_router_enabled=True),
        NoCandidateSearch(), query_planner=NoPlanner())
    response = TestClient(app).post("/api/query", json={
        "message": "列出所有工程系统设计题", "request_id": "api-unaudited"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["semantic_scope"]["missing"] == 1
    assert body["meta"]["semantic_scope"]["complete"] is False
    assert body["meta"]["timings"]["model_attempts"] == 0
    assert next(row for row in body["data"] if row["canonical_question_id"] == "other")["semantic_verification"] == "PENDING"
    assert "完整总数尚未确定" in body["meta"]["answer"]
