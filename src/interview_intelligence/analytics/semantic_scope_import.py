"""Validate and atomically publish a complete, independently reviewed task scope.

This module validates audit provenance and source bindings. It never classifies
questions, calls a model, or treats a valid hash as proof of semantic accuracy.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
from typing import Literal

from pydantic import Field, model_validator
from sqlalchemy import select

from interview_intelligence.analytics.semantic_scope import (
    ENGINEERING_DESIGN_TASK, SemanticTask, SemanticScopeIncomplete, _digest,
    load_semantic_scope, semantic_base_filters, semantic_input_hash, semantic_scope_version,
)
from interview_intelligence.analytics.stats import _active_from, _conditions
from interview_intelligence.contracts import FilterSpec, StrictModel
from interview_intelligence.domain.models import (
    CorpusState, OccurrenceTaskAnnotation, QuestionOccurrence, SourceRevision,
)
from interview_intelligence.ingestion.snapshot import decode_source


SCHEMA = "semantic_scope_import_v1"
RECEIPT_SCHEMA = "semantic_scope_import_receipt_v1"


def _require(condition, code):
    if not condition:
        raise ValueError(code)


class ScopeReviewer(StrictModel):
    id: str = Field(min_length=1, max_length=256)
    kind: Literal["agent", "human"]


class ScopeImportReview(StrictModel):
    occurrence_id: str = Field(min_length=1)
    decision: Literal["ACCEPT", "REJECT", "UNRESOLVED"]
    quote: str
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def meaningful_review(self):
        _require(bool(self.reason.strip()), "SEMANTIC_SCOPE_REVIEW_REASON_REQUIRED")
        if self.decision != "UNRESOLVED":
            _require(bool(self.quote.strip()), "SEMANTIC_SCOPE_REVIEW_QUOTE_REQUIRED")
        return self


class ScopeImportDecision(ScopeImportReview):
    canonical_question_id: str = Field(min_length=1)
    source_revision_id: str = Field(min_length=1)
    raw_file_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decoded_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ScopeImportManifest(StrictModel):
    occurrence_count: int = Field(ge=0, strict=True)
    occurrence_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bindings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ScopeImportPayload(StrictModel):
    schema_version: Literal["semantic_scope_import_v1"] = Field(alias="schema")
    task: SemanticTask
    version: str
    corpus_revision: int = Field(ge=0, strict=True)
    task_annotation_revision: int = Field(ge=0, strict=True)
    filters: FilterSpec
    reviewer: ScopeReviewer
    reviewed_at: str
    manifest: ScopeImportManifest
    decisions: list[ScopeImportDecision]

    @model_validator(mode="after")
    def audit_identity(self):
        _require(self.version == semantic_scope_version(self.task), "SEMANTIC_SCOPE_IMPORT_VERSION_INVALID")
        _require(bool(self.reviewer.id.strip()), "SEMANTIC_SCOPE_REVIEWER_INVALID")
        try:
            aware = datetime.fromisoformat(self.reviewed_at).utcoffset() is not None
        except ValueError:
            aware = False
        _require(aware, "SEMANTIC_SCOPE_REVIEW_TIMESTAMP_INVALID")
        semantic_base_filters(self.filters, task=self.task)
        return self


_BINDING_FIELDS = ("occurrence_id", "canonical_question_id", "source_revision_id",
                   "raw_file_hash", "decoded_text_hash", "input_hash")


def _binding(occurrence, revision):
    return {"occurrence_id": occurrence.id,
        "canonical_question_id": occurrence.canonical_question_id,
        "source_revision_id": revision.id, "raw_file_hash": revision.raw_file_hash,
        "decoded_text_hash": revision.decoded_text_hash,
        "input_hash": semantic_input_hash(occurrence, revision)}


def _manifest(bindings):
    ordered = sorted(bindings, key=lambda value: value["occurrence_id"])
    return {"occurrence_count": len(ordered),
        "occurrence_set_sha256": _digest([value["occurrence_id"] for value in ordered]),
        "bindings_sha256": _digest(ordered)}


def _source_basis(session, filters, source_reader):
    _require(callable(source_reader), "SEMANTIC_SCOPE_SOURCE_READER_REQUIRED")
    rows = session.execute(select(QuestionOccurrence, SourceRevision, OccurrenceTaskAnnotation)
        .select_from(_active_from()).outerjoin(OccurrenceTaskAnnotation,
            OccurrenceTaskAnnotation.occurrence_id == QuestionOccurrence.id)
        .where(*_conditions(filters)).order_by(QuestionOccurrence.id)).all()
    texts, bindings, objects = {}, [], {}
    for occurrence, revision, annotation in rows:
        if revision.id not in texts:
            try:
                raw = source_reader(revision)
            except OSError:
                raise ValueError("SEMANTIC_SCOPE_SOURCE_UNAVAILABLE") from None
            _require(isinstance(raw, bytes) and hashlib.sha256(raw).hexdigest() == revision.raw_file_hash,
                     "SEMANTIC_SCOPE_SOURCE_HASH_MISMATCH")
            try:
                text = decode_source(raw)
            except UnicodeError:
                raise ValueError("SEMANTIC_SCOPE_SOURCE_DECODE_INVALID") from None
            _require(hashlib.sha256(text.encode("utf-8")).hexdigest() == revision.decoded_text_hash,
                     "SEMANTIC_SCOPE_DECODED_HASH_MISMATCH")
            texts[revision.id] = text
        text = texts[revision.id]
        spans = occurrence.source_spans
        _require(isinstance(spans, list) and bool(spans), "SEMANTIC_SCOPE_SOURCE_SPAN_MISMATCH")
        for span in spans:
            _require(isinstance(span, dict), "SEMANTIC_SCOPE_SOURCE_SPAN_MISMATCH")
            start, end = span.get("start_char"), span.get("end_char")
            _require(span.get("revision_id") == revision.id and type(start) is int and type(end) is int
                and 0 <= start < end <= len(text) and text[start:end] == span.get("quote"),
                "SEMANTIC_SCOPE_SOURCE_SPAN_MISMATCH")
            for key, offset in (("start_line", start), ("end_line", end - 1)):
                _require(key not in span or type(span[key]) is int
                    and span[key] == text.count("\n", 0, offset) + 1, "SEMANTIC_SCOPE_SOURCE_LINE_MISMATCH")
        bindings.append(_binding(occurrence, revision))
        objects[occurrence.id] = (occurrence, revision, annotation)
    return bindings, objects


def _coverage(task, decisions, active_ids):
    ids = [value.occurrence_id for value in decisions]
    return {"task": task, "version": semantic_scope_version(task),
        "occurrences": len(active_ids), "accepted": sum(value.decision == "ACCEPT" for value in decisions),
        "rejected": sum(value.decision == "REJECT" for value in decisions),
        "unresolved": sum(value.decision == "UNRESOLVED" for value in decisions),
        "missing": len(set(active_ids) - set(ids)), "unexpected": len(set(ids) - set(active_ids)),
        "duplicates": len(ids) - len(set(ids)), "stale": 0, "invalid": 0, "complete": False}


def _require_coverage(coverage, *, permit_unresolved=False):
    keys = ("missing", "unexpected", "duplicates", "stale", "invalid")
    if any(coverage[key] for key in keys) or not permit_unresolved and coverage["unresolved"]:
        raise SemanticScopeIncomplete(coverage)


def build_scope_import_artifact(session, filters: FilterSpec, reviews, *, source_reader,
        reviewer, reviewed_at, task=ENGINEERING_DESIGN_TASK):
    """Freeze explicit reviews and exact source bindings; unresolved stays explicit."""
    version = semantic_scope_version(task)
    filters = semantic_base_filters(FilterSpec.model_validate(filters), task=task)
    reviews = [ScopeImportReview.model_validate(value) for value in reviews]
    bindings, objects = _source_basis(session, filters, source_reader)
    coverage = _coverage(task, reviews, objects)
    _require_coverage(coverage, permit_unresolved=True)
    by_id = {value["occurrence_id"]: value for value in bindings}
    decisions = [{**by_id[value.occurrence_id], **value.model_dump(mode="json")}
                 for value in sorted(reviews, key=lambda item: item.occurrence_id)]
    state = session.get(CorpusState, 1)
    _require(state is not None, "SEMANTIC_SCOPE_CORPUS_STATE_MISSING")
    payload = ScopeImportPayload.model_validate({"schema": SCHEMA, "task": task, "version": version,
        "corpus_revision": state.current_revision, "task_annotation_revision": state.task_annotation_revision,
        "filters": filters.model_dump(mode="json"), "reviewer": reviewer, "reviewed_at": reviewed_at,
        "manifest": _manifest(bindings), "decisions": decisions}).model_dump(mode="json", by_alias=True)
    artifact = {"payload": payload, "sha256": _digest(payload)}
    # Validate quotes now as well as at publication, while permitting review work
    # in progress to be saved with UNRESOLVED rather than inventing a negative.
    _validate(session, artifact, source_reader=source_reader, permit_unresolved=True)
    return artifact


def _decision(payload, item, artifact_sha):
    return {"version": payload.version, "decision": item.decision,
        "input_hash": item.input_hash, "quote": item.quote, "reason": item.reason,
        "reviewer_kind": payload.reviewer.kind, "review": {
            "reviewer": payload.reviewer.model_dump(mode="json"), "reviewed_at": payload.reviewed_at,
            "human_verified": payload.reviewer.kind == "human", "import_sha256": artifact_sha,
            "source_manifest_sha256": _digest(payload.manifest.model_dump(mode="json")),
            "source_scope_filters": payload.filters.model_dump(mode="json"),
            "corpus_revision": payload.corpus_revision}}


def _validate(session, artifact, *, source_reader, permit_unresolved=False):
    _require(isinstance(artifact, dict) and set(artifact) == {"payload", "sha256"}
        and artifact["sha256"] == _digest(artifact["payload"]), "SEMANTIC_SCOPE_IMPORT_DIGEST_MISMATCH")
    supplied_filters = artifact["payload"].get("filters") if isinstance(artifact["payload"], dict) else None
    _require(isinstance(supplied_filters, dict) and set(supplied_filters) == set(FilterSpec.model_fields),
             "SEMANTIC_SCOPE_IMPORT_FILTERS_INCOMPLETE")
    payload = ScopeImportPayload.model_validate(artifact["payload"])
    state = session.get(CorpusState, 1)
    _require(state is not None, "SEMANTIC_SCOPE_CORPUS_STATE_MISSING")
    _require(payload.corpus_revision == state.current_revision, "SEMANTIC_SCOPE_IMPORT_CORPUS_CHANGED")
    bindings, objects = _source_basis(session, payload.filters, source_reader)
    coverage = _coverage(payload.task, payload.decisions, objects)
    allow_unresolved = permit_unresolved or payload.task == ENGINEERING_DESIGN_TASK
    _require_coverage(coverage, permit_unresolved=allow_unresolved)
    _require(payload.manifest.model_dump(mode="json") == _manifest(bindings),
             "SEMANTIC_SCOPE_IMPORT_MANIFEST_CHANGED")
    by_id = {value["occurrence_id"]: value for value in bindings}
    changes = []
    for item in payload.decisions:
        occurrence, _, annotation = objects[item.occurrence_id]
        if {key: getattr(item, key) for key in _BINDING_FIELDS} != by_id[item.occurrence_id]:
            coverage["stale"] += 1
            continue
        if not item.quote.strip() or not any(item.quote in span["quote"] for span in occurrence.source_spans):
            coverage["invalid"] += 1
            continue
        evidence = annotation.evidence if annotation is not None else {}
        _require(isinstance(evidence, dict), "SEMANTIC_SCOPE_IMPORT_EXISTING_EVIDENCE_INVALID")
        scopes = evidence.get("semantic_scopes", {})
        _require(isinstance(scopes, dict), "SEMANTIC_SCOPE_IMPORT_EXISTING_EVIDENCE_INVALID")
        before = scopes.get(payload.task)
        after = _decision(payload, item, artifact["sha256"])
        if before != after:
            changes.append((occurrence.id, annotation, deepcopy(before), after))
    _require_coverage(coverage, permit_unresolved=allow_unresolved)
    # A repeated import is permitted after its own revision increment only when
    # every decision still carries this exact artifact's immutable provenance.
    _require(state.task_annotation_revision == payload.task_annotation_revision or not changes and bool(objects),
             "SEMANTIC_SCOPE_IMPORT_ANNOTATIONS_CHANGED")
    coverage["complete"] = not bool(coverage["unresolved"])
    return payload, state, coverage, changes


def validate_scope_import_artifact(session, artifact, *, source_reader):
    """Require exact reviewed sources; engineering UNRESOLVED stays visible pending.

    Algorithm publication still requires complete binary decisions. An explicit
    unresolved engineering review is not a missing source binding or a negative.
    """
    payload, state, coverage, changes = _validate(session, artifact, source_reader=source_reader)
    return {"schema": RECEIPT_SCHEMA, "status": "DRY_RUN", "artifact_sha256": artifact["sha256"],
        "task": payload.task, "version": payload.version, "corpus_revision": state.current_revision,
        "before_annotation_revision": state.task_annotation_revision,
        "after_annotation_revision": state.task_annotation_revision + bool(changes),
        "changed_annotations": len(changes), "new_annotations": sum(row[1] is None for row in changes),
        "coverage": coverage, "manifest": payload.manifest.model_dump(mode="json"), "model_calls": 0}


def _annotation_snapshot(annotation):
    if annotation is None:
        return None
    return {key: deepcopy(getattr(annotation, key)) for key in (
        "occurrence_id", "response_form", "coding_focus", "producer_version", "classification_status", "evidence")}


def import_semantic_scope(database, artifact, *, source_reader, apply=False, before_commit=None):
    """Publish under one DB transaction; a durable PREPARED audit precedes commit.

    File writes and a database commit have no cross-system atomicity. The callback
    saves prepared receipt/rollback evidence and must not label them committed.
    Only the returned APPLIED receipt confirms that the DB transaction committed.
    """
    _require(not apply or callable(before_commit), "SEMANTIC_SCOPE_IMPORT_RECEIPT_WRITER_REQUIRED")
    with database.session() as session, session.begin():
        if apply and session.bind.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if apply:
            session.get(CorpusState, 1, with_for_update=True)
        receipt = validate_scope_import_artifact(session, artifact, source_reader=source_reader)
        if not apply:
            return receipt
        payload, state, _, changes = _validate(session, artifact, source_reader=source_reader)
        if not changes:
            return {**receipt, "status": "UNCHANGED"}
        rollback = []
        for oid, annotation, previous, after in changes:
            rollback.append({"occurrence_id": oid, "before_annotation": _annotation_snapshot(annotation),
                             "after_scope_sha256": _digest(after)})
            if annotation is None:
                annotation = OccurrenceTaskAnnotation(occurrence_id=oid, response_form="UNKNOWN",
                    coding_focus="UNKNOWN", classification_status="UNKNOWN", producer_version=SCHEMA, evidence={})
                session.add(annotation)
            evidence = deepcopy(annotation.evidence)
            scopes = evidence.setdefault("semantic_scopes", {})
            history = evidence.setdefault("semantic_scope_history", {})
            _require(isinstance(history, dict), "SEMANTIC_SCOPE_IMPORT_HISTORY_INVALID")
            entries = history.setdefault(payload.task, [])
            _require(isinstance(entries, list), "SEMANTIC_SCOPE_IMPORT_HISTORY_INVALID")
            entries.append({"import_sha256": artifact["sha256"], "previous": previous,
                            "reviewed_at": payload.reviewed_at})
            scopes[payload.task] = after
            annotation.evidence = evidence
        state.task_annotation_revision += 1
        session.flush()
        scope = load_semantic_scope(session, payload.filters, task=payload.task)
        receipt.update(status="PREPARED", after_annotation_revision=state.task_annotation_revision,
                       semantic_scope=scope.metadata(), rollback_evidence=rollback)
        before_commit(deepcopy(receipt))
    return {**receipt, "status": "APPLIED"}
