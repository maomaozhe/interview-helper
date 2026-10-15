"""Exhaustive occurrence classification drives SQL frequency and pagination."""
from copy import deepcopy
from datetime import date
import hashlib

import pytest
from sqlalchemy import select

from interview_intelligence.analytics.listing import ListRequest, list_questions
from interview_intelligence.analytics.semantic_scope import (
    VERSION, SemanticScopeIncomplete, load_semantic_scope, semantic_input_hash,
)
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    CanonicalQuestion, DocumentBuild, Interview, OccurrenceTaskAnnotation,
    QuestionOccurrence, SourceDocument, SourceRevision, create_database,
)


def seed_semantic_corpus(*, extra=0):
    db = create_database("sqlite+pysqlite:///:memory:")
    # Third matching source of c-main is its first algorithm occurrence.
    items = [
        ("c-main", "字节", "FIRST", "REJECT", "手写工程缓存组件", "ENGINEERING"),
        ("c-main", "字节", "FIRST", "REJECT", "说明已有缓存容量设计", "ALGORITHM"),
        ("c-main", "字节", "FIRST", "ACCEPT", "手撕算法：实现 O(1) LRU", "UNKNOWN"),
        ("c-main", "字节", "SECOND", "ACCEPT", "算法：实现 O(1) LRU", "NONE"),
        ("c-second", "字节", "SECOND", "ACCEPT", "给定数组求最大子数组和", "UNKNOWN"),
        ("c-negative", "字节", "FIRST", "REJECT", "解释布隆过滤器原理", "ALGORITHM"),
        ("c-other", "腾讯", "FIRST", None, "反转链表", "ALGORITHM"),
    ] + [(f"c-extra-{i:03}", "字节", "FIRST", "ACCEPT",
          f"算法题：数组第 {i} 种求解目标", "NONE") for i in range(extra)]
    with db.session() as session, session.begin():
        seen = set()
        for number, (cid, company, round_name, verdict, raw, focus) in enumerate(items):
            if cid not in seen:
                session.add(CanonicalQuestion(id=cid, canonical_text=raw,
                    primary_topic_id="fundamentals.data_structures", taxonomy_version="v1",
                    question_type="KNOWLEDGE"))
                seen.add(cid)
                session.flush()
            source = SourceDocument(id=f"s-{number}", source_identity=f"post:{number}",
                source_type="file", original_relative_path=f"{number}.md")
            session.add(source)
            session.flush()
            digest = hashlib.sha256(raw.encode()).hexdigest()
            revision = SourceRevision(id=f"r-{number}", source_document_id=source.id,
                raw_file_hash=digest, decoded_text_hash=digest,
                snapshot_path=f"{digest}.md", raw_file_path=f"{number}.md")
            session.add(revision)
            session.flush()
            build = DocumentBuild(id=f"b-{number}", source_revision_id=revision.id,
                processing_fingerprint="v1", document_kind="INTERVIEW_REPORT",
                processing_state="READY", publication_state="ACTIVE")
            session.add(build)
            session.flush()
            source.active_build_id = build.id
            interview = Interview(id=f"i-{number}", build_id=build.id, session_key="round",
                session_order=1, session_kind="SINGLE", analytics_eligible=True,
                company_normalized=company, round=round_name, interview_date=date(2026, 8, 1))
            session.add(interview)
            session.flush()
            occurrence = QuestionOccurrence(id=f"o-{number:03}", interview_id=interview.id,
                canonical_question_id=cid, raw_question=raw, normalized_question=raw,
                question_order=1, source_spans=[{"revision_id": revision.id,
                    "quote": raw, "start_char": 0, "end_char": len(raw)}],
                topic_id="fundamentals.data_structures", taxonomy_version="v1",
                question_type="KNOWLEDGE", confidence=.9)
            session.add(occurrence)
            session.flush()
            evidence = {"legacy_evidence": "preserve this"}
            if verdict:
                evidence["semantic_scopes"] = {"ALGORITHM_PROBLEM": {
                    "version": VERSION, "decision": verdict,
                    "input_hash": semantic_input_hash(occurrence, revision),
                    "quote": raw, "reason": "本题原文的任务属于该范围" if verdict == "ACCEPT"
                        else "本题是工程实现或原理讨论"}}
            session.add(OccurrenceTaskAnnotation(occurrence_id=occurrence.id,
                response_form="UNKNOWN", coding_focus=focus, classification_status="NEEDS_REVIEW",
                producer_version="extraction_task_v1", evidence=evidence))
    return db


