"""Source-reviewed corrections retain exhaustive SQL and immutable old evidence."""
from copy import deepcopy
import hashlib

import pytest
from sqlalchemy import select

from interview_intelligence.analytics.semantic_audit import (
    RULE_VERSION, ScopeReview, build_amendment, project_amendment,
)
from interview_intelligence.analytics.semantic_scope import (
    TASK, _digest, load_semantic_scope, semantic_input_hash, SemanticScopeIncomplete,
)
from interview_intelligence.analytics.listing import ListRequest, list_questions
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    CorpusState, OccurrenceTaskAnnotation, QuestionOccurrence, SourceRevision, create_database,
)
from test_semantic_scope_listing import seed_semantic_corpus


def fixture():
    db = seed_semantic_corpus()
    sources = {}
    with db.session() as session, session.begin():
        occurrence = session.get(QuestionOccurrence, "o-000")
        occurrence.raw_question = "给定一棵树和节点路径，编写遍历程序返回对应节点的值。"
        occurrence.normalized_question = occurrence.raw_question
        for i in range(7):
            o, r = session.get(QuestionOccurrence, f"o-{i:03}"), session.get(SourceRevision, f"r-{i}")
            raw = o.raw_question.encode()
            sources[r.id] = raw
            r.raw_file_hash = r.decoded_text_hash = hashlib.sha256(raw).hexdigest()
            o.source_spans = [{"revision_id": r.id, "quote": o.raw_question, "start_char": 0,
                              "end_char": len(o.raw_question), "start_line": 1, "end_line": 1}]
            a = session.get(OccurrenceTaskAnnotation, o.id)
            if TASK in a.evidence.get("semantic_scopes", {}):
                evidence = deepcopy(a.evidence)
                evidence["semantic_scopes"][TASK].update(input_hash=semantic_input_hash(o, r), quote=o.raw_question)
                a.evidence = evidence
    return db, sources


def review(db, **changes):
    with db.session() as session:
        occurrence = session.get(QuestionOccurrence, "o-000")
        previous = session.get(OccurrenceTaskAnnotation, occurrence.id).evidence["semantic_scopes"][TASK]
        return {"occurrence_id": occurrence.id, "expected_decision_sha256": _digest(previous),
            "decision": "ACCEPT", "task_kind": "COMPUTATIONAL_TASK", "quote": occurrence.raw_question,
            "operation_quote": "编写遍历程序", "goal_quote": "返回对应节点的值", "reason": "原题要求遍历输入结构并返回可核对的节点值；不由组件名称或旧标签决定。", **changes}


def build(db, sources, reviews=None):
    with db.session() as session:
        return build_amendment(session, FilterSpec(company="字节"), reviews or [review(db)],
            source_reader=lambda r: sources[r.id], reviewer={"id": "test-reviewer", "kind": "agent"},
            reviewed_at="2026-10-16T10:00:00+08:00")


def project(db, sources, artifact, writer=lambda receipt: None):
    return project_amendment(db, artifact, source_reader=lambda r: sources[r.id], before_commit=writer)


def test_explicit_review_projects_real_sql_without_changing_legacy_labels_or_old_audit():
    db, sources = fixture();artifact = build(db, sources)
    with db.session() as session:
        before = deepcopy(session.get(OccurrenceTaskAnnotation, "o-000").evidence)
        unrelated = deepcopy(session.get(OccurrenceTaskAnnotation, "o-005").evidence)
    receipts = []
    receipt = project(db, sources, artifact, receipts.append)
    assert receipts == [receipt] and receipt["status"] == "LOCAL_CANDIDATE_ONLY"
    assert receipt["after_scope"]["accepted"] == 4 and receipt["before_scope"]["accepted"] == 3
    assert receipt["semantic_rereview_count"] == 1 and receipt["unchanged_decisions"] == 5
    assert receipt["before_scope"]["scope_hash"] != receipt["after_scope"]["scope_hash"]
    with db.session() as session:
        a = session.get(OccurrenceTaskAnnotation, "o-000")
        assert a.coding_focus == "ENGINEERING" and a.classification_status == "NEEDS_REVIEW"
        assert a.evidence["legacy_evidence"] == before["legacy_evidence"]
        assert a.evidence["semantic_scope_history"][TASK][0]["previous"] == before["semantic_scopes"][TASK]
        assert a.evidence["semantic_scopes"][TASK]["review"]["rule_version"] == RULE_VERSION
        assert a.evidence["semantic_scopes"][TASK]["review"]["human_verified"] is False
        assert session.get(OccurrenceTaskAnnotation, "o-005").evidence == unrelated
        result = list_questions(session, ListRequest(company="字节", semantic_task=TASK, page_size=100), signing_key="test")
        assert result["meta"]["semantic_scope"]["complete"]
        assert [(r["canonical_question_id"], r["occurrence_count"]) for r in result["data"]] == [("c-main", 3), ("c-second", 1)]
        narrow = load_semantic_scope(session, FilterSpec(company="字节", round="SECOND"))
        assert set(narrow.accepted_occurrence_ids) == {"o-003", "o-004"}
        with pytest.raises(SemanticScopeIncomplete):load_semantic_scope(session, FilterSpec(company="腾讯"))


