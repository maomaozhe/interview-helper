"""Shared evidence declarations with explicit host and legacy Pi profiles.

Regenerate the Pi runtime data with ``python scripts/sync-tool-contract.py``.
Native Python and JavaScript presence, slicing and pagination behavior remains
separate: these are compatible projections for different model input paths.
"""

import re

ROW_FIELDS = ("canonical_question_id", "key", "occurrence_count", "interview_count",
              "source_document_count", "company_count", "semantic_verification",
              "confirmed_occurrence_count", "pending_occurrence_count",
              "relevance_scope_status", "relevance_scope_reason", "relevance_scope_hash", "frequency_basis")
SOURCE_FIELDS = ("revision_id", "start_line", "end_line", "quote")
COMMON_META_FIELDS = ("route", "corpus_revision", "task_annotation_revision", "user_state_revision",
                      "applied_filters", "group_by", "topic_level", "sort", "counts", "sample_counts",
                      "total_groups", "count_scope")
COVERAGE_EVIDENCE = {
    "count_fields": ("candidate_count", "eligible_count", "returned_count", "invalid_candidate_count",
                     "invalid_evidence_count", "invalid_schema_count"),
    "enum_fields": {"candidate_verification_status": ("COMPLETE", "PARTIAL"),
                    "verification_basis": ("empty_sql_scope",)},
    "max_count": 2 ** 53 - 1,
    "result_completeness": {
        "count_fields": ("candidate_count", "eligible_count", "returned_count", "invalid_candidate_count",
                         "invalid_evidence_count", "invalid_schema_count", "page_cutoff_count",
                         "processed_count", "unprocessed_count", "confirmed_count", "pending_count", "excluded_count"),
        "boolean_fields": ("corpus_complete", "verification_complete", "candidate_universe_complete",
                           "business_count_complete", "complete", "business_membership_complete"),
        "enum_fields": {
            "scope": ("retrieval_candidates", "empty_sql_scope", "engineering_candidate_workset"),
            "reason": ("candidate_workset_unprocessed", "candidate_workset_pending",
                       "complete_authorized_candidate_workset"),
        },
    },
}
COVERAGE_META_FIELDS = (*COVERAGE_EVIDENCE["count_fields"], *COVERAGE_EVIDENCE["enum_fields"], "result_completeness")
SCOPE_META_FIELDS = ("relevance_scope_hash", "scope_candidate_counts", "candidate_pool_limit",
                     "candidate_workset_progress")
HOST_META_FIELDS = COMMON_META_FIELDS + SCOPE_META_FIELDS + COVERAGE_META_FIELDS + ("search_ranking", "frequency_ranking", "semantic_scope",
                                        "semantic_counts", "pending_counts")
PI_LEGACY_META_FIELDS = COMMON_META_FIELDS + SCOPE_META_FIELDS + ("review_statuses", "review_order", "relevance_status",
                                             "returned_count", "semantic_task", "semantic_scope",
                                             "semantic_counts", "pending_counts") + tuple(
                                                 key for key in COVERAGE_META_FIELDS if key != "returned_count")
PAGINATION_BOOLEAN_FIELDS = ("workset", "processing_complete", "has_unprocessed", "next_requires_processing")
PAGINATION_FIELDS = ("total", "result_total", "returned", "offset", "page_size", "top_n", "kind") + PAGINATION_BOOLEAN_FIELDS
WORKSET_PROGRESS = {
    "version": "candidate_workset_progress_v1",
    "count_fields": ("candidate_total", "processed", "unprocessed", "confirmed", "pending", "excluded",
                     "visible", "batch_size", "last_batch_count"),
    "boolean_fields": ("processing_complete", "next_requires_processing"),
}
REVIEW_STATES = {
    "scope": "requested_question_ids",
    "statuses": ("UNSEEN", "WEAK", "REVIEWED", "MASTERED"),
    "count_fields": ("review_count", "version"),
    "binding_statuses": ("RESOLVED",),
    "id_pattern": "[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",
    "max_input_items": 1000,
    "max_items": 5,
    "max_score": 5,
}
EVIDENCE_RESULT_ROWS = 5

EVIDENCE_PROJECTION = {
    "row_fields": ROW_FIELDS,
    "source_fields": SOURCE_FIELDS,
    "meta_fields": {"host": HOST_META_FIELDS, "pi_legacy": PI_LEGACY_META_FIELDS},
    "pagination_fields": PAGINATION_FIELDS,
    "pagination_boolean_fields": PAGINATION_BOOLEAN_FIELDS,
    "workset_progress": WORKSET_PROGRESS,
    "coverage_evidence": COVERAGE_EVIDENCE,
    "review_states": REVIEW_STATES,
    "limits": {"canonical_text": 400, "quote": 160, "sources": 2,
               "relevance_scope_reason": 400, "tool_rows": EVIDENCE_RESULT_ROWS},
}


