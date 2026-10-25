"""Existing-topic browsing is a lossless partition of engineering source facts."""
from copy import deepcopy

import pytest

from interview_intelligence.analytics.engineering_categories import engineering_collection_categories
from interview_intelligence.analytics.listing import ListRequest
from interview_intelligence.analytics.semantic_scope import load_semantic_scope
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    CanonicalQuestion, DocumentBuild, Interview, OccurrenceTaskAnnotation,
    QuestionOccurrence, SourceDocument,
)
from test_engineering_scope_listing import engineering_corpus


TASK = "ENGINEERING_DESIGN"


def preview(db, **filters):
    with db.session() as session:
        return engineering_collection_categories(session, FilterSpec(**filters))


def change_topic(db, identity, topic, version="v1"):
    with db.session() as session, session.begin():
        row = session.get(QuestionOccurrence, identity)
        row.topic_id, row.taxonomy_version = topic, version


def by_kind(result, kind):
    return next(category for category in result["categories"] if category["kind"] == kind)


def test_preview_reports_exact_canonical_and_occurrence_counts_with_pending_visible():
    result = preview(engineering_corpus())
    assert result["counts"] == {"confirmed": 3, "pending": 1, "pending_only": 1,
        "visible": 4, "confirmed_occurrences": 4, "pending_occurrences": 1,
        "visible_occurrences": 5}
    assert by_kind(result, "TOPIC_L1")["label"] == "计算机基础"
    assert by_kind(result, "TOPIC_L1")["canonical_ids"] == ["agent", "cache", "orders"]
    assert by_kind(result, "PENDING")["canonical_ids"] == ["other"]
    assert by_kind(result, "PENDING")["occurrence_ids"] == ["o-006"]
    assert result["classification_basis"] == "existing_occurrence_taxonomy_l1"
    assert "已有主题" in result["classification_notice"]


@pytest.mark.parametrize("topic,version,reason", [
    ("not_a_known_topic", "v1", "UNKNOWN_TOPIC"),
    ("", "v1", "UNKNOWN_TOPIC"),
    ("other.unknown", "v1", "UNCLASSIFIED_TOPIC"),
    ("ai.agent", "old", "TAXONOMY_VERSION_MISMATCH"),
])
def test_unknown_or_obsolete_classification_is_not_hidden_or_guessed(topic, version, reason):
    db = engineering_corpus()
    change_topic(db, "o-004", topic, version)
    result = preview(db, company="字节")
    unclear = by_kind(result, "UNCLEAR")
    assert unclear["canonical_ids"] == ["agent"]
    assert unclear["classification_issues"] == {"agent": [reason]}
    assert unclear["counts"]["confirmed"] == unclear["counts"]["visible"] == 1
    assert result["counts"]["visible"] == 3


def test_canonical_occurrences_in_multiple_l1_topics_stay_whole_in_unclear_category():
    db = engineering_corpus()
    change_topic(db, "o-001", "redis.cache_problems")
    change_topic(db, "o-002", "system_design.cache")
    result = preview(db, company="字节")
    unclear = by_kind(result, "UNCLEAR")
    assert unclear["canonical_ids"] == ["cache"]
    assert unclear["occurrence_ids"] == ["o-001", "o-002"]
    assert unclear["classification_issues"] == {"cache": ["MULTIPLE_L1_TOPICS"]}
    assert unclear["counts"]["confirmed_occurrences"] == 2


def test_rejected_occurrence_and_canonical_primary_topic_do_not_override_current_visible_topics():
    db = engineering_corpus()
    change_topic(db, "o-001", "redis.cache_problems")
    change_topic(db, "o-002", "redis.high_availability")
    # o-000 has the same canonical but is a semantic negative. Canonical primary
    # topic also disagrees; neither is a matching engineering source occurrence.
    with db.session() as session, session.begin():
        session.get(CanonicalQuestion, "cache").primary_topic_id = "ai.agent"
    result = preview(db, company="字节")
    redis = next(category for category in result["categories"] if category["label"] == "Redis")
    assert redis["canonical_ids"] == ["cache"]
    assert redis["occurrence_ids"] == ["o-001", "o-002"]
    assert all(category["kind"] != "UNCLEAR" for category in result["categories"])


def test_mixed_confirmed_pending_canonical_moves_whole_to_pending_without_losing_positive_count():
    db = engineering_corpus()
    with db.session() as session, session.begin():
        annotation = session.get(OccurrenceTaskAnnotation, "o-002")
        evidence = deepcopy(annotation.evidence)
        evidence["semantic_scopes"][TASK]["decision"] = "UNRESOLVED"
        evidence["semantic_scopes"][TASK]["reason"] = "局部目标尚不足以确认归属"
        annotation.evidence = evidence
    change_topic(db, "o-002", "unknown_topic")
    result = preview(db, company="字节")
    pending = by_kind(result, "PENDING")
    assert pending["canonical_ids"] == ["cache"]
    assert pending["occurrence_ids"] == ["o-001", "o-002"]
    assert pending["counts"] == {"confirmed": 1, "pending": 1, "pending_only": 0,
        "visible": 1, "confirmed_occurrences": 1, "pending_occurrences": 1,
        "visible_occurrences": 2}
    assert result["counts"]["confirmed"] == 3
    assert result["counts"]["visible"] == 3


