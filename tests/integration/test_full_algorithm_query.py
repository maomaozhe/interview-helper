"""Host invariants for the complete algorithm collection and follow-up turns.

Plans here stand in for validated model tool calls. Live model selection is
verified separately; these tests keep SQL scope, context, counts and cursors
independent of provider availability and the 50-candidate search limit.
"""
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.domain.models import AgentConversation, AgentTurn, ToolInvocation
from test_semantic_scope_listing import seed_semantic_corpus


TASK = "ALGORITHM_PROBLEM"
FIRST = "字节跳动算法题，按照出现频率排名"
FOLLOW_UP = "我要所有的算法题，字节出现过的"


class NoCandidateRetrieval:
    """A complete list must not quietly revert to a search candidate sample."""
    def retrieve(self, *args, **kwargs):
        raise AssertionError("Full algorithm scope must use SQL, never candidate retrieval")


def service_fixture(tmp_path, *, extra=0):
    db = seed_semantic_corpus(extra=extra)
    return QueryService(db, NoCandidateRetrieval(), signing_key="complete-scope-test",
        today=lambda: date(2026, 10, 14), feedback_root=tmp_path / "feedback")


def algorithm_plan(*, action="LIST", page_size=20, **changes):
    return QuerySpec(action=action, semantic_task=TASK,
        filters={"company": "字节"}, sort="frequency", top_n=None,
        page_size=page_size, **changes)


def complete(service, request, plan, name="list_questions"):
    run = service.begin(request)
    service.execute(run, name, plan)
    response = service.finish(run, provider="fixture")
    return run, response


def assert_complete_scope(response, *, canonical, occurrences, returned):
    facts = response["facts"]
    meta = facts["meta"]
    assert response["planning"]["spec"]["semantic_task"] == TASK
    assert response["planning"]["spec"]["filters"]["company"] == "字节"
    assert response["planning"]["spec"]["top_n"] is None
    assert meta["semantic_task"] == TASK
    assert meta["semantic_scope"]["complete"] is True
    assert meta["semantic_scope"]["accepted"] == occurrences
    assert meta["applied_filters"]["company"] == "字节"
    assert meta["applied_filters"]["coding_focus"] is None
    assert meta["applied_filters"]["annotation_status"] is None
    assert meta["pagination"]["total"] == canonical
    assert meta["pagination"]["result_total"] == canonical
    assert meta["sample_counts"]["selected_occurrences"] == occurrences
    assert len(facts["data"]) == returned
    assert "最多 50" not in response["answer"]
    assert not any("最多 50" in warning for warning in response["warnings"])


def test_first_frequency_request_lists_every_audited_algorithm_and_only_algorithm_occurrences(tmp_path):
    service = service_fixture(tmp_path)
    run, response = complete(service, QueryRequest(message=FIRST, request_id="all-first"),
        algorithm_plan(page_size=1))
    assert response["intent"] == "LIST"
    assert_complete_scope(response, canonical=2, occurrences=3, returned=1)
    row = response["facts"]["data"][0]
    # c-main also has two non-algorithm occurrences; they cannot inflate rank.
    assert row["canonical_question_id"] == "c-main"
    assert row["occurrence_count"] == 2
    assert row["matched_question"] == "手撕算法：实现 O(1) LRU"
    assert "2 道去重算法题" in response["answer"]
    assert "3 次算法提问" in response["answer"]
    assert run.state["list_request"]["semantic_task"] == TASK
    with service.database.session() as session:
        receipt = session.get(AgentTurn, run.id)
        assert receipt.status == "SUCCEEDED"
        assert receipt.state_after["last_plan"]["semantic_task"] == TASK
        assert receipt.state_after["conversation_focus"]["semantic_task"] == TASK


def test_model_catalog_exposes_only_fully_audited_company_collections(tmp_path):
    service = service_fixture(tmp_path)
    catalog = service._filter_catalog()
    assert set(catalog["companies"]) == {"字节", "腾讯"}
    assert catalog["full_algorithm_companies"] == ["字节"]


