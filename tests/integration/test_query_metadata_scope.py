"""Query execution preserves the scope selected by the planner and UI."""

import pytest
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.analytics.counts import count_questions
from interview_intelligence.analytics.listing import ListRequest, list_questions
from interview_intelligence.analytics.stats import query_question_stats
from interview_intelligence.contracts import FilterSpec, StatsRequest
from interview_intelligence.domain.models import Interview
from interview_intelligence.search.service import eligible_canonical_ids
from test_analytics import seed_corpus


def position_corpus(exact_position="Java后端实习生"):
    db, first_id, second_id = seed_corpus()
    with db.session() as session, session.begin():
        for interview in session.scalars(select(Interview)):
            interview.job_family = "BACKEND"
            if interview.company_normalized == "字节" and interview.round == "FIRST":
                interview.position_normalized = exact_position
                # A literal job title must not infer a language constraint.
                interview.language_tags = []
            else:
                interview.position_normalized = "Java服务端工程师"
                interview.language_tags = ["JAVA"]
    return db, first_id, second_id


def assert_scope(db, filters, expected_ids, expected_occurrences):
    values = filters.model_dump()
    with db.session() as session:
        count = count_questions(session, filters)
        listing = list_questions(session, ListRequest(**values), signing_key="test")
        stats = query_question_stats(session, StatsRequest(**values))
        eligible = eligible_canonical_ids(session, filters)
    assert count["canonical_questions"] == len(expected_ids)
    assert count["occurrences"] == expected_occurrences
    for rows in (listing["data"], stats["data"]):
        assert {row["canonical_question_id"] for row in rows} == expected_ids
        assert sum(row["occurrence_count"] for row in rows) == expected_occurrences
    assert set(eligible) == expected_ids


@pytest.mark.parametrize("position", ["Java后端实习生", "Java服务端实习生", "Java后台实习生"])
def test_exact_position_does_not_expand_to_a_family_or_infer_language(position):
    db, first_id, _ = position_corpus(position)
    assert_scope(db, FilterSpec(position=position), {first_id}, 1)


def test_explicit_family_and_language_work_without_an_exact_position():
    db, first_id, second_id = position_corpus()
    assert_scope(db, FilterSpec(job_family="BACKEND", language="JAVA"), {first_id, second_id}, 2)


def test_exact_position_and_explicit_language_remain_intersected():
    db, _, _ = position_corpus()
    assert_scope(db, FilterSpec(position="Java后端实习生", language="JAVA"), set(), 0)


@pytest.mark.parametrize("company", ["字节跳动", "抖音"])
def test_company_alias_reaches_list_count_stats_and_search_eligibility(company):
    db, first_id, second_id = position_corpus()
    assert_scope(db, FilterSpec(company=company), {first_id, second_id}, 2)


def test_planner_receives_actual_company_and_position_values_with_aliases():
    db, _, _ = position_corpus()
    service = QueryService(db, signing_key="test")
    run = service.begin(QueryRequest(message="字节跳动的 Java 后端题", request_id="catalog"))
    catalog = service.model_context(run)["filter_catalog"]
    assert catalog["companies"] == ["字节", "腾讯"]
    assert catalog["positions"] == ["Java后端实习生", "Java服务端工程师"]
    assert catalog["company_aliases"]["字节跳动"] == "字节"
    assert catalog["companies_complete"] and catalog["positions_complete"]


def test_history_requery_exposes_user_goal_and_allows_correcting_model_derived_position():
    db, first_id, second_id = position_corpus()
    service = QueryService(db, signing_key="test")
    message = "Java 后端方向的面试题"
    old = service.begin(QueryRequest(message=message, request_id="old-position"))
    service.execute(old, "list_questions", QuerySpec(action="LIST", filters={"position": "Java后端实习生"}))
    service.finish(old, provider="fixture")
    refreshed = service.begin(QueryRequest(message=message, request_id="requery-position", requery_of_run_id=old.id))
    context = service.model_context(refreshed)
    assert context["requery_origin"]["original_message"] == message
    assert context["explicit_filters"] == {}
    assert context["session"]["filters"]["position"] is None
    assert context["session"]["list_request"]["position"] is None
    assert context["session"]["last_plan"]["filters"]["position"] == "Java后端实习生"
    assert refreshed.state["filters"]["position"] == "Java后端实习生"
    corrected = service.execute(refreshed, "list_questions",
        QuerySpec(action="LIST", filters={"job_family": "BACKEND", "language": "JAVA"}))
    assert corrected["facts"]["meta"]["applied_filters"]["position"] is None
    assert {row["canonical_question_id"] for row in corrected["facts"]["data"]} == {first_id, second_id}