def test_combined_filters_and_topic_filter_apply_to_the_same_occurrence():
    db = engineering_corpus()
    change_topic(db, "o-001", "redis.cache_problems")
    change_topic(db, "o-002", "ai.agent")
    second = preview(db, company="字节", round="SECOND", topic_l1="AI")
    assert second["counts"]["visible"] == second["counts"]["visible_occurrences"] == 1
    assert second["categories"][0]["label"] == "AI"
    assert second["categories"][0]["occurrence_ids"] == ["o-002"]
    first = preview(db, company="字节", round="FIRST", topic_l1="AI")
    assert first["counts"]["visible"] == 0
    assert first["categories"] == []


@pytest.mark.parametrize("excluded_by", ["inactive", "excluded", "ineligible"])
def test_inactive_excluded_or_ineligible_facts_never_enter_categories(excluded_by):
    db = engineering_corpus()
    with db.session() as session, session.begin():
        if excluded_by == "inactive":
            session.get(SourceDocument, "s-6").active_build_id = None
        elif excluded_by == "excluded":
            session.get(DocumentBuild, "b-6").decision = "EXCLUDED"
        else:
            session.get(Interview, "i-6").analytics_eligible = False
    result = preview(db)
    assert result["counts"]["visible"] == 3
    assert result["counts"]["pending"] == 0
    assert all("other" not in category["canonical_ids"] for category in result["categories"])


def test_category_union_is_exact_disjoint_and_counts_add_for_every_visible_record():
    db = engineering_corpus(extra=73)
    change_topic(db, "o-001", "redis.cache_problems")
    change_topic(db, "o-002", "system_design.cache")
    change_topic(db, "o-004", "other.unknown")
    with db.session() as session:
        scope = load_semantic_scope(session, FilterSpec(), task=TASK)
        result = engineering_collection_categories(session, FilterSpec(), semantic_scope=scope)
    canonical_ids = [identity for category in result["categories"] for identity in category["canonical_ids"]]
    occurrence_ids = [identity for category in result["categories"] for identity in category["occurrence_ids"]]
    assert len(canonical_ids) == len(set(canonical_ids)) == 77
    assert len(occurrence_ids) == len(set(occurrence_ids)) == 78
    assert set(occurrence_ids) == set(scope.selected_occurrence_ids)
    for name in result["counts"]:
        assert sum(category["counts"][name] for category in result["categories"]) == result["counts"][name]
    assert {category["kind"] for category in result["categories"]} == {"TOPIC_L1", "UNCLEAR", "PENDING"}


def test_optional_ranked_selection_keeps_all_selected_canonical_occurrences_and_rejects_foreign_id():
    db = engineering_corpus()
    with db.session() as session:
        result = engineering_collection_categories(session, FilterSpec(company="字节"), canonical_ids=["cache"])
        assert result["counts"]["visible"] == 1
        assert result["counts"]["confirmed_occurrences"] == 2
        assert result["categories"][0]["occurrence_ids"] == ["o-001", "o-002"]
        empty = engineering_collection_categories(session, FilterSpec(company="字节"), canonical_ids=[])
        assert empty["categories"] == [] and empty["counts"]["visible"] == 0
        with pytest.raises(ValueError, match="INVALID_CATEGORY_SCOPE"):
            engineering_collection_categories(session, FilterSpec(company="字节"), canonical_ids=["other"])


def test_category_hash_binds_topics_without_changing_semantic_scope_and_category_ids_stay_stable():
    db = engineering_corpus()
    first = preview(db, company="字节")
    first_id = by_kind(first, "TOPIC_L1")["id"]
    assert preview(db, company="字节") == first
    change_topic(db, "o-004", "ai.agent")
    second = preview(db, company="字节")
    assert second["scope_hash"] == first["scope_hash"]
    assert second["category_hash"] != first["category_hash"]
    assert next(category for category in second["categories"] if category["label"] == "计算机基础")["id"] == first_id
    change_topic(db, "o-004", "fundamentals.data_structures")
    assert preview(db, company="字节")["category_hash"] == first["category_hash"]


def test_precomputed_scope_cannot_hide_current_missing_review_or_cross_filter_boundaries():
    db = engineering_corpus()
    with db.session() as session:
        scope = load_semantic_scope(session, FilterSpec(company="字节"), task=TASK)
    with db.session() as session, session.begin():
        session.delete(session.get(OccurrenceTaskAnnotation, "o-004"))
    with db.session() as session:
        with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
            engineering_collection_categories(session, FilterSpec(company="字节"), semantic_scope=scope)
        with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
            engineering_collection_categories(session, FilterSpec(company="腾讯"), semantic_scope=scope)


def test_display_page_fields_do_not_change_source_category_identity():
    db = engineering_corpus()
    with db.session() as session:
        base = engineering_collection_categories(session, FilterSpec(company="字节"))
        request = ListRequest(company="字节", semantic_task=TASK, page_size=1, sort="importance")
        assert engineering_collection_categories(session, request) == base