def test_all_algorithm_follow_up_preserves_company_semantic_scope_and_removes_top_n(tmp_path):
    service = service_fixture(tmp_path, extra=8)
    # A historical model may have imposed a small Top N. "All" must begin a
    # complete set instead of keeping that previous cap in the saved cursor.
    first, response = complete(service, QueryRequest(message=FIRST, request_id="follow-first"),
        algorithm_plan(page_size=2).model_copy(update={"top_n": 2}))
    assert first.state["list_request"]["top_n"] == 2
    request = QueryRequest(message=FOLLOW_UP, request_id="follow-all",
        conversation_id=first.conversation_id, expected_version=response["conversation_version"])
    follow = service.begin(request)
    context = service.model_context(follow)
    assert context["session"]["last_plan"]["semantic_task"] == TASK
    assert context["session"]["last_plan"]["top_n"] == 2
    assert context["session"]["conversation_focus"]["semantic_task"] == TASK
    assert context["session"]["conversation_focus"]["filters"]["company"] == "字节"
    assert context["session"]["list_request"]["semantic_task"] == TASK
    service.execute(follow, "list_questions", algorithm_plan(page_size=3))
    result = service.finish(follow, provider="fixture")
    assert result["conversation_id"] == response["conversation_id"]
    assert result["planning"]["spec"]["action"] == "LIST"
    assert_complete_scope(result, canonical=10, occurrences=11, returned=3)
    assert result["facts"]["meta"]["pagination"]["offset"] == 0
    assert follow.state["list_request"]["top_n"] is None
    assert follow.state["list_request"]["semantic_task"] == TASK


def test_next_paginates_full_corpus_beyond_fifty_with_no_duplicates_or_omissions(tmp_path):
    service = service_fixture(tmp_path, extra=63)
    run, response = complete(service, QueryRequest(message=FIRST, request_id="pages-first"),
        algorithm_plan(page_size=13))
    ids, ranks, page_number = [], [], 0
    while True:
        page_number += 1
        assert_complete_scope(response, canonical=65, occurrences=66,
            returned=min(13, 65 - len(ids)))
        rows = response["facts"]["data"]
        ids.extend(row["canonical_question_id"] for row in rows)
        ranks.extend(row["rank"] for row in rows)
        if not response["facts"]["meta"]["pagination"]["next_cursor"]:
            break
        # Default NEXT has no semantic_task or filters. The host must recover
        # them from its saved signed scope rather than a model's guesses.
        run, response = complete(service, QueryRequest(message="下一页",
            request_id=f"pages-next-{page_number}", conversation_id=run.conversation_id,
            expected_version=response["conversation_version"]), QuerySpec(action="NEXT"))
        assert response["planning"]["spec"]["action"] == "NEXT"
    assert page_number == 5
    assert len(ids) == len(set(ids)) == 65
    assert set(ids) == {"c-main", "c-second", *[f"c-extra-{i:03}" for i in range(63)]}
    assert ranks == list(range(1, 66))
    assert run.state["list_request"]["cursor"] is None
    assert run.state["conversation_focus"]["semantic_task"] == TASK


def test_count_counts_entire_semantic_scope_after_one_displayed_question(tmp_path):
    service = service_fixture(tmp_path, extra=63)
    first, response = complete(service, QueryRequest(message=FIRST, request_id="count-first"),
        algorithm_plan(page_size=1))
    run, counted = complete(service, QueryRequest(message="字节出现过的算法题一共多少？",
        request_id="count-all", conversation_id=first.conversation_id,
        expected_version=response["conversation_version"]), algorithm_plan(action="COUNT"),
        name="get_question_count")
    meta = counted["facts"]["meta"]
    assert counted["intent"] == "COUNT"
    assert meta["count_scope"] == "filtered_corpus"
    assert meta["semantic_task"] == TASK
    assert meta["semantic_scope"]["complete"] is True
    assert meta["counts"] == {"canonical_questions": 65, "occurrences": 66,
        "interviews": 66, "source_documents": 66, "known_companies": 1}
    assert "65 道去重题目" in counted["answer"]
    assert "66 条提问记录" in counted["answer"]
    assert run.state["last_count"]["semantic_task"] == TASK
    continued = service.begin(QueryRequest(message="再列出所有题", request_id="after-count",
        conversation_id=run.conversation_id, expected_version=counted["conversation_version"]))
    context = service.model_context(continued)
    assert context["session"]["conversation_focus"]["intent"] == "COUNT"
    assert context["session"]["conversation_focus"]["semantic_task"] == TASK
    service.fail(continued)


