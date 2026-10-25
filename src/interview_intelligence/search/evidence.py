"""Bounded, source-verified context for semantic candidate verification."""
from __future__ import annotations

import hashlib
import json
import base64
from copy import deepcopy
from pathlib import Path

from sqlalchemy import select

from interview_intelligence.agent.task_context import compile_task_context
from interview_intelligence.analytics.stats import _active_from, _conditions
from interview_intelligence.domain.models import (
    QuestionOccurrence, SourceRevision, Interview, DocumentBuild, SourceDocument, QuestionRelation,
)
from interview_intelligence.ingestion.snapshot import decode_source
from interview_intelligence.search.referents import (
    build_reference_frame, build_source_item_frame, seal_source_record, content_hash, explicit_reference_anchor,
)


VERSION = "candidate_source_context_v4"


def _clip(text, budget, *, tail=False):
    raw = text.encode("utf-8")
    return (raw[-budget:] if tail else raw[:budget]).decode("utf-8", errors="ignore") if budget else ""


def _fair_budgets(lengths, budget):
    """Share a byte budget, donating unused shares from shorter text."""
    shares = [0] * len(lengths)
    for position, index in enumerate(sorted(range(len(lengths)), key=lengths.__getitem__)):
        share = min(lengths[index], budget // (len(lengths) - position))
        shares[index] = share
        budget -= share
    return shares


def _bound_occurrences(occurrences):
    """Keep each original as a continuous prefix; neighbors use only leftovers."""
    originals = [item["original_question"] for item in occurrences]
    shares = _fair_budgets([len(text.encode("utf-8")) for text in originals], 500)
    for item, original, share in zip(occurrences, originals, shares):
        item["original_question"] = _clip(original, share)
        # VERIFIED proves source identity/spans, not that this bounded view
        # contains the complete question. Never join a head and tail into one.
        item["original_question_complete"] = item["original_question"] == original
    remaining = 500 - sum(len(item["original_question"].encode("utf-8")) for item in occurrences)
    # Source references have priority over display-only neighbors, but never
    # over either current original. Whole nearest-first antecedents only.
    for item in occurrences:
        if "source_active" in item:
            item["source_identity_hash"] = seal_source_record(item)["source_identity_hash"]
    reference_needs = [sum(len(record["original_question"].encode("utf-8"))
        for record in item.get("_reference_input", {}).get("preceding", [])[:4]) for item in occurrences]
    item_proposals = [build_source_item_frame(item, item.get("_source_item_text"), text_budget=500)
        if "_source_item_text" in item else None for item in occurrences]
    item_needs = [proposal["frame_text_bytes"] if proposal else 0 for proposal in item_proposals]
    needs = [left + right for left, right in zip(reference_needs, item_needs)]
    for index, (item, share) in enumerate(zip(occurrences, _fair_budgets(needs, remaining))):
        reference = item.pop("_reference_input", None)
        item_text = item.pop("_source_item_text", None)
        reference_share, item_share = _fair_budgets([reference_needs[index], item_needs[index]], share)
        item_frame = build_source_item_frame(item, item_text, source_context_index=index, text_budget=item_share) if item_text else None
        if item_frame:
            item["source_item_structure_hash"] = item_frame["source_item_structure_hash"]
        if "source_active" in item:
            item["source_identity_hash"] = seal_source_record(item)["source_identity_hash"]
        if item_frame:
            item_frame["current_source_identity_hash"] = item["source_identity_hash"]
            for qualifier in item_frame["qualifiers"]:
                qualifier["current_source_identity_hash"] = item["source_identity_hash"]
                qualifier.pop("source_identity_hash", None)
            item_frame["frame_hash"] = content_hash({key: value for key, value in item_frame.items() if key != "frame_hash"})
            item["source_item_frame"] = item_frame
        if reference is not None:
            item["reference_frame"] = build_reference_frame(item, reference["preceding"], reference["relations"],
                source_context_index=index, text_budget=reference_share, source_text=reference["source_text"])
    remaining -= sum(sum(item.get(key, {}).get("frame_text_bytes", 0) for key in ("reference_frame", "source_item_frame")) for item in occurrences)
    # Reclaimed original space must not enlarge neighboring questions for
    # short originals. Retain their previous field caps before sharing leftovers.
    neighbor_share = 500 // len(occurrences)
    for item in occurrences:
        item["context_before"] = _clip(item["context_before"], neighbor_share // 2, tail=True)
        item["context_after"] = _clip(item["context_after"], neighbor_share // 10)
    context_lengths = [sum(len(item[key].encode("utf-8")) for key in
                           ("context_before", "context_after")) for item in occurrences]
    for item, share in zip(occurrences, _fair_budgets(context_lengths, remaining)):
        # Preserve the previous preference for preceding context, but only
        # after original questions. Context never becomes question evidence.
        before_budget = max(share * 5 // 6, share - len(item["context_after"].encode("utf-8")))
        before = _clip(item["context_before"], before_budget, tail=True)
        after = _clip(item["context_after"], share - len(before.encode("utf-8")))
        item["context_before"], item["context_after"] = before, after


def _source_record(occurrence, revision, build_id, source_text):
    """Bind an original ask to one active immutable source; no canonical text."""
    def field(value, key):
        return value.get(key) if isinstance(value, dict) else getattr(value, key)
    context, status = {}, "UNAVAILABLE"
    spans = deepcopy(field(occurrence, "source_spans"))
    revision_id = field(revision, "id")
    original = field(occurrence, "raw_question")
    if source_text is not None:
        try:
            if (type(spans) is not list or not spans or any(type(span) is not dict
                    or type(span.get("start_char")) is not int or type(span.get("end_char")) is not int
                    or type(span.get("quote")) is not str or not span["quote"].strip() for span in spans)):
                raise ValueError("INVALID_SOURCE_SPAN_STRUCTURE")
            if any(span.get("revision_id", revision_id) != revision_id for span in spans):
                raise ValueError("SOURCE_REVISION_MISMATCH")
            context = compile_task_context(source_text, spans)
            # Valid source positions authenticate their quoted text, not an
            # unrelated raw ask stored beside it. One continuous exact subspan
            # is allowed; joins across spans and canonical rewrites are not.
            if type(original) is not str or not original.strip() or not any(original in span["quote"] for span in spans):
                raise ValueError("ORIGINAL_QUESTION_SOURCE_MISMATCH")
            status = "VERIFIED"
        except ValueError:
            context, status = {}, "INVALID_SPANS"
    record = {"occurrence_id": field(occurrence, "id"), "revision_id": revision_id,
        "source_interview_id": field(occurrence, "interview_id"), "build_id": build_id,
        "question_order": field(occurrence, "question_order"), "source_spans": spans,
        "source_sha256": field(revision, "raw_file_hash"),
        "decoded_text_sha256": hashlib.sha256(source_text.encode()).hexdigest() if source_text is not None else None,
        "source_active": True, "original_question": original,
        "original_question_complete": True, "context_before": context.get("context_before", ""),
        "context_after": context.get("context_after", ""), "source_context_status": status}
    return seal_source_record(record)


def _assemble_contexts(grouped, records, relations, source_texts, *, include_coverage=False, qualified=None):
    by_interview = {}
    for record in records.values():
        by_interview.setdefault(record["source_interview_id"], []).append(record)
    for sequence in by_interview.values():
        sequence.sort(key=lambda record: (record["question_order"], record["occurrence_id"]))
    result = {}
    for cid, selected in grouped.items():
        if not selected:
            continue
        occurrences = [deepcopy(record) for record in selected[:2]]
        for record in occurrences:
            record["_source_item_text"] = source_texts.get(record["revision_id"])
            if explicit_reference_anchor(record["original_question"]):
                sequence = by_interview.get(record["source_interview_id"], [])
                # Keep the real physical prefix. Never skip an invalid/large
                # nearest ask to reach an attractive older business noun.
                preceding = list(reversed([other for other in sequence
                    if other["question_order"] < record["question_order"]]))
                record["source_reference_preceding_ids"] = [other["occurrence_id"] for other in preceding[:4]]
                record["source_identity_hash"] = seal_source_record(record)["source_identity_hash"]
                record["_reference_input"] = {"preceding": preceding, "relations": relations,
                    "source_text": source_texts.get(record["revision_id"])}
        _bound_occurrences(occurrences)
        result[cid] = {"source_context_version": VERSION, "source_context": occurrences}
        if include_coverage:
            ids = sorted(qualified[cid])
            verified = [item["occurrence_id"] for item in occurrences
                if item["source_context_status"] == "VERIFIED" and item["original_question_complete"]]
            result[cid]["source_context_coverage"] = {"version": "candidate_source_coverage_v1",
                "qualified_occurrence_count": len(ids), "qualified_occurrence_ids": ids,
                "qualified_occurrence_ids_hash": hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest(),
                "selected_occurrence_ids": [item["occurrence_id"] for item in occurrences],
                "verified_complete_occurrence_ids": verified,
                "omitted_occurrence_count": len(ids) - len(occurrences),
                "incomplete_original_count": sum(not item["original_question_complete"] for item in occurrences),
                "complete": set(ids) == set(verified)}
    return result


def candidate_source_context(session, canonical_ids, filters, *, snapshot_root=None, occurrence_ids=None,
        include_coverage=False):
    """Use only matching occurrences; neighboring text is context, not evidence.

    At most two occurrences and 500 UTF-8 bytes of text per canonical keep a
    Top50 batch within the model request budget. Missing/corrupt snapshots never
    become source evidence. The original question is still available separately.
    """
    if not canonical_ids:
        return {}
    if len(canonical_ids) > 50 or len(set(canonical_ids)) != len(canonical_ids):
        raise ValueError("invalid candidate context IDs")
    conditions = _conditions(filters)
    if occurrence_ids is not None:
        conditions.append(QuestionOccurrence.id.in_(occurrence_ids))
    rows = list(session.execute(select(QuestionOccurrence, SourceRevision)
        .select_from(_active_from()).where(*conditions,
            QuestionOccurrence.canonical_question_id.in_(canonical_ids))
        .order_by(QuestionOccurrence.canonical_question_id, QuestionOccurrence.id)))
    if snapshot_root is None:
        from interview_intelligence.config import load_settings
        snapshot_root = load_settings().snapshot_root
    # Context follows the active source interview, not a topic/type-filtered
    # subset. Current candidate eligibility remains exactly the original query.
    from interview_intelligence.contracts import FilterSpec
    interview_ids = {occurrence.interview_id for occurrence, _ in rows}
    context_from = (QuestionOccurrence.__table__.join(Interview, QuestionOccurrence.interview_id == Interview.id)
        .join(DocumentBuild, Interview.build_id == DocumentBuild.id)
        .join(SourceRevision, DocumentBuild.source_revision_id == SourceRevision.id)
        .join(SourceDocument, SourceRevision.source_document_id == SourceDocument.id))
    context_rows = list(session.execute(select(QuestionOccurrence, SourceRevision, Interview.build_id)
        .select_from(context_from).where(*_conditions(FilterSpec()), Interview.id.in_(interview_ids)))) if interview_ids else []
    cache, records, grouped = {}, {}, {cid: [] for cid in canonical_ids}
    for occurrence, revision, build_id in context_rows:
        if revision.id not in cache:
            path = Path(snapshot_root) / f"{revision.raw_file_hash}.md"
            try:
                raw = path.read_bytes()
                cache[revision.id] = decode_source(raw) if hashlib.sha256(raw).hexdigest() == revision.raw_file_hash else None
                if cache[revision.id] is not None and revision.decoded_text_hash and (
                        hashlib.sha256(cache[revision.id].encode()).hexdigest() != revision.decoded_text_hash):
                    cache[revision.id] = None
            except (OSError, UnicodeError):
                cache[revision.id] = None
        records[occurrence.id] = _source_record(occurrence, revision, build_id, cache[revision.id])
    qualified = {cid: [] for cid in canonical_ids}
    for occurrence, _ in rows:
        qualified[occurrence.canonical_question_id].append(occurrence.id)
        if occurrence.id in records:
            grouped[occurrence.canonical_question_id].append(records[occurrence.id])
    relations = [{column.name: getattr(edge, column.name) for column in QuestionRelation.__table__.columns
                  if column.name != "created_at"} for edge in session.scalars(select(QuestionRelation).where(
        QuestionRelation.relation_type == "OBSERVED_FOLLOWUP", QuestionRelation.target_occurrence_id.in_(records)))] if records else []
    return _assemble_contexts(grouped, records, relations, cache, include_coverage=include_coverage, qualified=qualified)


def enrich_candidate_source_references(candidates, corpus, graph):
    """Deterministic offline preparation using the same host source builder.

    Frozen synthetic candidates with no corpus occurrence remain unchanged.
    Real candidates are rebuilt only from their already selected occurrences;
    their canonical identity/text/order and the original batch stay untouched.
    """
    if graph.get("schema") != "active_source_followup_graph_v1" or not isinstance(graph.get("relations"), list):
        raise ValueError("INVALID_ACTIVE_SOURCE_GRAPH")
    if graph.get("active_occurrence_count") != len(corpus["occurrences"]) or graph.get("observed_active_edge_count") != len(graph["relations"]):
        raise ValueError("SOURCE_GRAPH_COVERAGE_MISMATCH")
    entities = corpus["entities"]
    interviews = {row["id"]: row for row in entities["interviews"]}
    builds = {row["id"]: row for row in entities["builds"]}
    documents = {row["id"]: row for row in entities["documents"]}
    cache, records = {}, {}
    for occurrence in corpus["occurrences"]:
        interview = interviews.get(occurrence["interview_id"], {})
        build = builds.get(interview.get("build_id"), {})
        revision = corpus["sources"].get(build.get("source_revision_id"), {})
        document = documents.get(revision.get("source_document_id"), {})
        if (interview.get("analytics_eligible") is not True or build.get("decision") != "INCLUDED"
                or document.get("active_build_id") != build.get("id")):
            continue
        revision_id = revision["id"]
        if revision_id not in cache:
            try:
                raw = base64.b64decode(revision["raw_base64"], validate=True)
                text = decode_source(raw)
                if hashlib.sha256(raw).hexdigest() != revision["raw_file_hash"] or (
                        revision.get("decoded_text_hash") and hashlib.sha256(text.encode()).hexdigest() != revision["decoded_text_hash"]):
                    text = None
                cache[revision_id] = text
            except (ValueError, UnicodeError):
                cache[revision_id] = None
        records[occurrence["id"]] = _source_record(occurrence, revision, build["id"], cache[revision_id])
    outputs, rebuilt = deepcopy(candidates), []
    for candidate in outputs:
        selected = candidate.get("source_context", [])
        ids = [source.get("occurrence_id") for source in selected]
        if not ids or all(oid not in records for oid in ids):
            continue
        if len(ids) > 2 or len(set(ids)) != len(ids) or any(oid not in records for oid in ids):
            raise ValueError("CANDIDATE_SOURCE_SELECTION_MISMATCH")
        cid = candidate["canonical_question_id"]
        source_occurrences = {row["id"]: row for row in corpus["occurrences"]}
        if any(source_occurrences[oid].get("canonical_question_id") != cid for oid in ids):
            raise ValueError("CANDIDATE_CANONICAL_SOURCE_MISMATCH")
        prepared = _assemble_contexts({cid: [records[oid] for oid in ids]}, records, graph["relations"], cache)[cid]
        candidate.update(prepared)
        rebuilt.append(cid)
    return {"candidates": outputs, "identity": {"version": "reference_enriched_candidates_v1",
        "builder": "candidate_source_context_and_reference_frame_v1", "candidate_count": len(outputs),
        "candidate_ids_hash": content_hash([row["canonical_question_id"] for row in outputs]),
        "rebuilt_candidate_count": len(rebuilt), "rebuilt_candidate_ids_hash": content_hash(rebuilt),
        "graph_body_hash": content_hash(graph), "corpus_body_hash": content_hash(corpus),
        "provider_calls": 0, "semantic_membership_certified": False}}
