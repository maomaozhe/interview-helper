"""Source-bound, explicit review amendments; projection is memory-SQLite only.

This is a review protocol, not a semantic classifier or a production publisher.
The unchanged v1 SQL evidence format is separate from the versioned review rule.
Unchanged decisions retain their original provenance and are not called v2 reviews.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
from typing import Literal

from pydantic import Field, model_validator
from sqlalchemy import select

from interview_intelligence.analytics.semantic_scope import (
    TASK, VERSION, _digest, load_semantic_scope, semantic_input_hash,
)
from interview_intelligence.analytics.stats import _active_from, _conditions
from interview_intelligence.contracts import FilterSpec, StrictModel
from interview_intelligence.domain.models import (
    CorpusState, OccurrenceTaskAnnotation, QuestionOccurrence, SourceRevision,
)
from interview_intelligence.ingestion.snapshot import decode_source

SCHEMA = "semantic_scope_amendment_v1"
RULE_VERSION = "algorithm_task_review_v2"
CRITERIA = (
    "Review the task in this occurrence, not its legacy label or component name. "
    "Accept an explicit computational operation with a checkable target: implement, "
    "derive or solve a transformation, traversal, parsing, query, data-structure "
    "operation or optimization. SQL and puzzle syntax are not automatic exclusions. "
    "A component name is not evidence against a self-contained computational task. "
    "Input/output words alone are insufficient: cite the actual operation and goal. "
    "Concept explanation, project recall and engineering-service behavior without "
    "a computational task are outside this confirmed scope. Incomplete tasks stay "
    "unconfirmed; do not borrow the task from another question or occurrence."
)


def _require(condition, code):
    if not condition:
        raise ValueError(code)


class ScopeReview(StrictModel):
    occurrence_id: str = Field(min_length=1)
    expected_decision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["ACCEPT", "REJECT"]
    task_kind: Literal["COMPUTATIONAL_TASK", "CONCEPT_ONLY", "ENGINEERING_BEHAVIOR", "INSUFFICIENT_EVIDENCE"]
    quote: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    operation_quote: str | None = None
    goal_quote: str | None = None

    @model_validator(mode="after")
    def explicit_computational_evidence(self):
        _require((self.decision == "ACCEPT") == (self.task_kind == "COMPUTATIONAL_TASK"),
                 "SEMANTIC_REVIEW_TASK_DECISION_CONFLICT")
        if self.decision == "ACCEPT":
            _require(bool(self.operation_quote and self.goal_quote), "SEMANTIC_REVIEW_TASK_EVIDENCE_REQUIRED")
        return self


def _snapshot(session):
    state = session.get(CorpusState, 1)
    _require(state is not None, "SEMANTIC_REVIEW_CORPUS_STATE_MISSING")
    return {key: getattr(state, key) for key in (
        "current_revision", "indexed_revision", "task_annotation_revision", "canonical_revision")}


def _basis(session, filters, source_reader):
    scope = load_semantic_scope(session, filters)
    rows = session.execute(select(QuestionOccurrence, SourceRevision, OccurrenceTaskAnnotation)
        .select_from(_active_from()).join(OccurrenceTaskAnnotation,
            OccurrenceTaskAnnotation.occurrence_id == QuestionOccurrence.id)
        .where(*_conditions(filters)).order_by(QuestionOccurrence.id)).all()
    texts, bindings, objects = {}, [], {}
    for occurrence, revision, annotation in rows:
        if revision.id not in texts:
            raw = source_reader(revision)
            _require(isinstance(raw, bytes) and hashlib.sha256(raw).hexdigest() == revision.raw_file_hash,
                     "SEMANTIC_REVIEW_SOURCE_HASH_MISMATCH")
            text = decode_source(raw)
            _require(hashlib.sha256(text.encode()).hexdigest() == revision.decoded_text_hash,
                     "SEMANTIC_REVIEW_DECODED_HASH_MISMATCH")
            texts[revision.id] = text
        text = texts[revision.id]
        for span in occurrence.source_spans:
            start, end = span.get("start_char"), span.get("end_char")
            _require(span.get("revision_id") == revision.id and type(start) is int and type(end) is int
                     and 0 <= start < end <= len(text) and text[start:end] == span.get("quote"),
                     "SEMANTIC_REVIEW_SOURCE_SPAN_MISMATCH")
            for key, offset in (("start_line", start), ("end_line", end - 1)):
                _require(key not in span or span[key] == text.count("\n", 0, offset) + 1,
                         "SEMANTIC_REVIEW_SOURCE_LINE_MISMATCH")
        decision = annotation.evidence["semantic_scopes"][TASK]
        bindings.append({"occurrence_id": occurrence.id, "canonical_question_id": occurrence.canonical_question_id,
            "source_revision_id": revision.id, "source_hash": revision.raw_file_hash,
            "input_hash": semantic_input_hash(occurrence, revision), "decision_sha256": _digest(decision)})
        objects[occurrence.id] = occurrence, annotation, decision
    return {"snapshot": _snapshot(session), "filters": filters.model_dump(mode="json"),
            "scope": scope.metadata(), "bindings": bindings}, objects


def build_amendment(session, filters: FilterSpec, reviews, *, source_reader, reviewer, reviewed_at):
    """Verify the full existing scope and freeze explicit, source-reviewed changes.

    source_reader receives SourceRevision and returns immutable raw bytes. No
    filesystem discovery, network, provider or private ID policy is embedded here.
    """
    _require(isinstance(reviewer, dict) and set(reviewer) == {"id", "kind"}
             and isinstance(reviewer["id"], str) and bool(reviewer["id"].strip())
             and reviewer["kind"] in {"agent", "human"}, "SEMANTIC_REVIEWER_INVALID")
    _require(isinstance(reviewed_at, str) and datetime.fromisoformat(reviewed_at).utcoffset() is not None,
             "SEMANTIC_REVIEW_TIMESTAMP_INVALID")
    reviews = [ScopeReview.model_validate(review) for review in reviews]
    _require(reviews and len({r.occurrence_id for r in reviews}) == len(reviews), "SEMANTIC_REVIEW_IDS_INVALID")
    basis, objects = _basis(session, filters, source_reader)
    changes = []
    for review in sorted(reviews, key=lambda r: r.occurrence_id):
        _require(review.occurrence_id in objects, "SEMANTIC_REVIEW_OUTSIDE_SCOPE")
        occurrence, _, previous = objects[review.occurrence_id]
        _require(_digest(previous) == review.expected_decision_sha256, "SEMANTIC_REVIEW_PREVIOUS_CHANGED")
        quotes = [s["quote"] for s in occurrence.source_spans]
        _require(any(review.quote in quote for quote in quotes), "SEMANTIC_REVIEW_QUOTE_NOT_IN_OCCURRENCE")
        for quote in (review.operation_quote, review.goal_quote):
            _require(quote is None or bool(quote.strip()) and quote in review.quote,
                     "SEMANTIC_REVIEW_TASK_QUOTE_NOT_IN_OCCURRENCE")
        _require(review.decision != previous["decision"], "SEMANTIC_REVIEW_NO_DECISION_CHANGE")
        after = {"version": VERSION, "input_hash": previous["input_hash"], "decision": review.decision,
            "quote": review.quote, "reason": review.reason, "reviewer_kind": reviewer["kind"],
            "review": {"rule_version": RULE_VERSION, "rule_sha256": _digest(CRITERIA),
                "reviewer": deepcopy(reviewer), "reviewed_at": reviewed_at,
                "human_verified": reviewer["kind"] == "human", "task_kind": review.task_kind,
                "operation_quote": review.operation_quote, "goal_quote": review.goal_quote}}
        changes.append({"review": review.model_dump(mode="json"), "before": deepcopy(previous), "after": after})
    payload = {"schema": SCHEMA, "task": TASK, "decision_format": VERSION, "rule_version": RULE_VERSION,
        "criteria": CRITERIA, "reviewer": deepcopy(reviewer), "reviewed_at": reviewed_at,
        "scope": "explicit reviewed amendments; unchanged decisions retain earlier review policy",
        "basis": basis, "changes": changes, "semantic_rereview_count": len(changes),
        "unchanged_decisions": basis["scope"]["occurrences"] - len(changes)}
    return {"payload": payload, "sha256": _digest(payload)}


def project_amendment(database, artifact, *, source_reader, before_commit):
    """Atomically project into an isolated memory DB; never a production publisher.

    before_commit must durably save the receipt; failures roll back the projection.
    Hashes prove consistency, not reviewer authority or semantic correctness.
    """
    url = database.engine.url
    _require(database.engine.dialect.name == "sqlite" and url.database in {None, "", ":memory:"}
             and not url.query, "SEMANTIC_PROJECTION_MEMORY_SQLITE_ONLY")
    _require(callable(before_commit), "SEMANTIC_PROJECTION_RECEIPT_WRITER_REQUIRED")
    _require(isinstance(artifact, dict) and set(artifact) == {"payload", "sha256"}
             and artifact["sha256"] == _digest(artifact["payload"]), "SEMANTIC_AMENDMENT_DIGEST_MISMATCH")
    payload = artifact["payload"]
    _require(payload.get("schema") == SCHEMA and payload.get("rule_version") == RULE_VERSION,
             "SEMANTIC_AMENDMENT_VERSION_INVALID")
    filters = FilterSpec.model_validate(payload["basis"]["filters"])
    with database.session() as session, session.begin():
        rebuilt = build_amendment(session, filters, [c["review"] for c in payload["changes"]],
            source_reader=source_reader, reviewer=payload["reviewer"], reviewed_at=payload["reviewed_at"])
        _require(rebuilt == artifact, "SEMANTIC_AMENDMENT_SCOPE_CHANGED")
        rollback = {}
        for change in payload["changes"]:
            oid = change["review"]["occurrence_id"]
            annotation = session.get(OccurrenceTaskAnnotation, oid)
            evidence = deepcopy(annotation.evidence)
            rollback[oid] = deepcopy(evidence)
            history = evidence.setdefault("semantic_scope_history", {}).setdefault(TASK, [])
            _require(isinstance(history, list), "SEMANTIC_REVIEW_HISTORY_INVALID")
            history.append({"amendment_sha256": artifact["sha256"], "previous": deepcopy(change["before"]),
                            "reviewed_at": payload["reviewed_at"], "rule_version": RULE_VERSION})
            evidence["semantic_scopes"][TASK] = deepcopy(change["after"])
            annotation.evidence = evidence
        state = session.get(CorpusState, 1)
        state.task_annotation_revision += 1
        session.flush()
        projected = load_semantic_scope(session, filters)
        receipt = {"schema": "semantic_scope_local_projection_v1", "status": "LOCAL_CANDIDATE_ONLY",
            "amendment_sha256": artifact["sha256"], "rule_version": RULE_VERSION,
            "before_snapshot": payload["basis"]["snapshot"], "after_snapshot": _snapshot(session),
            "before_scope": payload["basis"]["scope"], "after_scope": projected.metadata(),
            "rollback_evidence": rollback, "semantic_rereview_count": len(payload["changes"]),
            "unchanged_decisions": payload["unchanged_decisions"], "model_calls": 0, "production_changed": False}
        before_commit(deepcopy(receipt))
    return receipt