def seed_historical_unverified_search(service):
    old_plan = QuerySpec(action="SEARCH", filters={"company": "字节"},
        search_query="算法 手撕 数组 链表", relevance_query="字节出现过的所有算法题",
        sort="frequency").model_dump(mode="json")
    after = {"filters": old_plan["filters"], "sort": "frequency", "last_plan": old_plan,
        "search_ranking": "frequency", "current_page_ids": [],
        "last_response": {"message": FIRST, "intent": "SEARCH", "answer": "相关性核验未完成，请重试。"}}
    # The observed incident was persisted as SUCCEEDED despite unavailable
    # semantic results. Requery must refresh the goal, not replay this answer.
    response = {"intent": "SEARCH", "answer": "相关性核验未完成，请重试。",
        "planning": {"spec": old_plan}, "tool_trace": [], "warnings": [],
        "facts": {"data": [], "meta": {"relevance_status": "UNAVAILABLE",
            "rerank_status": "SKIPPED_BUDGET", "applied_filters": old_plan["filters"]}}}
    with service.database.session() as session, session.begin():
        conversation = AgentConversation(user_id=service.user_id, state=after, version=1)
        session.add(conversation)
        session.flush()
        turn = AgentTurn(user_id=service.user_id, conversation_id=conversation.id,
            request_id="historical-search", payload_hash="historical-fixture", message=FIRST,
            status="SUCCEEDED", response=response, state_after=after,
            request_payload={"filters": {}, "page_size": 20,
                "pipeline": "HYBRID_RERANK", "resolved_explicit_filters": {}})
        session.add(turn)
        session.flush()
        return turn.id, conversation.id


def test_requery_historical_unverified_search_can_refresh_goal_as_complete_list(tmp_path):
    service = service_fixture(tmp_path)
    source_id, old_conversation = seed_historical_unverified_search(service)
    refresh = service.begin(QueryRequest(message="重新检索", request_id="refresh-complete",
        requery_of_run_id=source_id))
    context = service.model_context(refresh)
    assert context["requery_origin"]["action"] == "SEARCH"
    assert context["requery_origin"]["original_message"] == FIRST
    assert context["session"]["last_plan"]["filters"]["company"] == "字节"
    assert context["session"]["last_plan"]["sort"] == "frequency"
    assert "current_page_ids" not in context["session"]
    service.execute(refresh, "list_questions", algorithm_plan(page_size=1))
    result = service.finish(refresh, provider="fixture")
    assert result["conversation_id"] != old_conversation
    assert result["run_id"] != source_id
    assert result["facts"]["meta"]["requery_of_run_id"] == source_id
    assert result["planning"]["spec"]["action"] == "LIST"
    assert_complete_scope(result, canonical=2, occurrences=3, returned=1)
    assert "核验未完成" not in result["answer"]
    assert refresh.state["conversation_focus"]["semantic_task"] == TASK


@pytest.mark.parametrize("filters", [
    {"coding_focus": "ALGORITHM"}, {"annotation_status": "KNOWN"},
    {"company": "腾讯"},
])
def test_explicit_ui_filters_cannot_be_silently_discarded_for_complete_scope(tmp_path, filters):
    service = service_fixture(tmp_path)
    run = service.begin(QueryRequest(message=FIRST, request_id=str(uuid4()), filters=filters))
    assert service.model_context(run)["explicit_filters"] == filters
    with pytest.raises(ValueError, match="EXPLICIT_FILTER_CONFLICT"):
        service.execute(run, "list_questions", algorithm_plan())
    with service.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ToolInvocation).where(
            ToolInvocation.run_id == run.id)) == 0
    service.fail(run)


@pytest.mark.parametrize("filters", [
    {"company": "字节", "coding_focus": "ALGORITHM"},
    {"company": "字节", "annotation_status": "KNOWN"},
])
def test_model_cannot_combine_semantic_scope_and_legacy_task_labels(filters):
    with pytest.raises(ValueError, match="SEMANTIC_SCOPE_LEGACY_TASK_FILTER_CONFLICT"):
        QuerySpec(action="LIST", semantic_task=TASK, filters=filters)
