"""Canonical collection categories retain unknown and pending source records."""
from copy import deepcopy
from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from interview_intelligence.analytics.engineering_categories import engineering_collection_categories
from interview_intelligence.analytics.listing import ListRequest, StatsListRequest, list_questions
from interview_intelligence.analytics.scope import verify_scope
from interview_intelligence.analytics.semantic_scope import ENGINEERING_DESIGN_TASK as TASK
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    CorpusState, Interview, OccurrenceTaskAnnotation, QuestionOccurrence, UserQuestionState,
)
from test_engineering_scope_listing import engineering_corpus


KEY = "category-scope-test"
AS_OF = date(2026, 10, 16)


def category_corpus():
    db = engineering_corpus(extra=5)
    with db.session() as session, session.begin():
        topics = {"o-001": "redis.cache_problems", "o-002": "database.architecture",
            "o-003": "ai.agent", "o-004": "ai.rag", "o-007": "fundamentals.network",
            "o-008": "fundamentals.operating_systems", "o-009": "other.unknown",
            "o-010": "not-a-current-topic", "o-011": "ai.agent"}
        for oid, topic in topics.items():
            session.get(QuestionOccurrence, oid).topic_id = topic
        session.get(QuestionOccurrence, "o-011").taxonomy_version = "old-version"
    return db


def collection(db, **changes):
    with db.session() as session:
        return engineering_collection_categories(session, FilterSpec(**changes))


def listing(db, **changes):
    with db.session() as session:
        return list_questions(session, ListRequest(semantic_task=TASK, **changes),
            signing_key=KEY, user_id="learner", as_of=AS_OF)


def bucket_id(db, label):
    return next(row["id"] for row in collection(db)["categories"] if row["label"] == label)


def test_category_pages_are_disjoint_and_union_equals_full_collection():
    db = category_corpus()
    root = listing(db)
    expected = {row["canonical_question_id"] for row in root["data"]}
    preview = collection(db)
    seen, categories = set(), []
    assert preview["counts"]["visible"] == len(expected) == 9
    assert {row["kind"] for row in preview["categories"]} == {"TOPIC_L1", "UNCLEAR", "PENDING"}
    for category in preview["categories"]:
        ids, ranks, cursor = [], [], None
        while True:
            page = listing(db, collection_category=category["id"], page_size=1, cursor=cursor)
            meta = page["meta"]
            assert meta["pagination"]["total"] == meta["pagination"]["result_total"] == len(category["canonical_ids"])
            assert meta["collection_category"]["root_counts"] == preview["counts"]
            assert meta["collection_category"]["counts"] == category["counts"]
            assert meta["semantic_counts_scope"] == "selected_collection_category_canonical_questions"
            assert meta["semantic_counts"]["selected_canonical_questions"] == len(category["canonical_ids"])
            assert meta["semantic_counts"]["selected_occurrences"] == category["counts"]["visible_occurrences"]
            assert meta["sample_counts"]["occurrences"] == category["counts"]["confirmed_occurrences"]
            ids += [row["canonical_question_id"] for row in page["data"]]
            ranks += [row["rank"] for row in page["data"]]
            cursor = meta["pagination"]["next_cursor"]
            if not cursor:
                break
        assert ranks == list(range(1, len(ids) + 1))
        assert set(ids) == set(category["canonical_ids"])
        assert not seen.intersection(ids)
        seen.update(ids)
        categories.append(category["id"])
    assert seen == expected
    assert sum(row["counts"]["visible"] for row in preview["categories"]) == len(expected)
    # Classification uses source occurrences, independent of the stale canonical label.
    assert next(row for row in preview["categories"] if row["label"] == "AI")["canonical_ids"] == ["agent", "orders"]
    assert {"cache", "extra-002", "extra-003", "extra-004"} == set(
        next(row for row in preview["categories"] if row["id"] == "unclear")["canonical_ids"])


