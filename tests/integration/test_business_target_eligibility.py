"""Engineering audit guides one recall branch, never whole-query eligibility."""
from copy import deepcopy

from sqlalchemy import select

from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import OccurrenceTaskAnnotation, QuestionOccurrence
from interview_intelligence.search.service import (
    eligible_canonical_ids, engineering_target_eligibility, search_questions,
)
from interview_intelligence.relevance_scope import compile_relevance_scope
from test_engineering_scope_listing import engineering_corpus


def test_same_occurrence_filters_do_not_borrow_another_rounds_positive_engineering_audit():
    db = engineering_corpus()
    with db.session() as session:
        filters = FilterSpec(company="字节", round="FIRST")
        broad = eligible_canonical_ids(session, filters)
        target, provenance = engineering_target_eligibility(session, filters, broad)
    assert set(broad) == {"cache", "orders", "agent", "project"}
    assert set(target) == {"orders", "agent"}
    assert "cache" not in target  # Its engineering-positive occurrences are SECOND.
    assert provenance["source"] == "current_occurrence_engineering_audit"
    assert provenance["business_membership_confirmed"] is False
    assert provenance["requested"] is provenance["available"] is provenance["applied"] is True
    assert provenance["fallback"] is False
    with db.session() as session:
        filters = FilterSpec(company="字节", round="SECOND")
        target, _ = engineering_target_eligibility(session, filters, eligible_canonical_ids(session, filters))
    assert target == ["cache"]


def test_missing_unresolved_and_stale_sources_remain_target_candidates_without_becoming_business_positives():
    db = engineering_corpus()
    with db.session() as session, session.begin():
        annotation = session.get(OccurrenceTaskAnnotation, "o-005")
        evidence = deepcopy(annotation.evidence)
        evidence["semantic_scopes"]["ENGINEERING_DESIGN"]["input_hash"] = "0" * 64
        annotation.evidence = evidence
        unresolved = session.get(OccurrenceTaskAnnotation, "o-003")
        evidence = deepcopy(unresolved.evidence)
        evidence["semantic_scopes"]["ENGINEERING_DESIGN"]["decision"] = "UNRESOLVED"
        unresolved.evidence = evidence
    with db.session() as session:
        target, meta = engineering_target_eligibility(session, FilterSpec(), eligible_canonical_ids(session, FilterSpec()))
    assert set(target) == {"cache", "orders", "agent", "other", "project"}
    assert meta["coverage"]["missing"] == meta["coverage"]["stale"] == meta["coverage"]["unresolved"] == 1
    assert meta["business_membership_confirmed"] is False


def test_unreviewed_corpus_uses_explicit_wide_fallback_instead_of_losing_candidates():
    db = engineering_corpus()
    with db.session() as session, session.begin():
        for annotation in session.scalars(select(OccurrenceTaskAnnotation)):
            annotation.evidence = {}
    with db.session() as session:
        broad = eligible_canonical_ids(session, FilterSpec())
        target, meta = engineering_target_eligibility(session, FilterSpec(), broad)
    assert target == broad
    assert meta["fallback"] is True and meta["source"] == "wide_unaudited_fallback"
    assert meta["requested"] is True and meta["available"] is meta["applied"] is False
    assert meta["coverage"]["accepted"] == 0 and meta["coverage"]["missing"] > 0


def test_reviewed_negative_only_subset_keeps_empty_target_prior_and_original_broad_eligibility():
    db = engineering_corpus()
    with db.session() as session:
        # Explicit company, round and type filters remain on each occurrence.
        filters = FilterSpec(question_type="KNOWLEDGE", company="字节", round="FIRST")
        for annotation in session.scalars(select(OccurrenceTaskAnnotation)):
            evidence = deepcopy(annotation.evidence)
            if "ENGINEERING_DESIGN" in evidence["semantic_scopes"]:
                evidence["semantic_scopes"]["ENGINEERING_DESIGN"]["decision"] = "REJECT"
                annotation.evidence = evidence
        session.flush()
        broad = eligible_canonical_ids(session, filters)
        target, meta = engineering_target_eligibility(session, filters, broad)
    assert len(broad) == 4 and target == []
    assert meta["coverage"]["rejected"] == 4
    assert meta["applied"] is True and meta["fallback"] is False
    assert meta["broad_scope"] == "original_eligible_ids"


def test_service_passes_soft_target_and_audit_provenance_without_narrowing_broad_eligibility():
    db = engineering_corpus()
    scope = compile_relevance_scope(["具体业务系统设计"], candidate_mode=True)
    class Retriever:
        supports_relevance_scope = True
        supports_target_eligibility = True
        def retrieve(self, query, eligible, pipeline, top_k, *, relevance_scope, target_eligible_ids, target_eligibility):
            assert set(eligible) == {"cache", "orders", "agent", "other", "project"}
            assert set(target_eligible_ids) == {"cache", "orders", "agent", "other"}
            assert relevance_scope == scope
            assert target_eligibility["semantic_version"] == "engineering_design_scope_v1"
            # A broad branch may still retrieve outside the soft engineering
            # prior. This is not an audited-business hard filter.
            return {"data": [{"canonical_question_id": "project"}], "meta": {
                "candidate_ids": ["project"], "relevance_scope_applied": True,
                "relevance_scope_hash": scope["scope_hash"], "target_eligibility_applied": True,
                "target_eligible_ids_applied": True,
                "target_eligible_ids_hash": target_eligibility["target_eligible_ids_hash"]}}
    with db.session() as session:
        result = search_questions(session, Retriever(), "具体业务系统设计", FilterSpec(), relevance_scope=scope)
    assert result["data"][0]["canonical_question_id"] == "project"
    assert result["meta"]["eligible_count"] == 5
    assert result["meta"]["target_eligibility"]["target_eligible_count"] == 4