@pytest.mark.parametrize("changes", [
    {"operation_quote": None}, {"goal_quote": ""}, {"task_kind": "ENGINEERING_BEHAVIOR"},
    {"decision": "REJECT"}, {"quote": "相邻题：反转链表"},
    {"goal_quote": "反转链表"}, {"expected_decision_sha256": "0" * 64},
    {"occurrence_id": "o-006"},
])
def test_no_auto_acceptance_without_current_occurrence_review_evidence(changes):
    db, sources = fixture()
    with pytest.raises(ValueError):build(db, sources, [review(db, **changes)])


def test_component_spelling_and_legacy_labels_are_not_a_classifier():
    # The protocol validates explicit review evidence; it does not infer a role
    # from algorithm/JSON/SQL names or accept arbitrary request-time phrases.
    db, sources = fixture()
    assert "JSON" not in review(db)["quote"] and "算法" not in review(db)["quote"]
    assert build(db, sources)["payload"]["changes"][0]["after"]["decision"] == "ACCEPT"
    with pytest.raises(ValueError):ScopeReview.model_validate(review(db, goal_quote=None))


@pytest.mark.parametrize("damage", ["source", "span", "negative_decision"])
def test_entire_scope_including_negatives_is_checked_before_projection(damage):
    db, sources = fixture()
    if damage == "source":sources["r-5"] += b" changed"
    else:
        with db.session() as session, session.begin():
            if damage == "span":
                o = session.get(QuestionOccurrence, "o-005");o.source_spans = [{**o.source_spans[0], "end_char": 1}]
            else:
                a = session.get(OccurrenceTaskAnnotation, "o-005");e = deepcopy(a.evidence)
                e["semantic_scopes"][TASK]["version"] = "stale";a.evidence = e
    with pytest.raises(ValueError):build(db, sources)


def test_stale_scope_and_repeated_projection_are_rejected():
    db, sources = fixture();artifact = build(db, sources)
    with db.session() as session, session.begin():session.get(CorpusState, 1).indexed_revision += 1
    with pytest.raises(ValueError, match="SCOPE_CHANGED"):project(db, sources, artifact)
    artifact = build(db, sources);project(db, sources, artifact)
    with pytest.raises(ValueError, match="PREVIOUS_CHANGED"):project(db, sources, artifact)


@pytest.mark.parametrize("reseal", [False, True])
def test_corrupt_or_resealed_arbitrary_projection_is_rebuilt_and_rejected(reseal):
    db, sources = fixture();artifact = build(db, sources)
    artifact["payload"]["changes"][0]["after"]["quote"] = "unreviewed replacement"
    if reseal:artifact["sha256"] = _digest(artifact["payload"])
    with pytest.raises(ValueError, match="DIGEST_MISMATCH|SCOPE_CHANGED"):project(db, sources, artifact)


def test_receipt_persistence_failure_rolls_back_all_annotation_and_revision_changes():
    db, sources = fixture();artifact = build(db, sources)
    def fail(receipt):raise OSError("receipt disk failure")
    with pytest.raises(OSError, match="disk failure"):project(db, sources, artifact, fail)
    assert build(db, sources) == artifact
    with db.session() as session:
        assert "semantic_scope_history" not in session.get(OccurrenceTaskAnnotation, "o-000").evidence


def test_projection_refuses_persistent_or_uri_database(tmp_path):
    db, sources = fixture();artifact = build(db, sources)
    persistent = create_database("sqlite+pysqlite:///" + str(tmp_path / "not-isolated.db"))
    with pytest.raises(ValueError, match="MEMORY_SQLITE_ONLY"):project(persistent, sources, artifact)