def listing(db, **changes):
    request = ListRequest(company="字节", semantic_task="ALGORITHM_PROBLEM", **changes)
    with db.session() as session:
        return list_questions(session, request, signing_key="test", as_of=date(2026, 10, 14))


def alter_scope(db, change, identity="o-005"):
    with db.session() as session, session.begin():
        annotation = session.get(OccurrenceTaskAnnotation, identity)
        evidence = deepcopy(annotation.evidence)
        change(evidence["semantic_scopes"]["ALGORITHM_PROBLEM"])
        annotation.evidence = evidence


def test_semantic_frequency_counts_only_algorithm_sources_and_ignores_legacy_labels():
    db = seed_semantic_corpus()
    result = listing(db)
    assert [(row["canonical_question_id"], row["occurrence_count"])
            for row in result["data"]] == [("c-main", 2), ("c-second", 1)]
    assert result["meta"]["sample_counts"] == {"occurrences": 3, "selected_occurrences": 3}
    assert result["meta"]["semantic_scope"] == {
        "task": "ALGORITHM_PROBLEM", "version": VERSION,
        "scope_hash": result["meta"]["semantic_scope"]["scope_hash"],
        "occurrences": 6, "accepted": 3, "rejected": 3, "complete": True}
    assert result["data"][0]["matched_question"] == "手撕算法：实现 O(1) LRU"
    assert result["data"][0]["source_summary"]["revision_id"] == "r-2"
    assert result["meta"]["applied_filters"]["coding_focus"] is None
    assert result["meta"]["applied_filters"]["annotation_status"] is None
    assert result["meta"]["semantic_task"] == "ALGORITHM_PROBLEM"
    assert "semantic_task" not in result["meta"]["applied_filters"]
    assert FilterSpec.model_validate(result["meta"]["applied_filters"]).company == "字节"
    with db.session() as session:
        annotation = session.get(OccurrenceTaskAnnotation, "o-002")
        assert annotation.coding_focus == "UNKNOWN"
        assert annotation.evidence["legacy_evidence"] == "preserve this"


@pytest.mark.parametrize("conflict", [
    {"coding_focus": "ALGORITHM"}, {"annotation_status": "KNOWN"},
])
def test_semantic_scope_rejects_explicit_legacy_task_filter_conflict(conflict):
    with pytest.raises(ValueError, match="SEMANTIC_SCOPE_LEGACY_TASK_FILTER_CONFLICT"):
        ListRequest(company="字节", semantic_task="ALGORITHM_PROBLEM", **conflict)
    db = seed_semantic_corpus()
    with db.session() as session, pytest.raises(ValueError,
            match="SEMANTIC_SCOPE_LEGACY_TASK_FILTER_CONFLICT"):
        load_semantic_scope(session, FilterSpec(company="字节", **conflict))


def test_full_scope_paginates_more_than_fifty_without_duplicates_or_omissions():
    db = seed_semantic_corpus(extra=63)
    cursor, ids, ranks = None, [], []
    while True:
        result = listing(db, page_size=13, cursor=cursor)
        assert result["meta"]["pagination"]["total"] == 65
        ids.extend(row["canonical_question_id"] for row in result["data"])
        ranks.extend(row["rank"] for row in result["data"])
        cursor = result["meta"]["pagination"]["next_cursor"]
        if not cursor:
            break
    assert len(ids) == len(set(ids)) == 65
    assert set(ids) == {"c-main", "c-second", *[f"c-extra-{i:03}" for i in range(63)]}
    assert ranks == list(range(1, 66))


