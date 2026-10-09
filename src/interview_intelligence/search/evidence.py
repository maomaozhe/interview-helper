"""Bounded, source-verified context for semantic candidate verification."""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select

from interview_intelligence.agent.task_context import compile_task_context
from interview_intelligence.analytics.stats import _active_from, _conditions
from interview_intelligence.domain.models import QuestionOccurrence, SourceRevision
from interview_intelligence.ingestion.snapshot import decode_source


VERSION = "candidate_source_context_v1"


def _clip(text, budget, *, tail=False):
    raw = text.encode("utf-8")
    return (raw[-budget:] if tail else raw[:budget]).decode("utf-8", errors="ignore") if budget else ""


def candidate_source_context(session, canonical_ids, filters, *, snapshot_root=None):
    """Use only matching occurrences; neighboring text is context, not evidence.

    At most two occurrences and 500 UTF-8 bytes of text per canonical keep a
    Top50 batch within the model request budget. Missing/corrupt snapshots never
    become source evidence. The original question is still available separately.
    """
    if not canonical_ids:
        return {}
    if len(canonical_ids) > 50 or len(set(canonical_ids)) != len(canonical_ids):
        raise ValueError("invalid candidate context IDs")
    rows = list(session.execute(select(QuestionOccurrence, SourceRevision)
        .select_from(_active_from()).where(*_conditions(filters),
            QuestionOccurrence.canonical_question_id.in_(canonical_ids))
        .order_by(QuestionOccurrence.canonical_question_id, QuestionOccurrence.id)))
    if snapshot_root is None:
        from interview_intelligence.config import load_settings
        snapshot_root = load_settings().snapshot_root
    cache, grouped = {}, {cid: [] for cid in canonical_ids}
    for occurrence, revision in rows:
        bucket = grouped[occurrence.canonical_question_id]
        if len(bucket) >= 2:
            continue
        if revision.id not in cache:
            path = Path(snapshot_root) / f"{revision.raw_file_hash}.md"
            try:
                raw = path.read_bytes()
                cache[revision.id] = decode_source(raw) if hashlib.sha256(raw).hexdigest() == revision.raw_file_hash else None
            except (OSError, UnicodeError):
                cache[revision.id] = None
        context, status = {}, "UNAVAILABLE"
        if cache[revision.id] is not None:
            try:
                context = compile_task_context(cache[revision.id], occurrence.source_spans)
                status = "VERIFIED"
            except ValueError:
                status = "INVALID_SPANS"
        bucket.append({"occurrence_id": occurrence.id, "revision_id": revision.id,
            "original_question": occurrence.raw_question, "context_before": context.get("context_before", ""),
            "context_after": context.get("context_after", ""), "source_context_status": status})
    result = {}
    for cid, occurrences in grouped.items():
        if not occurrences:
            continue
        budget = 500 // len(occurrences)
        for item in occurrences:
            item["original_question"] = _clip(item["original_question"], budget * 2 // 5)
            item["context_before"] = _clip(item["context_before"], budget // 2, tail=True)
            item["context_after"] = _clip(item["context_after"], budget // 10)
        result[cid] = {"source_context_version": VERSION, "source_context": occurrences}
    return result
