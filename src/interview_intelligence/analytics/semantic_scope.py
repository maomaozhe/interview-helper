"""Complete, evidence-bound occurrence scopes for semantic SQL lists."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from sqlalchemy import select

from interview_intelligence.analytics.stats import _active_from, _conditions
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    OccurrenceTaskAnnotation, QuestionOccurrence, SourceRevision,
)


TASK = "ALGORITHM_PROBLEM"
VERSION = "algorithm_problem_scope_v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")


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


def semantic_base_filters(filters: FilterSpec) -> FilterSpec:
    """Reject conflicting explicit legacy labels rather than silently clearing them."""
    if filters.coding_focus is not None or filters.annotation_status is not None:
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

    def metadata(self) -> dict:
        return {"task": self.task, "version": self.version,
                "scope_hash": self.scope_hash, "occurrences": self.occurrences,
                "accepted": len(self.accepted_occurrence_ids),
                "rejected": self.rejected, "complete": True}


class SemanticScopeIncomplete(ValueError):
    """No partial ranked result may stand in for the requested complete scope."""
    def __init__(self, coverage):
        super().__init__("SEMANTIC_SCOPE_INCOMPLETE")
        self.coverage = coverage


def load_semantic_scope(session, filters: FilterSpec, *, task: str = TASK) -> SemanticScope:
    """Validate every active matching occurrence, including semantic negatives.

    Evidence format (kept alongside, rather than replacing, legacy labels):
    annotation.evidence['semantic_scopes']['ALGORITHM_PROBLEM'] = {
        'version': VERSION, 'decision': 'ACCEPT' or 'REJECT',
        'input_hash': semantic_input_hash(occurrence, revision),
        'quote': a contiguous original-question/source-span quote,
        'reason': a nonempty explanation grounded in that occurrence,
    }
    """
    if task != TASK:
        raise ValueError("SEMANTIC_SCOPE_TASK_INVALID")
    filters = semantic_base_filters(filters)
    rows = session.execute(select(QuestionOccurrence, SourceRevision, OccurrenceTaskAnnotation)
        .select_from(_active_from()).outerjoin(OccurrenceTaskAnnotation,
            OccurrenceTaskAnnotation.occurrence_id == QuestionOccurrence.id)
        .where(*_conditions(filters)).order_by(QuestionOccurrence.id))
    accepted, decisions = [], []
    coverage = {"task": task, "version": VERSION, "occurrences": 0,
                "accepted": 0, "rejected": 0, "missing": 0,
                "stale": 0, "invalid": 0, "complete": False}
    for occurrence, revision, annotation in rows:
        coverage["occurrences"] += 1
        evidence = annotation.evidence if annotation else None
        scopes = evidence.get("semantic_scopes") if isinstance(evidence, dict) else None
        decision = scopes.get(task) if isinstance(scopes, dict) else None
        if not isinstance(decision, dict):
            coverage["missing"] += 1
            continue
        try:
            expected_hash = semantic_input_hash(occurrence, revision)
        except ValueError:
            coverage["invalid"] += 1
            continue
        if decision.get("version") != VERSION or decision.get("input_hash") != expected_hash:
            coverage["stale"] += 1
            continue
        quote, reason = decision.get("quote"), decision.get("reason")
        original = [occurrence.raw_question, *[span.get("quote", "")
                    for span in occurrence.source_spans if isinstance(span, dict)]]
        if (decision.get("decision") not in ("ACCEPT", "REJECT")
                or not isinstance(quote, str) or not quote.strip()
                or not any(quote in text for text in original if isinstance(text, str))
                or not isinstance(reason, str) or not reason.strip()):
            coverage["invalid"] += 1
            continue
        positive = decision["decision"] == "ACCEPT"
        coverage["accepted" if positive else "rejected"] += 1
        if positive:
            accepted.append(occurrence.id)
        decisions.append({"occurrence_id": occurrence.id, "input_hash": expected_hash,
                          "decision": decision["decision"], "quote": quote, "reason": reason})
    if coverage["missing"] or coverage["stale"] or coverage["invalid"]:
        raise SemanticScopeIncomplete(coverage)
    return SemanticScope(task=task, version=VERSION,
        scope_hash=_digest({"task": task, "version": VERSION, "decisions": decisions}),
        accepted_occurrence_ids=tuple(accepted), occurrences=coverage["occurrences"],
        rejected=coverage["rejected"])