def project_review_states(value):
    """Summarize this requested ID set; sample details never imply full coverage.

    Keep API receipts unchanged. Legacy Pi receipts may contain the ID mapping
    directly instead of the ReviewService wrapper. Invalid rows remain unknown
    and do not acquire the default UNSEEN status.
    """
    if not isinstance(value, dict):
        return None
    records = value.get("states") if "states" in value else value
    if not isinstance(records, dict) or len(records) > REVIEW_STATES["max_input_items"]:
        return None
    items = []
    counts = dict.fromkeys(REVIEW_STATES["statuses"], 0)
    valid_count = 0
    for identity, state in records.items():
        if (not isinstance(identity, str) or re.fullmatch(REVIEW_STATES["id_pattern"], identity) is None
                or not isinstance(state, dict) or not isinstance(state.get("status"), str)
                or state["status"] not in REVIEW_STATES["statuses"]
                or any(type(state.get(key)) is not int or not 0 <= state[key] <= COVERAGE_EVIDENCE["max_count"]
                       for key in REVIEW_STATES["count_fields"])):
            continue
        valid_count += 1
        counts[state["status"]] += 1
        if len(items) >= REVIEW_STATES["max_items"]:
            continue
        item = {"canonical_question_id": identity, "status": state["status"],
                **{key: state[key] for key in REVIEW_STATES["count_fields"]}}
        if isinstance(state.get("binding_status"), str) and state["binding_status"] in REVIEW_STATES["binding_statuses"]:
            item["binding_status"] = state["binding_status"]
        if type(state.get("last_score")) is int and 0 <= state["last_score"] <= REVIEW_STATES["max_score"]:
            item["last_score"] = state["last_score"]
        items.append(item)
    result = {"scope": REVIEW_STATES["scope"], "items": items, "total_count": len(records),
              "valid_count": valid_count, "invalid_count": len(records) - valid_count,
              "returned_count": len(items), "truncated": len(items) < len(records),
              "status_counts": counts, "summary_complete": valid_count == len(records)}
    if ("states" in value and type(value.get("user_state_revision")) is int
            and 0 <= value["user_state_revision"] <= COVERAGE_EVIDENCE["max_count"]):
        result["user_state_revision"] = value["user_state_revision"]
    return result


def project_coverage_fields(value, spec):
    """Keep only bounded count/boolean/enumeration facts, including zero/False.

    Missing or malformed counts stay unknown; they must never turn into zero.
    No source text, audit rows, identifiers or cursor can enter this projection.
    """
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in spec["count_fields"]:
        if type(value.get(key)) is int and 0 <= value[key] <= COVERAGE_EVIDENCE["max_count"]:
            result[key] = value[key]
    for key in spec.get("boolean_fields", ()):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    for key, allowed in spec["enum_fields"].items():
        if isinstance(value.get(key), str) and value[key] in allowed:
            result[key] = value[key]
    return result


def project_coverage_meta(meta):
    """Shared host/Pi contract for the scope needed to assess a tool result."""
    result = project_coverage_fields(meta, COVERAGE_EVIDENCE)
    completeness = project_coverage_fields(meta.get("result_completeness"), COVERAGE_EVIDENCE["result_completeness"])
    if completeness:
        result["result_completeness"] = completeness
    return result


def project_workset_progress(progress):
    """Expose only consistent aggregate progress, never workset IDs or proofs."""
    if not isinstance(progress, dict) or progress.get("version") != WORKSET_PROGRESS["version"]:
        return None
    if (any(type(progress.get(key)) is not int or progress[key] < 0 for key in WORKSET_PROGRESS["count_fields"])
            or any(type(progress.get(key)) is not bool for key in WORKSET_PROGRESS["boolean_fields"])):
        return None
    if (progress["processed"] + progress["unprocessed"] != progress["candidate_total"]
            or progress["confirmed"] + progress["pending"] + progress["excluded"] != progress["processed"]
            or progress["confirmed"] + progress["pending"] != progress["visible"]
            or not 1 <= progress["batch_size"] <= 50
            or progress["last_batch_count"] > progress["batch_size"]
            or progress["processing_complete"] != (progress["unprocessed"] == 0)
            or progress["next_requires_processing"] and progress["processing_complete"]):
        return None
    return {"version": WORKSET_PROGRESS["version"],
            **{key: progress[key] for key in (*WORKSET_PROGRESS["count_fields"], *WORKSET_PROGRESS["boolean_fields"])}}


def project_meta(meta):
    """Host profile keeps input order/types and derives paging only from a cursor."""
    result = {key: value for key, value in meta.items() if key in HOST_META_FIELDS}
    coverage = project_coverage_meta(meta)
    # Replace coverage values in place to preserve the historical host key order.
    for key in COVERAGE_META_FIELDS:
        if key in coverage:
            result[key] = coverage[key]
        else:
            result.pop(key, None)
    if "candidate_workset_progress" in result:
        progress = project_workset_progress(result["candidate_workset_progress"])
        if progress is None:
            result.pop("candidate_workset_progress")
        else:
            result["candidate_workset_progress"] = progress
    if meta.get("pagination"):
        result["pagination"] = {key: value for key, value in meta["pagination"].items()
                                if key in PAGINATION_FIELDS and (key not in PAGINATION_BOOLEAN_FIELDS or type(value) is bool)}
        if result["pagination"].get("workset") is True:
            # This is a candidate-universe size, not a known business-result total.
            result["pagination"].pop("total", None)
        result["pagination"]["has_next_page"] = bool(meta["pagination"].get("next_cursor"))
    return result


def project_row(row):
    """Bound host evidence while preserving native presence and slice behavior."""
    limits = EVIDENCE_PROJECTION["limits"]
    result = {key: row[key] for key in ROW_FIELDS if key in row}
    if result.get("frequency_basis") != "engineering_qualified_occurrences":
        result.pop("frequency_basis", None)
    if "relevance_scope_reason" in result:
        if isinstance(result["relevance_scope_reason"], str):
            result["relevance_scope_reason"] = result["relevance_scope_reason"][:limits["relevance_scope_reason"]]
        else:
            result.pop("relevance_scope_reason")
    if row.get("canonical_text"):
        result["canonical_text"] = row["canonical_text"][:limits["canonical_text"]]
    if row.get("sources"):
        result["sources"] = [{key: (value[:limits["quote"]] if key == "quote" else value)
                              for key, value in source.items() if key in SOURCE_FIELDS}
                             for source in row["sources"][:limits["sources"]]]
    return result