@pytest.mark.parametrize("change, category", [
    (lambda evidence: evidence.clear(), "stale"),
    (lambda evidence: evidence.update(version="old"), "stale"),
    (lambda evidence: evidence.update(input_hash="0" * 64), "stale"),
    (lambda evidence: evidence.update(decision="UNKNOWN"), "invalid"),
    (lambda evidence: evidence.update(decision={"ACCEPT": True}), "invalid"),
    (lambda evidence: evidence.update(quote="相邻题目的算法目标"), "invalid"),
    (lambda evidence: evidence.update(reason=""), "invalid"),
])
def test_unreviewed_or_invalid_negative_also_prevents_claiming_complete_scope(change, category):
    db = seed_semantic_corpus()
    alter_scope(db, change)
    with pytest.raises(SemanticScopeIncomplete) as error:
        listing(db)
    assert error.value.coverage[category] == 1
    assert error.value.coverage["occurrences"] == 6


def test_missing_annotation_fails_closed_even_when_legacy_scope_would_exclude_it():
    db = seed_semantic_corpus()
    with db.session() as session, session.begin():
        session.delete(session.get(OccurrenceTaskAnnotation, "o-002"))
    with pytest.raises(SemanticScopeIncomplete) as error:
        listing(db)
    assert error.value.coverage["missing"] == 1


@pytest.mark.parametrize("field", ["raw_question", "source_spans", "decoded_text_hash"])
def test_modified_original_evidence_invalidates_classification(field):
    db = seed_semantic_corpus()
    with db.session() as session, session.begin():
        row = session.get(SourceRevision, "r-2") if field == "decoded_text_hash" else session.get(QuestionOccurrence, "o-002")
        if field == "source_spans":
            row.source_spans = [{**row.source_spans[0], "end_char": 1}]
        elif field == "decoded_text_hash":
            row.decoded_text_hash = "f" * 64
        else:
            row.raw_question += "并发变体"
    with pytest.raises(SemanticScopeIncomplete):
        listing(db)


def test_cursor_detects_new_decision_even_without_annotation_revision_bump():
    db = seed_semantic_corpus()
    cursor = listing(db, page_size=1)["meta"]["pagination"]["next_cursor"]
    alter_scope(db, lambda evidence: evidence.update(decision="ACCEPT"))
    with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
        listing(db, page_size=1, cursor=cursor)


def test_scope_checks_same_company_round_and_can_be_empty():
    db = seed_semantic_corpus()
    # Tencent has no reviewed scope; it must not block the byte-only request.
    result = listing(db, round="SECOND")
    assert result["meta"]["semantic_scope"]["occurrences"] == 2
    assert sum(row["occurrence_count"] for row in result["data"]) == 2
    with db.session() as session:
        empty = load_semantic_scope(session, FilterSpec(company="百度"))
    assert empty.occurrences == 0
    assert empty.accepted_occurrence_ids == ()


def test_all_rejected_scope_has_no_page_and_zero_counts():
    db = seed_semantic_corpus()
    for identity in ("o-002", "o-003", "o-004"):
        alter_scope(db, lambda evidence: evidence.update(decision="REJECT"), identity)
    result = listing(db)
    assert result["data"] == []
    assert result["meta"]["pagination"]["total"] == 0
    assert result["meta"]["pagination"]["next_cursor"] is None
    assert result["meta"]["sample_counts"]["occurrences"] == 0


def test_new_in_scope_occurrence_requires_its_own_decision():
    db = seed_semantic_corpus()
    with db.session() as session, session.begin():
        # Moving the unaudited Tencent occurrence into scope simulates newly
        # visible data even if a producer omitted a corpus-revision increment.
        session.get(Interview, "i-6").company_normalized = "字节"
    with pytest.raises(SemanticScopeIncomplete) as error:
        listing(db)
    assert error.value.coverage["occurrences"] == 7
    assert error.value.coverage["missing"] == 1
