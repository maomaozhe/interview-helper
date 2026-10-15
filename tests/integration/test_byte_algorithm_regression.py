"""Company aliases and task labels survive SQL, pagination and old empty lists."""
from copy import deepcopy

import pytest
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    AgentConversation, AgentTurn, CanonicalQuestion, OccurrenceTaskAnnotation,
    QuestionOccurrence,
)
from test_analytics import seed_corpus
from test_query_metadata_scope import assert_scope


def algorithm_corpus():
    db, algorithm_id, mixed_id = seed_corpus()
    with db.session() as session, session.begin():
        for question in session.scalars(select(CanonicalQuestion)):
            question.canonical_text = ("用堆求第 k 大元素" if question.id == algorithm_id
                                       else "实现支持排名的计数结构")
        for occurrence in session.scalars(select(QuestionOccurrence)):
            question = session.get(CanonicalQuestion, occurrence.canonical_question_id)
            occurrence.raw_question = occurrence.normalized_question = question.canonical_text
            occurrence.source_spans = [{"quote": question.canonical_text}]
            session.add(OccurrenceTaskAnnotation(
                occurrence_id=occurrence.id,
                response_form="VERBAL" if question.id == algorithm_id else "CODE",
                coding_focus="ALGORITHM" if question.id == algorithm_id else "MIXED",
                classification_status="NEEDS_REVIEW", producer_version="fixture",
            ))
    return db, algorithm_id, mixed_id


@pytest.mark.parametrize("company", ["字节跳动", "字节", "抖音"])
def test_algorithm_tasks_include_mixed_and_do_not_require_legacy_algorithm_labels(company):
    db, algorithm_id, mixed_id = algorithm_corpus()
    assert_scope(db, FilterSpec(company=company, coding_focus="ALGORITHM",
                               annotation_status="KNOWN"), {algorithm_id, mixed_id}, 2)


def test_explicit_code_round_and_legacy_category_constraints_remain_effective():
    db, _, mixed_id = algorithm_corpus()
    assert_scope(db, FilterSpec(company="字节跳动", coding_focus="ALGORITHM",
                               response_form="CODE", round="SECOND"), {mixed_id}, 1)
    # An explicitly selected legacy category is still a real intersection.
    assert_scope(db, FilterSpec(company="字节跳动", coding_focus="ALGORITHM",
                               question_type="ALGORITHM"), set(), 0)


def test_repeating_a_previous_empty_algorithm_list_recovers_and_paginates():
    db, algorithm_id, mixed_id = algorithm_corpus()
    service = QueryService(db, signing_key="test", task_annotation_policy="KNOWN")
    old = service.begin(QueryRequest(message="字节跳动算法题", request_id="empty-old"))
    service.execute(old, "list_questions", QuerySpec(action="LIST", filters={
        "company": "字节跳动", "topic_l1": "算法", "question_type": "ALGORITHM"}))
    service.finish(old, provider="fixture")
    with db.session() as session:
        row = session.get(AgentTurn, old.id)
        original = deepcopy((row.response, row.state_before, row.state_after))
        version = session.get(AgentConversation, old.conversation_id).version
    fresh = service.begin(QueryRequest(message="字节算法题", request_id="repeat-new",
        conversation_id=old.conversation_id, expected_version=version))
    context = service.model_context(fresh)
    assert context["filter_catalog"]["company_aliases"]["字节跳动"] == "字节"
    assert context["explicit_filters"] == {}
    response = service.execute(fresh, "list_questions", QuerySpec(action="LIST", page_size=1,
        filters={"company": "字节跳动", "coding_focus": "ALGORITHM"}))
    service.finish(fresh, provider="fixture")
    assert response["facts"]["meta"]["pagination"]["result_total"] == 2
    first = response["facts"]["data"][0]["canonical_question_id"]
    with db.session() as session:
        version = session.get(AgentConversation, old.conversation_id).version
        row = session.get(AgentTurn, old.id)
        assert (row.response, row.state_before, row.state_after) == original
    next_run = service.begin(QueryRequest(message="下一页", request_id="next-page",
        conversation_id=old.conversation_id, expected_version=version))
    page = service.execute(next_run, "list_questions", QuerySpec(action="NEXT"))
    service.finish(next_run, provider="fixture")
    second = page["facts"]["data"][0]["canonical_question_id"]
    assert {first, second} == {algorithm_id, mixed_id}
    assert page["facts"]["meta"]["pagination"]["result_total"] == 2


def test_history_requery_discards_only_model_derived_algorithm_intersections():
    db, algorithm_id, mixed_id = algorithm_corpus()
    service = QueryService(db, signing_key="test", task_annotation_policy="KNOWN")
    old = service.begin(QueryRequest(message="字节跳动算法题", request_id="requery-old"))
    service.execute(old, "list_questions", QuerySpec(action="LIST", filters={
        "company": "字节跳动", "topic_l1": "算法", "question_type": "ALGORITHM"}))
    service.finish(old, provider="fixture")
    refreshed = service.begin(QueryRequest(message="字节跳动算法题", request_id="requery-new",
                                           requery_of_run_id=old.id))
    context = service.model_context(refreshed)
    assert context["requery_origin"]["original_message"] == "字节跳动算法题"
    assert context["session"]["filters"]["topic_l1"] is None
    assert context["session"]["list_request"]["question_type"] is None
    assert context["session"]["last_plan"]["filters"]["question_type"] == "ALGORITHM"
    assert refreshed.state["filters"]["topic_l1"] == "算法"
    response = service.execute(refreshed, "list_questions", QuerySpec(action="LIST",
        filters={"company": "字节跳动", "coding_focus": "ALGORITHM"}))
    assert {row["canonical_question_id"] for row in response["facts"]["data"]} == {algorithm_id, mixed_id}


def test_history_requery_preserves_user_selected_algorithm_topic():
    db, _, _ = algorithm_corpus()
    service = QueryService(db, signing_key="test")
    old = service.begin(QueryRequest(message="字节跳动算法题", request_id="ui-old",
                                     filters={"topic_l1": "算法"}))
    service.execute(old, "list_questions", QuerySpec(action="LIST", filters={
        "company": "字节跳动", "topic_l1": "算法", "coding_focus": "ALGORITHM"}))
    service.finish(old, provider="fixture")
    refreshed = service.begin(QueryRequest(message="字节跳动算法题", request_id="ui-requery",
                                           requery_of_run_id=old.id))
    context = service.model_context(refreshed)
    assert context["explicit_filters"] == {"topic_l1": "算法"}
    assert context["session"]["filters"]["topic_l1"] == "算法"
    with pytest.raises(ValueError, match="EXPLICIT_FILTER_CONFLICT"):
        service.execute(refreshed, "list_questions", QuerySpec(action="LIST", filters={
            "company": "字节跳动", "coding_focus": "ALGORITHM"}))
