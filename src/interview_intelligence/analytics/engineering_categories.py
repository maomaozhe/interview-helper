"""Lossless browsing categories for an audited engineering collection.

These are existing taxonomy labels, not new semantic claims. Category membership
is assigned to canonical questions, so choosing a category never loses another
visible occurrence of the same question.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from typing import Iterable

from sqlalchemy import select

from interview_intelligence.analytics.semantic_scope import (
    ENGINEERING_DESIGN_TASK, SemanticScope, load_semantic_scope,
)
from interview_intelligence.analytics.stats import _active_from, _conditions
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import QuestionOccurrence, SourceRevision
from interview_intelligence.taxonomy import load_taxonomy


VERSION = "engineering_l1_browse_v1"
PENDING_CATEGORY_ID = "pending"
UNCLEAR_CATEGORY_ID = "unclear"


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _counts(rows, accepted: set[str]) -> dict:
    confirmed = {row.canonical_question_id for row in rows if row.id in accepted}
    pending = {row.canonical_question_id for row in rows if row.id not in accepted}
    return {
        "confirmed": len(confirmed),
        "pending": len(pending),
        "pending_only": len(pending - confirmed),
        "visible": len(confirmed | pending),
        "confirmed_occurrences": sum(row.id in accepted for row in rows),
        "pending_occurrences": sum(row.id not in accepted for row in rows),
        "visible_occurrences": len(rows),
    }


def engineering_collection_categories(session, filters: FilterSpec, *,
        semantic_scope: SemanticScope | None = None,
        canonical_ids: Iterable[str] | None = None) -> dict:
    """Preview an exact, disjoint partition of the visible source-bound scope.

    ``canonical_ids`` can restrict browsing to a previously ranked/review-filtered
    result, but must be a subset of this current scope. It does not filter topic
    occurrences: each selected canonical retains all its matching source records.
    A supplied scope is checked against current facts before it is reused.
    """
    # A ListRequest is a FilterSpec subclass; page cursors and display settings
    # must not accidentally become part of the source-filter/category identity.
    filters = FilterSpec.model_validate({key: getattr(filters, key)
        for key in FilterSpec.model_fields})
    current = load_semantic_scope(session, filters, task=ENGINEERING_DESIGN_TASK)
    if semantic_scope is not None and (semantic_scope.task != ENGINEERING_DESIGN_TASK
            or semantic_scope.scope_hash != current.scope_hash):
        raise ValueError("SNAPSHOT_CHANGED")
    scope = current
    accepted = set(scope.accepted_occurrence_ids)
    rows = list(session.execute(select(QuestionOccurrence.id,
        QuestionOccurrence.canonical_question_id, QuestionOccurrence.topic_id,
        QuestionOccurrence.taxonomy_version, SourceRevision.id.label("source_revision_id"))
        .select_from(_active_from()).where(*_conditions(filters),
            QuestionOccurrence.id.in_(scope.selected_occurrence_ids))
        .order_by(QuestionOccurrence.id)))
    visible_ids = {row.canonical_question_id for row in rows}
    selected_ids = visible_ids if canonical_ids is None else set(canonical_ids)
    if not selected_ids <= visible_ids:
        raise ValueError("INVALID_CATEGORY_SCOPE")
    rows = [row for row in rows if row.canonical_question_id in selected_ids]
    taxonomy = load_taxonomy()
    labels = {topic_id: l1 for l1, children in taxonomy.topics.items()
        for topic_id in children.values()}
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.canonical_question_id].append(row)
    buckets, reasons = defaultdict(list), {}
    names = {}
    for canonical_id, occurrences in sorted(grouped.items()):
        if any(row.id not in accepted for row in occurrences):
            category_id = PENDING_CATEGORY_ID
        else:
            invalid = set()
            if any(row.taxonomy_version != taxonomy.version for row in occurrences):
                invalid.add("TAXONOMY_VERSION_MISMATCH")
            if any(not row.topic_id or row.topic_id not in labels for row in occurrences):
                invalid.add("UNKNOWN_TOPIC")
            if any(row.topic_id == "other.unknown" for row in occurrences):
                invalid.add("UNCLASSIFIED_TOPIC")
            l1 = {labels[row.topic_id] for row in occurrences if row.topic_id in labels}
            if len(l1) > 1:
                invalid.add("MULTIPLE_L1_TOPICS")
            if invalid or len(l1) != 1:
                category_id = UNCLEAR_CATEGORY_ID
                reasons[canonical_id] = sorted(invalid or {"UNKNOWN_TOPIC"})
            else:
                label = next(iter(l1))
                category_id = "topic:" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:16]
                names[category_id] = label
        buckets[category_id].extend(occurrences)
    categories = []
    for category_id in sorted(buckets, key=lambda key: (
            1 if key == UNCLEAR_CATEGORY_ID else 2 if key == PENDING_CATEGORY_ID else 0,
            names.get(key, key))):
        members = buckets[category_id]
        ids = sorted({row.canonical_question_id for row in members})
        kind = "PENDING" if category_id == PENDING_CATEGORY_ID else (
            "UNCLEAR" if category_id == UNCLEAR_CATEGORY_ID else "TOPIC_L1")
        categories.append({"id": category_id, "label": "待核验" if kind == "PENDING"
            else "分类不明确" if kind == "UNCLEAR" else names[category_id],
            "kind": kind, "canonical_ids": ids,
            "occurrence_ids": sorted(row.id for row in members),
            "counts": _counts(members, accepted),
            "classification_issues": {key: reasons[key] for key in ids if key in reasons}})
    bindings = [{"occurrence_id": row.id, "canonical_question_id": row.canonical_question_id,
        "source_revision_id": row.source_revision_id, "topic_id": row.topic_id,
        "taxonomy_version": row.taxonomy_version,
        "verification": "CONFIRMED" if row.id in accepted else "PENDING"} for row in rows]
    category_hash = _digest({"version": VERSION, "scope_hash": scope.scope_hash,
        "taxonomy_version": taxonomy.version, "taxonomy": taxonomy.topics,
        "filters": filters.model_dump(mode="json"), "bindings": bindings,
        "canonical_ids": sorted(selected_ids), "categories": categories})
    return {"version": VERSION, "taxonomy_version": taxonomy.version,
        "scope_hash": scope.scope_hash, "category_hash": category_hash,
        "counts": _counts(rows, accepted), "categories": categories,
        "classification_basis": "existing_occurrence_taxonomy_l1",
        "classification_notice": "按已有主题组织浏览；分类不明确和待核验题单独保留，分类不构成新的题意审核结论。"}