def test_pending_bucket_has_priority_and_retains_confirmed_and_pending_occurrences():
    db = category_corpus()
    with db.session() as session, session.begin():
        annotation = session.get(OccurrenceTaskAnnotation, "o-000")
        evidence = deepcopy(annotation.evidence)
        evidence["semantic_scopes"][TASK].update(decision="UNRESOLVED", reason="当前片段信息不足，保留待核验")
        annotation.evidence = evidence
    page = listing(db, collection_category="pending", page_size=20)
    rows = {row["canonical_question_id"]: row for row in page["data"]}
    assert set(rows) == {"cache", "other"}
    assert rows["cache"]["confirmed_occurrence_count"] == 2
    assert rows["cache"]["pending_occurrence_count"] == 1
    assert rows["cache"]["semantic_verification"] == "CONFIRMED"
    assert rows["cache"]["pending_sources"][0]["occurrence_id"] == "o-000"
    assert page["meta"]["collection_category"]["counts"] == {
        "confirmed": 1, "pending": 2, "pending_only": 1, "visible": 2,
        "confirmed_occurrences": 2, "pending_occurrences": 2, "visible_occurrences": 4}
    assert page["meta"]["sample_counts"] == {"occurrences": 2, "selected_occurrences": 2}
    assert page["meta"]["semantic_counts"]["selected_occurrences"] == 4
    assert "cache" not in next(row for row in collection(db)["categories"]
        if row["id"] == "unclear")["canonical_ids"]


def test_category_filter_is_after_root_top_n_not_an_independent_top_n_per_bucket():
    db = category_corpus()
    ai = bucket_id(db, "AI")
    root = listing(db, top_n=2)
    assert [row["canonical_question_id"] for row in root["data"]] == ["cache", "agent"]
    page = listing(db, top_n=2, collection_category=ai)
    assert [row["canonical_question_id"] for row in page["data"]] == ["agent"]
    assert page["meta"]["collection_category"]["root_counts"]["visible"] == 2
    assert page["meta"]["collection_category"]["counts"]["visible"] == 1
    assert page["meta"]["pagination"]["result_total"] == 1
    assert page["data"][0]["rank"] == 1
    assert page["meta"]["semantic_counts"]["selected_canonical_questions"] == 1


def test_category_filter_retains_review_filter_and_top_n_order():
    db = category_corpus()
    ai = bucket_id(db, "AI")
    with db.session() as session, session.begin():
        session.add(UserQuestionState(user_id="learner", canonical_question_id="orders", status="MASTERED"))
    page = listing(db, top_n=1, review_statuses=["MASTERED"],
        review_order="BEFORE_TOP_N", collection_category=ai)
    assert [row["canonical_question_id"] for row in page["data"]] == ["orders"]
    assert page["meta"]["collection_category"]["root_counts"]["visible"] == 1
    # AFTER_TOP_N yields an empty root; it must not quietly choose a question
    # from this category that was outside the original selected root.
    with pytest.raises(ValueError, match="INVALID_COLLECTION_CATEGORY"):
        listing(db, top_n=1, review_statuses=["MASTERED"],
            review_order="AFTER_TOP_N", collection_category=ai)


def test_category_cursor_and_exact_host_request_support_structured_pagination():
    db = category_corpus()
    ai = bucket_id(db, "AI")
    first = listing(db, collection_category=ai, page_size=1)
    cursor = first["meta"]["pagination"]["next_cursor"]
    assert cursor
    request = first["meta"]["list_request"]
    assert request["semantic_task"] == TASK and request["collection_category"] == ai
    assert "cursor" not in request
    payload = verify_scope(cursor, key=KEY, corpus_revision=first["meta"]["corpus_revision"])
    assert payload["scope"]["collection_category"] == ai
    assert payload["collection_category_hash"] == first["meta"]["collection_category"]["category_hash"]
    with db.session() as session:
        second = list_questions(session, ListRequest(**request, cursor=cursor),
            signing_key=KEY, user_id="learner", as_of=AS_OF)
    assert [row["canonical_question_id"] for row in first["data"] + second["data"]] == ["agent", "orders"]
    assert [row["rank"] for row in second["data"]] == [2]
    with pytest.raises(ValueError, match="INVALID_CURSOR_SCOPE"):
        listing(db, collection_category="unclear", page_size=1, cursor=cursor)
    with pytest.raises(ValueError, match="INVALID_CURSOR_SCOPE"):
        listing(db, page_size=1, cursor=cursor)


@pytest.mark.parametrize("field,value", [("topic_id", "redis.cache_problems"), ("taxonomy_version", "different")])
def test_category_cursor_detects_topic_binding_change_even_without_revision_bump(field, value):
    db = category_corpus()
    ai = bucket_id(db, "AI")
    first = listing(db, collection_category=ai, page_size=1)
    with db.session() as session, session.begin():
        setattr(session.get(QuestionOccurrence, "o-003"), field, value)
    with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
        listing(db, collection_category=ai, page_size=1,
            cursor=first["meta"]["pagination"]["next_cursor"])


