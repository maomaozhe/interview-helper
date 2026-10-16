"""Shared evidence declarations with explicit host and legacy Pi profiles.

Regenerate the Pi runtime data with ``python scripts/sync-tool-contract.py``.
Native Python and JavaScript presence, slicing and pagination behavior remains
separate: these are compatible projections for different model input paths.
"""

ROW_FIELDS = ("canonical_question_id", "key", "occurrence_count", "interview_count",
              "source_document_count", "company_count")
SOURCE_FIELDS = ("revision_id", "start_line", "end_line", "quote")
COMMON_META_FIELDS = ("route", "corpus_revision", "task_annotation_revision", "user_state_revision",
                      "applied_filters", "group_by", "topic_level", "sort", "counts", "sample_counts",
                      "total_groups", "count_scope")
HOST_META_FIELDS = COMMON_META_FIELDS + ("search_ranking", "frequency_ranking")
PI_LEGACY_META_FIELDS = COMMON_META_FIELDS + ("review_statuses", "review_order", "relevance_status",
                                             "returned_count", "semantic_task", "semantic_scope")
PAGINATION_FIELDS = ("total", "result_total", "returned", "offset", "page_size", "top_n")
EVIDENCE_RESULT_ROWS = 5

EVIDENCE_PROJECTION = {
    "row_fields": ROW_FIELDS,
    "source_fields": SOURCE_FIELDS,
    "meta_fields": {"host": HOST_META_FIELDS, "pi_legacy": PI_LEGACY_META_FIELDS},
    "pagination_fields": PAGINATION_FIELDS,
    "limits": {"canonical_text": 400, "quote": 160, "sources": 2, "tool_rows": EVIDENCE_RESULT_ROWS},
}


def project_meta(meta):
    """Host profile keeps input order/types and derives paging only from a cursor."""
    result = {key: value for key, value in meta.items() if key in HOST_META_FIELDS}
    if meta.get("pagination"):
        result["pagination"] = {key: value for key, value in meta["pagination"].items()
                                if key in PAGINATION_FIELDS}
        result["pagination"]["has_next_page"] = bool(meta["pagination"].get("next_cursor"))
    return result


def project_row(row):
    """Bound host evidence while preserving native presence and slice behavior."""
    limits = EVIDENCE_PROJECTION["limits"]
    result = {key: row[key] for key in ROW_FIELDS if key in row}
    if row.get("canonical_text"):
        result["canonical_text"] = row["canonical_text"][:limits["canonical_text"]]
    if row.get("sources"):
        result["sources"] = [{key: (value[:limits["quote"]] if key == "quote" else value)
                              for key, value in source.items() if key in SOURCE_FIELDS}
                             for source in row["sources"][:limits["sources"]]]
    return result
