from copy import deepcopy

from interview_intelligence.search.completeness import search_completeness, partial_verification_warning


def test_complete_candidate_verification_does_not_mean_complete_corpus():
    meta = {"relevance_status": "VERIFIED", "candidate_verification_status": "COMPLETE",
            "eligible_count": 2482, "candidate_count": 50, "invalid_candidate_count": 0,
            "rerank_audit": {"candidates": [{"decision": "RETURNED"}, {"decision": "PAGE_CUTOFF"}]}}
    before = deepcopy(meta)
    result = search_completeness(meta, 1)
    assert result["verification_complete"] is True
    assert result["corpus_complete"] is False
    assert result["scope"] == "retrieval_candidates"
    assert result["candidate_count"] == 50 and result["eligible_count"] == 2482
    assert result["page_cutoff_count"] == 1
    assert meta == before


def test_partial_verification_preserves_evidence_and_schema_counts():
    meta = {"relevance_status": "VERIFIED", "candidate_verification_status": "PARTIAL",
            "invalid_candidate_count": 3, "candidate_ids": ["a", "b", "c", "d"],
            "rerank_audit": {"invalid_evidence_count": 2, "invalid_schema_count": 1}}
    result = search_completeness(meta, 1)
    assert result["verification_complete"] is False
    assert result["candidate_count"] == 4
    assert result["invalid_evidence_count"] == 2 and result["invalid_schema_count"] == 1
    warning = partial_verification_warning(meta)
    assert "2 个证据异常候选" in warning and "1 个结构异常候选" in warning
    assert "未能完成核验" in warning and "可能不完整" in warning


def test_unknown_candidate_count_is_not_inferred_from_returned_page():
    result = search_completeness({"relevance_status": "VERIFIED"}, 20)
    assert result["candidate_count"] is None
    assert result["returned_count"] == 20
    assert result["corpus_complete"] is False


def test_legacy_schema_count_remains_compatible():
    warning = partial_verification_warning({"invalid_candidate_count": 2})
    assert "2 个结构异常候选" in warning
    assert "证据异常" not in warning


def test_scoped_pending_warning_describes_retained_rows_instead_of_exclusion():
    meta = {"candidate_verification_status": "PARTIAL", "invalid_candidate_count": 3,
        "scope_candidate_counts": {"confirmed": 4, "pending": 3, "visible": 7}}
    warning = partial_verification_warning(meta)
    assert "3 个候选" in warning and "已保留为待核验" in warning
    assert "仅限本次检索候选" in warning and "排除" not in warning


def test_proven_empty_sql_scope_has_explicit_completeness_basis():
    result = search_completeness({"verification_basis": "empty_sql_scope", "relevance_status": "VERIFIED",
                                  "eligible_count": 0, "candidate_count": 0}, 0)
    assert result["scope"] == "empty_sql_scope"
    assert result["corpus_complete"] is True
    assert result["verification_complete"] is True
def test_workset_completeness_distinguishes_full_candidate_universe_from_processed_business_results():
    from interview_intelligence.search.completeness import search_completeness, partial_verification_warning
    progress = {"version": "candidate_workset_progress_v1", "candidate_total": 320, "processed": 20,
        "unprocessed": 300, "confirmed": 8, "pending": 5, "excluded": 7, "visible": 13,
        "processing_complete": False, "batch_size": 20, "last_batch_count": 20, "next_requires_processing": True}
    meta = {"candidate_workset_progress": progress}
    value = search_completeness(meta, 5)
    assert value["candidate_universe_complete"] is True and value["candidate_count"] == 320
    assert value["processed_count"] == 20 and value["unprocessed_count"] == 300
    assert value["returned_count"] == 5 and value["verification_complete"] is False
    assert value["business_count_complete"] is False and value["corpus_complete"] is False
    warning = partial_verification_warning(meta)
    assert "320" in warning and "300" in warning and "候选总数不代表业务题总数" in warning
    assert "已排除" not in warning


def test_traversed_workset_can_still_have_unresolved_business_members():
    from interview_intelligence.search.completeness import search_completeness
    value = search_completeness({"candidate_workset_progress": {"version": "candidate_workset_progress_v1",
        "candidate_total": 4, "processed": 4, "unprocessed": 0, "confirmed": 1, "pending": 1,
        "excluded": 2, "processing_complete": True}}, 2)
    assert value["candidate_universe_complete"] is True and value["verification_complete"] is False
    assert value["business_count_complete"] is False
