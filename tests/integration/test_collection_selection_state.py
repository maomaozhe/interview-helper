"""Independent state-machine checks for trusted collection browse choices."""
from datetime import date
from uuid import uuid4

import pytest

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.domain.models import AgentConversation, CorpusState, QuestionOccurrence
from test_collection_category_listing import category_corpus
from test_complete_engineering_query import NoCandidateSearch, execute


def service_fixture(tmp_path):
    db = category_corpus()
    service = QueryService(db, NoCandidateSearch(), signing_key="selection-state",
        today=lambda: date(2026, 10, 16), feedback_root=tmp_path / "feedback",
        collection_selection_threshold=1)
    return db, service


def follow(service, message, previous, **changes):
    return execute(service, message, conversation_id=previous["conversation_id"],
        expected_version=previous["conversation_version"], **changes)


def meta(result):
    return result["facts"]["meta"]


def test_changed_revision_requires_fresh_choice_and_then_all_is_not_reasked(tmp_path):
    db, service = service_fixture(tmp_path)
    _, first = execute(service, "列出所有工程系统设计题")
    with db.session() as session, session.begin():
        session.get(CorpusState, 1).task_annotation_revision += 1
    _, refreshed = follow(service, "全量分页", first)
    assert refreshed["intent"] == "CLARIFY"
    assert "已更新" in refreshed["answer"]
    assert meta(refreshed)["collection_selection"]["stage"] == "scope"
    _, selected = follow(service, "全量分页", refreshed, page_size=2)
    assert selected["intent"] == "LIST"
    _, next_page = follow(service, "下一页", selected)
    assert next_page["intent"] == "NEXT"
    assert not ({row["canonical_question_id"] for row in selected["facts"]["data"]}
        & {row["canonical_question_id"] for row in next_page["facts"]["data"]})


def test_changed_source_topic_rebuilds_choice_without_using_old_membership(tmp_path):
    db, service = service_fixture(tmp_path)
    _, first = execute(service, "列出所有工程系统设计题")
    _, menu = follow(service, "按分类浏览", first)
    ai_choice = next(row for row in meta(menu)["collection_selection"]["choices"] if row["label"] == "AI")
    with db.session() as session, session.begin():
        # Deliberately do not bump corpus/annotation revision: the facet binding
        # itself must invalidate a stale menu.
        session.get(QuestionOccurrence, "o-003").topic_id = "redis.cache_problems"
    _, refreshed = follow(service, ai_choice["message"], menu)
    assert refreshed["intent"] == "CLARIFY"
    assert refreshed["facts"]["data"] == []
    assert "已更新" in refreshed["answer"]
    _, menu = follow(service, "按分类浏览", refreshed)
    ai_choice = next(row for row in meta(menu)["collection_selection"]["choices"] if row["label"] == "AI")
    _, ai = follow(service, ai_choice["message"], menu)
    assert {row["canonical_question_id"] for row in ai["facts"]["data"]} == {"agent"}


def test_requery_selected_all_restarts_first_page_without_losing_consent(tmp_path):
    _, service = service_fixture(tmp_path)
    _, first = execute(service, "列出所有工程系统设计题", page_size=2)
    _, selected = follow(service, "全量分页", first)
    _, second = follow(service, "下一页", selected)
    _, replay = execute(service, "重查", requery_of_run_id=second["run_id"])
    assert replay["intent"] == "LIST"
    assert meta(replay)["collection_selection"]["stage"] == "selected"
    assert meta(replay)["pagination"]["offset"] == 0
    assert [row["canonical_question_id"] for row in replay["facts"]["data"]] == [
        row["canonical_question_id"] for row in selected["facts"]["data"]]


def test_competing_choice_cannot_commit_against_old_conversation_version(tmp_path):
    _, service = service_fixture(tmp_path)
    _, first = execute(service, "列出所有工程系统设计题")
    follow(service, "全量分页", first)
    with pytest.raises(ValueError, match="CONVERSATION_VERSION_CONFLICT"):
        service.begin(QueryRequest(message="按分类浏览", request_id=str(uuid4()),
            conversation_id=first["conversation_id"], expected_version=first["conversation_version"]))


def test_selection_preview_revokes_old_page_detail_and_write_capabilities(tmp_path):
    _, service = service_fixture(tmp_path)
    _, small = execute(service, "列出所有工程系统设计题前1道")
    run, large = follow(service, "列出所有工程系统设计题", small)
    assert large["intent"] == "CLARIFY"
    assert run.state["current_page_ids"] == []
    assert "list_request" not in run.state
    next_run = service.begin(QueryRequest(message="标记当前页已掌握", request_id=str(uuid4()),
        conversation_id=large["conversation_id"], expected_version=large["conversation_version"]))
    policy = service.tool_policy(next_run)
    assert "record_review" not in policy.actions
    assert "get_question_details" not in policy.actions
    service.fail(next_run, ValueError("TEST_CAPABILITY_CHECK_FINISHED"))


