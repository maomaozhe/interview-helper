"""Source audit publication is complete, atomic, reproducible and opt-in."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from interview_intelligence.analytics.semantic_scope import (
    ENGINEERING_DESIGN_TASK as TASK, SemanticScopeIncomplete, _digest, load_semantic_scope,
)
from interview_intelligence.analytics.semantic_scope_import import (
    build_scope_import_artifact, import_semantic_scope, validate_scope_import_artifact,
)
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    CorpusState, Interview, OccurrenceTaskAnnotation, QuestionOccurrence, SourceRevision,
)
from test_engineering_scope_listing import engineering_corpus


REVIEWED_AT = "2026-10-16T10:00:00+08:00"


def fixture():
    db = engineering_corpus()
    with db.session() as session:
        sources = {f"r-{int(value.id[2:])}": value.raw_question.encode("utf-8")
                   for value in session.scalars(select(QuestionOccurrence))}
    return db, sources


def build(db, sources, *, changes=None, reviewer_kind="agent", task=TASK):
    with db.session() as session:
        reviews = []
        for occurrence in session.scalars(select(QuestionOccurrence).order_by(QuestionOccurrence.id)):
            if occurrence.id == "o-006":
                continue
            decision = "ACCEPT" if task == TASK and occurrence.id in {"o-001", "o-002", "o-003", "o-004"} else "REJECT"
            reviews.append({"occurrence_id": occurrence.id, "decision": decision,
                "quote": occurrence.raw_question, "reason": "逐问核查建设任务及原文证据"})
        if changes:
            changes(reviews)
        return build_scope_import_artifact(session, FilterSpec(company="字节"), reviews,
            source_reader=lambda revision: sources[revision.id],
            reviewer={"id": "independent-reviewer", "kind": reviewer_kind},
            reviewed_at=REVIEWED_AT, task=task)


def publish(db, sources, artifact, **kwargs):
    return import_semantic_scope(db, artifact,
        source_reader=lambda revision: sources[revision.id], **kwargs)


def snapshot(db):
    with db.session() as session:
        return {"revision": session.get(CorpusState, 1).task_annotation_revision,
            "annotations": {row.occurrence_id: {"response_form": row.response_form,
                "coding_focus": row.coding_focus, "producer_version": row.producer_version,
                "classification_status": row.classification_status, "evidence": deepcopy(row.evidence)}
                for row in session.scalars(select(OccurrenceTaskAnnotation))}}


def rehash(artifact):
    artifact["sha256"] = _digest(artifact["payload"])
    return artifact


def test_dry_run_is_default_and_preserves_every_database_field():
    db, sources = fixture()
    artifact = build(db, sources)
    before = snapshot(db)
    receipt = publish(db, sources, artifact)
    assert receipt["status"] == "DRY_RUN"
    assert receipt["coverage"]["complete"] is True
    assert receipt["coverage"]["accepted"] == 4
    assert receipt["changed_annotations"] == 6
    assert receipt["after_annotation_revision"] == before["revision"] + 1
    assert snapshot(db) == before


def test_atomic_apply_retains_legacy_and_other_task_evidence_with_history_and_idempotence():
    db, sources = fixture()
    artifact = build(db, sources)
    before = snapshot(db)
    prepared = []
    receipt = publish(db, sources, artifact, apply=True, before_commit=prepared.append)
    after = snapshot(db)
    assert receipt["status"] == "APPLIED"
    assert prepared[0]["status"] == "PREPARED"
    assert after["revision"] == before["revision"] + 1
    assert len(receipt["rollback_evidence"]) == 6
    for oid, old in before["annotations"].items():
        new = after["annotations"][oid]
        if oid == "o-006":
            assert old == new
            continue
        assert {key: value for key, value in old.items() if key != "evidence"} == {
            key: value for key, value in new.items() if key != "evidence"}
        assert new["evidence"]["legacy_evidence"] == old["evidence"]["legacy_evidence"]
        assert new["evidence"]["semantic_scopes"]["ALGORITHM_PROBLEM"] == old["evidence"]["semantic_scopes"]["ALGORITHM_PROBLEM"]
        assert new["evidence"]["semantic_scope_history"][TASK][0]["previous"] == old["evidence"]["semantic_scopes"][TASK]
        review = new["evidence"]["semantic_scopes"][TASK]["review"]
        assert review["human_verified"] is False
        assert review["import_sha256"] == artifact["sha256"]
        assert review["reviewer"]["kind"] == "agent"
    with db.session() as session:
        scope = load_semantic_scope(session, FilterSpec(company="字节"), task=TASK)
        assert scope.metadata()["complete"] is True
        assert len(scope.accepted_occurrence_ids) == 4
    repeated = publish(db, sources, artifact, apply=True, before_commit=prepared.append)
    assert repeated["status"] == "UNCHANGED"
    assert len(prepared) == 1
    assert snapshot(db) == after


def test_new_annotation_keeps_unreviewed_legacy_labels_unknown():
    db, sources = fixture()
    with db.session() as session, session.begin():
        session.delete(session.get(OccurrenceTaskAnnotation, "o-003"))
    artifact = build(db, sources, reviewer_kind="human")
    receipt = publish(db, sources, artifact, apply=True, before_commit=lambda receipt: None)
    assert receipt["new_annotations"] == 1
    rollback = next(row for row in receipt["rollback_evidence"] if row["occurrence_id"] == "o-003")
    assert rollback["before_annotation"] is None
    with db.session() as session:
        annotation = session.get(OccurrenceTaskAnnotation, "o-003")
        assert (annotation.response_form, annotation.coding_focus, annotation.classification_status) == (
            "UNKNOWN", "UNKNOWN", "UNKNOWN")
        assert annotation.evidence["semantic_scopes"][TASK]["review"]["human_verified"] is True


@pytest.mark.parametrize("mangle", [
    lambda value: value["payload"]["decisions"].pop(),
    lambda value: value["payload"]["decisions"].append(deepcopy(value["payload"]["decisions"][0])),
    lambda value: value["payload"]["decisions"][0].update(occurrence_id="outside"),
])
def test_missing_extra_or_duplicate_occurrence_prevents_publication(mangle):
    db, sources = fixture()
    artifact = build(db, sources)
    before = snapshot(db)
    mangle(artifact)
    rehash(artifact)
    with pytest.raises(SemanticScopeIncomplete):
        publish(db, sources, artifact, apply=True, before_commit=lambda receipt: None)
    assert snapshot(db) == before


def test_algorithm_unresolved_reviews_still_prevent_publication():
    db, sources = fixture()
    artifact = build(db, sources, changes=lambda values: values[0].update(
        decision="UNRESOLVED", reason="原文上下文不足，需进一步复核"), task="ALGORITHM_PROBLEM")
    before = snapshot(db)
    with pytest.raises(SemanticScopeIncomplete) as error:
        publish(db, sources, artifact, apply=True, before_commit=lambda receipt: None)
    assert error.value.coverage["unresolved"] == 1
    assert error.value.coverage["rejected"] == 5
    assert error.value.coverage["complete"] is False
    assert snapshot(db) == before


def test_engineering_unresolved_review_is_published_as_pending_with_idempotent_repeat():
    db, sources = fixture()
    artifact = build(db, sources, changes=lambda values: values[1].update(
        decision="UNRESOLVED", reason="原问建设目标的语义需要独立复核"))
    prepared = []
    preview = publish(db, sources, artifact)
    assert preview["coverage"]["unresolved"] == 1
    assert preview["coverage"]["complete"] is False
    before_revision = snapshot(db)["revision"]
    receipt = publish(db, sources, artifact, apply=True, before_commit=prepared.append)
    assert receipt["status"] == "APPLIED"
    assert receipt["semantic_scope"]["complete"] is False
    assert receipt["semantic_scope"]["unresolved"] == 1
    assert snapshot(db)["revision"] == before_revision + 1
    with db.session() as session:
        annotation = session.get(OccurrenceTaskAnnotation, "o-001")
        assert annotation.evidence["semantic_scopes"][TASK]["decision"] == "UNRESOLVED"
        scope = load_semantic_scope(session, FilterSpec(company="字节"), task=TASK)
        assert "o-001" not in scope.accepted_occurrence_ids
        assert scope.metadata()["complete"] is False
    repeated = publish(db, sources, artifact, apply=True, before_commit=prepared.append)
    assert repeated["status"] == "UNCHANGED"
    assert snapshot(db)["revision"] == before_revision + 1
    assert len(prepared) == 1


@pytest.mark.parametrize("quote", ["", "相邻题的完整真实引用"])
def test_pending_engineering_decision_does_not_bypass_its_own_source_quote(quote):
    db, sources = fixture()
    with pytest.raises(SemanticScopeIncomplete) as error:
        build(db, sources, changes=lambda values: values[0].update(decision="UNRESOLVED", quote=quote))
    assert error.value.coverage["invalid"] == 1


@pytest.mark.parametrize("field,value,error", [
    ("corpus_revision", 999, "CORPUS_CHANGED"),
    ("task_annotation_revision", 999, "ANNOTATIONS_CHANGED"),
    ("version", "algorithm_problem_scope_v1", "VERSION_INVALID"),
])
def test_audit_is_bound_to_task_version_and_current_corpus(field, value, error):
    db, sources = fixture()
    artifact = build(db, sources)
    artifact["payload"][field] = value
    rehash(artifact)
    with pytest.raises(ValueError, match=error):
        publish(db, sources, artifact)


def test_immutable_payload_and_manifest_cannot_be_silently_replaced():
    db, sources = fixture()
    artifact = build(db, sources)
    artifact["payload"]["reviewer"]["id"] = "other-reviewer"
    with pytest.raises(ValueError, match="DIGEST_MISMATCH"):
        publish(db, sources, artifact)
    rehash(artifact)
    artifact["payload"]["manifest"]["bindings_sha256"] = "0" * 64
    rehash(artifact)
    with pytest.raises(ValueError, match="MANIFEST_CHANGED"):
        publish(db, sources, artifact)


def test_scope_filters_must_be_explicit_instead_of_relying_on_future_defaults():
    db, sources = fixture()
    artifact = build(db, sources)
    artifact["payload"]["filters"].pop("date_basis")
    rehash(artifact)
    with pytest.raises(ValueError, match="FILTERS_INCOMPLETE"):
        publish(db, sources, artifact)


@pytest.mark.parametrize("binding", ["canonical_question_id", "source_revision_id", "input_hash"])
def test_each_decision_must_match_its_current_occurrence_binding(binding):
    db, sources = fixture()
    artifact = build(db, sources)
    artifact["payload"]["decisions"][0][binding] = "0" * 64 if binding == "input_hash" else "different"
    rehash(artifact)
    with pytest.raises(SemanticScopeIncomplete) as error:
        publish(db, sources, artifact)
    assert error.value.coverage["stale"] == 1


def test_new_active_occurrence_invalidates_exact_manifest_even_without_revision_bump():
    db, sources = fixture()
    artifact = build(db, sources)
    with db.session() as session, session.begin():
        session.get(Interview, "i-6").company_normalized = "字节"
    with pytest.raises(SemanticScopeIncomplete) as error:
        publish(db, sources, artifact)
    assert error.value.coverage["missing"] == 1


@pytest.mark.parametrize("mangle,error", [
    (lambda db, sources: sources.update({"r-1": b"changed"}), "SOURCE_HASH_MISMATCH"),
    (lambda db, sources: alter_source(db, "decoded_text_hash", "0" * 64), "DECODED_HASH_MISMATCH"),
    (lambda db, sources: alter_span(db, "end_char", 1), "SOURCE_SPAN_MISMATCH"),
    (lambda db, sources: alter_span(db, "start_line", 2), "SOURCE_LINE_MISMATCH"),
    (lambda db, sources: alter_span(db, "revision_id", "r-0"), "SOURCE_SPAN_MISMATCH"),
])
def test_raw_bytes_decoded_hash_characters_and_lines_are_verified(mangle, error):
    db, sources = fixture()
    artifact = build(db, sources)
    mangle(db, sources)
    with pytest.raises(ValueError, match=error):
        publish(db, sources, artifact)


def alter_source(db, field, value):
    with db.session() as session, session.begin():
        setattr(session.get(SourceRevision, "r-1"), field, value)


def alter_span(db, field, value):
    with db.session() as session, session.begin():
        row = session.get(QuestionOccurrence, "o-001")
        row.source_spans = [{**row.source_spans[0], field: value}]


def test_a_review_cannot_borrow_a_true_quote_from_another_question():
    db, sources = fixture()
    with pytest.raises(SemanticScopeIncomplete) as error:
        build(db, sources, changes=lambda values: values[0].update(quote=values[1]["quote"]))
    assert error.value.coverage["invalid"] == 1


def test_apply_requires_durable_audit_writer_and_rolls_back_when_it_fails():
    db, sources = fixture()
    artifact = build(db, sources)
    before = snapshot(db)
    with pytest.raises(ValueError, match="RECEIPT_WRITER_REQUIRED"):
        publish(db, sources, artifact, apply=True)
    def fail(receipt):
        assert receipt["status"] == "PREPARED"
        assert len(receipt["rollback_evidence"]) == 6
        raise OSError("receipt storage failure")
    with pytest.raises(OSError, match="storage failure"):
        publish(db, sources, artifact, apply=True, before_commit=fail)
    assert snapshot(db) == before


def test_malformed_history_cannot_partially_publish_other_rows():
    db, sources = fixture()
    with db.session() as session, session.begin():
        row = session.get(OccurrenceTaskAnnotation, "o-005")
        row.evidence = {**row.evidence, "semantic_scope_history": {TASK: "invalid"}}
    artifact = build(db, sources)
    before = snapshot(db)
    with pytest.raises(ValueError, match="HISTORY_INVALID"):
        publish(db, sources, artifact, apply=True, before_commit=lambda receipt: None)
    assert snapshot(db) == before


def test_cli_defaults_to_dry_run_and_writes_immutable_apply_and_rollback_evidence(tmp_path, monkeypatch, capsys):
    db, sources = fixture()
    artifact = build(db, sources)
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    for raw in sources.values():
        (snapshot_root / (hashlib.sha256(raw).hexdigest() + ".md")).write_bytes(raw)
    artifact_path = tmp_path / "review.json"
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("scope_import_cli", root / "scripts/import-semantic-scope.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from types import SimpleNamespace
    monkeypatch.setattr(module, "load_settings", lambda: SimpleNamespace(
        snapshot_root=snapshot_root, database_url="sqlite+pysqlite:///:memory:"))
    monkeypatch.setattr(module, "create_database", lambda url, create_tables=False: db)
    monkeypatch.setattr(db.engine, "dispose", lambda: None)
    args = [str(artifact_path), "--output-dir", str(tmp_path / "audit")]
    before = snapshot(db)
    assert module.main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "DRY_RUN"
    assert snapshot(db) == before
    assert module.main([*args, "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "APPLIED"
    directory = tmp_path / "audit" / artifact["sha256"]
    assert json.loads((directory / "manifest.json").read_text(encoding="utf-8")) == artifact
    assert json.loads((directory / "prepared-receipt.json").read_text(encoding="utf-8"))["status"] == "PREPARED"
    assert json.loads((directory / "rollback-evidence.json").read_text(encoding="utf-8"))["rollback_evidence"]
    assert module.main([*args, "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "UNCHANGED"
    with pytest.raises(ValueError, match="AUDIT_FILE_CONFLICT"):
        module.save_immutable(directory / "manifest.json", {"different": True})
