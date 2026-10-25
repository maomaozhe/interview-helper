"""Business relevance uncertainty survives bounded evidence independently of engineering scope."""
from copy import deepcopy
import json

from interview_intelligence.agent.evidence_projection import project_meta, project_row


def test_business_pending_status_reason_and_hash_survive_without_raw_context():
    row = {"canonical_question_id": "application", "canonical_text": "业务应用",
        "occurrence_count": 7, "semantic_verification": "CONFIRMED", "confirmed_occurrence_count": 7,
        "relevance_scope_status": "PENDING", "relevance_scope_reason": "业务目标缺少可核验依据。" * 100,
        "relevance_scope_hash": "a" * 64,
        "retrieval": {"source_context": [{"original_question": "PRIVATE_ORIGINAL_QUESTION"}],
                      "relevance_scope_evidence": "PRIVATE_EVIDENCE"},
        "source_text": "PRIVATE_FULL_SOURCE", "pending_sources": [{"raw_question": "PRIVATE_PENDING_SOURCE"}]}
    before = deepcopy(row)
    result = project_row(row)
    assert result["semantic_verification"] == "CONFIRMED"
    assert result["relevance_scope_status"] == "PENDING"
    assert result["relevance_scope_hash"] == "a" * 64
    assert result["occurrence_count"] == result["confirmed_occurrence_count"] == 7
    assert result["relevance_scope_reason"] == row["relevance_scope_reason"][:400]
    assert "PRIVATE_" not in json.dumps(result) and row == before


def test_scope_meta_preserves_counts_bound_and_candidate_pagination_without_sources_or_cursor():
    meta = {"route": "SEARCH", "relevance_scope_hash": "b" * 64,
        "scope_candidate_counts": {"confirmed": 18, "pending": 3, "visible": 21}, "candidate_pool_limit": 50,
        "relevance_scope": {"source_messages": ["PRIVATE_USER_ORIGINAL"],
                            "target_evidence": [{"quote": "PRIVATE_EVIDENCE"}]},
        "rerank_audit": {"candidates": [{"source_context": "PRIVATE_SOURCE_CONTEXT"}]},
        "catalog_selection_audit": {"titles": ["PRIVATE_CATALOG_TITLE"], "selected_ids": ["PRIVATE_ID"]},
        "pagination": {"kind": "semantic_candidates", "total": 21, "result_total": 21, "returned": 20,
            "page_size": 20, "offset": 0, "next_cursor": "PRIVATE_SIGNED_CURSOR"}}
    before = deepcopy(meta)
    result = project_meta(meta)
    assert result["scope_candidate_counts"] == {"confirmed": 18, "pending": 3, "visible": 21}
    assert result["relevance_scope_hash"] == "b" * 64 and result["candidate_pool_limit"] == 50
    assert result["pagination"]["kind"] == "semantic_candidates"
    assert result["pagination"]["has_next_page"] is True
    assert "PRIVATE_" not in json.dumps(result) and meta == before


def test_non_text_scope_reason_cannot_smuggle_a_raw_source_object_into_model_evidence():
    result = project_row({"relevance_scope_status": "PENDING",
        "relevance_scope_reason": {"original_question": "PRIVATE_RAW"}})
    assert result == {"relevance_scope_status": "PENDING"}
