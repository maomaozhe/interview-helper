"""Complete, evidence-bound occurrence scopes for semantic SQL lists."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Literal

from sqlalchemy import select

from interview_intelligence.analytics.stats import _active_from, _conditions
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    OccurrenceTaskAnnotation, QuestionOccurrence, SourceRevision,
)


TASK = "ALGORITHM_PROBLEM"
VERSION = "algorithm_problem_scope_v1"
ENGINEERING_DESIGN_TASK = "ENGINEERING_DESIGN"
ENGINEERING_DESIGN_VERSION = "engineering_design_scope_v1"
SemanticTask = Literal["ALGORITHM_PROBLEM", "ENGINEERING_DESIGN"]
SEMANTIC_TASK_VERSIONS = {
    TASK: VERSION,
    ENGINEERING_DESIGN_TASK: ENGINEERING_DESIGN_VERSION,
}
_SHA256 = re.compile(r"[0-9a-f]{64}")


def semantic_scope_version(task: str) -> str:
    """Each audited meaning has an independent decision-format version."""
    if task not in SEMANTIC_TASK_VERSIONS:
        raise ValueError("SEMANTIC_SCOPE_TASK_INVALID")
    return SEMANTIC_TASK_VERSIONS[task]


def _digest(payload) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def semantic_input_hash(occurrence, revision) -> str:
    """Bind a decision to its occurrence and immutable decoded source snapshot.

    SourceRevision.decoded_text_hash is the SHA-256 of decode_source(raw_bytes)
    encoded as UTF-8; it is the text_snapshot_sha256 used by the audit protocol.
    A source audit must verify raw_file_hash, decoded_text_hash and source spans
    before writing its decision. SQL subsequently checks that input is unchanged.
    """
    snapshot_hash = revision.decoded_text_hash
    spans = occurrence.source_spans
    if (not isinstance(snapshot_hash, str) or not _SHA256.fullmatch(snapshot_hash)
            or not isinstance(spans, list) or not spans
            or any(not isinstance(span, dict) for span in spans)):
        raise ValueError("SEMANTIC_SCOPE_INPUT_INVALID")
    return _digest({
        "occurrence_id": occurrence.id,
        "raw_question": occurrence.raw_question,
        "source_spans": spans,
        "source_revision_id": revision.id,
        "text_snapshot_sha256": snapshot_hash,
    })


def semantic_base_filters(filters: FilterSpec, *, task: str = TASK) -> FilterSpec:
    """Reject conflicting explicit legacy labels rather than silently clearing them."""
    if task == TASK and (filters.coding_focus is not None or filters.annotation_status is not None):
        raise ValueError("SEMANTIC_SCOPE_LEGACY_TASK_FILTER_CONFLICT: "
                         "semantic_task cannot combine coding_focus or annotation_status")
    return filters


@dataclass(frozen=True)
class SemanticScope:
    task: str
    version: str
    scope_hash: str
    accepted_occurrence_ids: tuple[str, ...]
    occurrences: int
    rejected: int
    pending_occurrence_ids: tuple[str, ...] = ()
    pending_records: tuple[dict, ...] = ()

    @property
    def selected_occurrence_ids(self) -> tuple[str, ...]:
        """Visible engineering union; pending is never positive evidence."""
        return self.accepted_occurrence_ids + self.pending_occurrence_ids

    def metadata(self) -> dict:
        result = {"task": self.task, "version": self.version,
                "scope_hash": self.scope_hash, "occurrences": self.occurrences,
                "accepted": len(self.accepted_occurrence_ids),
                "rejected": self.rejected, "complete": not self.pending_occurrence_ids}
        if self.pending_occurrence_ids:
            result.update(pending=len(self.pending_occurrence_ids), **{
                category.lower(): sum(row["status"] == category for row in self.pending_records)
                for category in ("UNRESOLVED", "MISSING", "STALE", "INVALID")})
        return result


class SemanticScopeIncomplete(ValueError):
    """No partial ranked result may stand in for the requested complete scope."""
    def __init__(self, coverage):
        super().__init__("SEMANTIC_SCOPE_INCOMPLETE")
        self.coverage = coverage


def load_semantic_scope(session, filters: FilterSpec, *, task: str = TASK) -> SemanticScope:
    """Validate every active matching occurrence, including semantic negatives.

    Evidence format (kept alongside, rather than replacing, legacy labels):
    annotation.evidence['semantic_scopes'][task] = {
        'version': semantic_scope_version(task), 'decision': 'ACCEPT' or 'REJECT',
        'input_hash': semantic_input_hash(occurrence, revision),
        'quote': a contiguous original-question/source-span quote,
        'reason': a nonempty explanation grounded in that occurrence,
    }
    """
    version = semantic_scope_version(task)
    filters = semantic_base_filters(filters, task=task)
    rows = session.execute(select(QuestionOccurrence, SourceRevision, OccurrenceTaskAnnotation)
        .select_from(_active_from()).outerjoin(OccurrenceTaskAnnotation,
            OccurrenceTaskAnnotation.occurrence_id == QuestionOccurrence.id)
        .where(*_conditions(filters)).order_by(QuestionOccurrence.id))
    accepted, decisions, pending = [], [], []
    coverage = {"task": task, "version": version, "occurrences": 0,
                "accepted": 0, "rejected": 0, "missing": 0,
                "stale": 0, "invalid": 0, "unresolved": 0, "complete": False}
    for occurrence, revision, annotation in rows:
        coverage["occurrences"] += 1
        evidence = annotation.evidence if annotation else None
        scopes = evidence.get("semantic_scopes") if isinstance(evidence, dict) else None
        decision = scopes.get(task) if isinstance(scopes, dict) else None
        status, pending_reason, expected_hash = None, None, None
        try:
            expected_hash = semantic_input_hash(occurrence, revision)
        except ValueError:
            status, pending_reason = "INVALID", "当前提问的来源绑定或原文位置无效，不能沿用旧审核结论。"
        if not isinstance(decision, dict):
            status, pending_reason = status or "MISSING", pending_reason or "当前提问尚无本任务的语义审核结论。"
        elif not status and (decision.get("version") != version or decision.get("input_hash") != expected_hash):
            status, pending_reason = "STALE", "语义审核版本或输入指纹已变化，需要按当前原问重新审核。"
        quote, reason = (decision.get("quote"), decision.get("reason")) if isinstance(decision, dict) else (None, None)
        spans = occurrence.source_spans if isinstance(occurrence.source_spans, list) else []
        original = [occurrence.raw_question, *[span.get("quote", "")
                    for span in spans if isinstance(span, dict)]]
        if not status and (decision.get("decision") not in ("ACCEPT", "REJECT", "UNRESOLVED")
                or not isinstance(quote, str) or not quote.strip()
                or not any(quote in text for text in original if isinstance(text, str))
                or not isinstance(reason, str) or not reason.strip()):
            status, pending_reason = "INVALID", "审核结构或连续原文引文无效；不能将异常当作语义排除。"
        if not status and decision.get("decision") == "UNRESOLVED":
            status, pending_reason = "UNRESOLVED", reason
        if status:
            coverage[status.lower()] += 1
            if task == ENGINEERING_DESIGN_TASK:
                record = {"occurrence_id": occurrence.id, "status": status,
                    "reason": pending_reason, "input_hash": expected_hash,
                    "audit_input_hash": decision.get("input_hash") if isinstance(decision, dict) else None}
                pending.append(record)
                # Bind invalid/absent decisions and current source too. Merely
                # changing an unresolved reason or a formerly invalid negative
                # must invalidate every cursor even without a revision bump.
                decisions.append({**record, "decision": "PENDING", "audit": decision,
                    "current_input_fingerprint": _digest({"raw_question": occurrence.raw_question,
                        "source_spans": occurrence.source_spans, "source_revision_id": revision.id,
                        "decoded_text_hash": revision.decoded_text_hash})})
            continue
        positive = decision["decision"] == "ACCEPT"
        coverage["accepted" if positive else "rejected"] += 1
        if positive:
            accepted.append(occurrence.id)
        decisions.append({"occurrence_id": occurrence.id, "input_hash": expected_hash,
                          "decision": decision["decision"], "quote": quote, "reason": reason})
    if task != ENGINEERING_DESIGN_TASK and any(coverage[key] for key in ("missing", "stale", "invalid", "unresolved")):
        # Algorithm remains a strict, fully reviewed collection. In particular,
        # introducing engineering pending visibility must not soften its gate.
        coverage["invalid"] += coverage.pop("unresolved")
        raise SemanticScopeIncomplete(coverage)
    return SemanticScope(task=task, version=version,
        scope_hash=_digest({"task": task, "version": version, "decisions": decisions}),
        accepted_occurrence_ids=tuple(accepted), occurrences=coverage["occurrences"],
        rejected=coverage["rejected"], pending_occurrence_ids=tuple(row["occurrence_id"] for row in pending),
        pending_records=tuple(pending))