def test_changed_count_scope_does_not_leave_old_collection_choice_active(tmp_path):
    db, service = service_fixture(tmp_path)
    _, first = execute(service, "列出所有工程系统设计题", filters={"company": "字节"})
    _, selected = follow(service, "全量分页", first)
    _, counted = follow(service, "这些题一共有多少题", selected, filters={"company": "腾讯"})
    assert counted["intent"] == "COUNT" and meta(counted)["applied_filters"]["company"] == "腾讯"
    with db.session() as session:
        state = session.get(AgentConversation, counted["conversation_id"]).state
        saved = state.get("collection_selection")
        assert not saved or saved["root_plan"]["filters"]["company"] == "腾讯", (
            "The next explicit all choice must not resurrect the old Byte scope")


def test_selected_all_group_statistics_next_keeps_same_canonical_scope(tmp_path):
    _, service = service_fixture(tmp_path)
    _, first = execute(service, "列出所有工程系统设计题")
    _, selected = follow(service, "全量分页", first)
    _, stats = follow(service, "这些题按公司统计", selected, plan=QuerySpec(action="STATS",
        semantic_task="ENGINEERING_DESIGN", group_by="company", page_size=1))
    assert stats["intent"] == "STATS" and meta(stats)["pagination"]["next_cursor"]
    _, second = follow(service, "下一页", stats)
    assert second["intent"] == "NEXT"
    assert {row["key"] for row in stats["facts"]["data"] + second["facts"]["data"]} == {"字节", "腾讯"}
    assert meta(second)["group_by"] == "company"

def test_selected_category_group_statistics_next_retains_category_union(tmp_path):
    db, service = service_fixture(tmp_path)
    from interview_intelligence.domain.models import Interview
    with db.session() as session, session.begin():
        session.get(Interview, "i-8").company_normalized = "腾讯"
    _, first = execute(service, "列出所有工程系统设计题")
    _, menu = follow(service, "按分类浏览", first)
    choice = next(row for row in meta(menu)["collection_selection"]["choices"] if row["label"] == "计算机基础")
    _, selected = follow(service, choice["message"], menu)
    assert {row["canonical_question_id"] for row in selected["facts"]["data"]} == {"extra-000", "extra-001"}
    _, stats = follow(service, "这些题按公司统计", selected, plan=QuerySpec(action="STATS",
        semantic_task="ENGINEERING_DESIGN", group_by="company", page_size=1))
    _, second = follow(service, "下一页", stats)
    rows = stats["facts"]["data"] + second["facts"]["data"]
    assert {row["key"] for row in rows} == {"字节", "腾讯"}
    assert sum(row["confirmed_occurrence_count"] for row in rows) == 2
    assert all(row["pending_occurrence_count"] == 0 for row in rows)
    assert meta(second)["semantic_counts"]["selected_canonical_questions"] == 2
    assert meta(second)["collection_selection"]["selected"]["label"] == "计算机基础"


def test_model_cannot_convert_an_unanswered_menu_into_full_consent(tmp_path):
    _, service = service_fixture(tmp_path)
    _, first = execute(service, "列出所有工程系统设计题")
    _, proposed = follow(service, "继续", first, plan=QuerySpec(action="LIST",
        semantic_task="ENGINEERING_DESIGN", filters={"topic_l1": "AI"}, top_n=1))
    assert proposed["intent"] == "CLARIFY" and proposed["facts"]["data"] == []
    assert meta(proposed)["collection_selection"]["stage"] == "scope"
    assert meta(proposed)["collection_selection"]["counts"]["visible"] == 9


def test_unrecognized_category_choice_cannot_fall_back_to_unscoped_sql(tmp_path):
    _, service = service_fixture(tmp_path)
    _, first = execute(service, "列出所有工程系统设计题")
    _, menu = follow(service, "按分类浏览", first)
    _, result = follow(service, "查看分类：不存在", menu, plan=QuerySpec(action="LIST",
        semantic_task="ENGINEERING_DESIGN"))
    assert result["intent"] == "CLARIFY"
    assert result["facts"]["data"] == []
    assert meta(result)["collection_selection"]["stage"] in {"scope", "categories"}

def test_typed_engineering_request_authorizes_later_natural_count(tmp_path):
    _, service = service_fixture(tmp_path)
    from interview_intelligence.agent.query_contract import StructuredQueryRequest
    from interview_intelligence.analytics.listing import ListRequest
    params = ListRequest(semantic_task="ENGINEERING_DESIGN", page_size=2)
    run = service.begin(StructuredQueryRequest(message="结构化列表操作", request_id=str(uuid4()), list_request=params))
    service.execute(run, "list_questions", QuerySpec(action="LIST", semantic_task="ENGINEERING_DESIGN", page_size=2))
    typed = service.finish(run, provider="structured-test")
    assert meta(typed)["pagination"]["result_total"] == 9
    _, counted = follow(service, "这些题一共有多少题", typed,
        plan=QuerySpec(action="COUNT", semantic_task="ENGINEERING_DESIGN"))
    assert counted["planning"]["spec"]["semantic_task"] == "ENGINEERING_DESIGN"
    assert meta(counted)["counts"]["canonical_questions"] == 8
    assert meta(counted)["pending_counts"]["canonical_questions"] == 1