@pytest.mark.parametrize("changes", [{}, {"semantic_task": "ALGORITHM_PROBLEM"}])
def test_category_requires_engineering_collection(changes):
    with pytest.raises(ValidationError, match="COLLECTION_CATEGORY_REQUIRES_ENGINEERING_DESIGN"):
        ListRequest(collection_category="pending", **changes)


def test_category_rejects_non_question_group_and_unknown_id():
    with pytest.raises(ValidationError, match="COLLECTION_CATEGORY_REQUIRES_QUESTION_LIST"):
        StatsListRequest(semantic_task=TASK, collection_category="pending", group_by="company")
    with pytest.raises(ValueError, match="INVALID_COLLECTION_CATEGORY"):
        listing(category_corpus(), collection_category="pretend-topic")


def test_category_scope_retains_company_and_round_on_same_occurrence():
    db = category_corpus()
    page = listing(db, collection_category="unclear", company="字节", round="SECOND")
    assert [row["canonical_question_id"] for row in page["data"]] == ["cache"]
    assert page["data"][0]["confirmed_occurrence_count"] == 2
    assert page["meta"]["list_request"]["company"] == "字节"
    assert page["meta"]["list_request"]["round"] == "SECOND"
    with pytest.raises(ValueError, match="INVALID_COLLECTION_CATEGORY"):
        listing(db, collection_category="unclear", company="腾讯")

def test_external_canonical_scope_limits_stats_counts_and_pending_sources():
    db = category_corpus()
    with db.session() as session, session.begin():
        annotation = session.get(OccurrenceTaskAnnotation, "o-000")
        evidence = deepcopy(annotation.evidence)
        evidence["semantic_scopes"][TASK].update(decision="UNRESOLVED", reason="此原问待确认")
        annotation.evidence = evidence
    with db.session() as session:
        result = list_questions(session, StatsListRequest(semantic_task=TASK, group_by="company"),
            canonical_ids=("cache",), signing_key=KEY, user_id="learner", as_of=AS_OF)
    assert [row["key"] for row in result["data"]] == ["字节"]
    assert result["data"][0]["confirmed_occurrence_count"] == 2
    assert result["data"][0]["pending_occurrence_count"] == 1
    assert {row["occurrence_id"] for row in result["data"][0]["pending_sources"]} == {"o-000"}
    assert result["meta"]["semantic_counts"]["selected_canonical_questions"] == 1
    assert result["meta"]["semantic_counts"]["confirmed_occurrences"] == 2
    assert result["meta"]["semantic_counts"]["pending_occurrences"] == 1
    assert result["meta"]["sample_counts"] == {"occurrences": 2, "selected_occurrences": 2}


def test_external_canonical_scope_is_bound_to_cursor_and_validated():
    db = category_corpus()
    request = StatsListRequest(semantic_task=TASK, group_by="company", page_size=1)
    with db.session() as session:
        first = list_questions(session, request, canonical_ids=("cache", "other"),
            signing_key=KEY, user_id="learner", as_of=AS_OF)
    cursor = first["meta"]["pagination"]["next_cursor"]
    assert cursor
    request = request.model_copy(update={"cursor": cursor})
    with db.session() as session:
        second = list_questions(session, request, canonical_ids=("other", "cache", "cache"),
            signing_key=KEY, user_id="learner", as_of=AS_OF)
    assert [row["key"] for row in first["data"] + second["data"]] == ["字节", "腾讯"]
    for different in (None, (), ("cache",)):
        with db.session() as session, pytest.raises(ValueError, match="INVALID_CURSOR_SCOPE"):
            list_questions(session, request, canonical_ids=different,
                signing_key=KEY, user_id="learner", as_of=AS_OF)
    with db.session() as session, pytest.raises(ValueError, match="INVALID_CATEGORY_SCOPE"):
        list_questions(session, request.model_copy(update={"cursor": None}), canonical_ids=("project",),
            signing_key=KEY, user_id="learner", as_of=AS_OF)


def test_empty_external_canonical_scope_is_not_unrestricted():
    db = category_corpus()
    with db.session() as session:
        result = list_questions(session, ListRequest(semantic_task=TASK), canonical_ids=(),
            signing_key=KEY, user_id="learner", as_of=AS_OF)
    assert result["data"] == []
    assert result["meta"]["pagination"]["result_total"] == 0
    assert result["meta"]["semantic_counts"]["selected_canonical_questions"] == 0
    assert result["meta"]["sample_counts"] == {"occurrences": 0, "selected_occurrences": 0}
    payload = verify_scope(result["meta"]["result_set_id"], key=KEY,
        corpus_revision=result["meta"]["corpus_revision"])
    assert payload["canonical_scope_hash"]
