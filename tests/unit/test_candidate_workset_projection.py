from copy import deepcopy

import pytest

from interview_intelligence.agent.evidence_projection import project_meta, project_row


def progress():
    return {"version": "candidate_workset_progress_v1", "candidate_total": 320,
            "processed": 20, "unprocessed": 300, "confirmed": 8, "pending": 5,
            "excluded": 7, "visible": 13, "batch_size": 20, "last_batch_count": 20,
            "processing_complete": False, "next_requires_processing": True}


def test_workset_model_evidence_distinguishes_universe_and_visible_results_without_private_ids():
    value = progress()
    original = {"candidate_workset_progress": {**value, "candidate_ids": ["PRIVATE-ID"]},
                "candidate_workset_snapshot": {"all_rows": ["PRIVATE-RAW"]},
                "engineering_collection_authorization": {"messages": ["PRIVATE-HISTORY"]},
                "pagination": {"kind": "semantic_candidates", "workset": True, "total": 320,
                               "result_total": 13, "next_cursor": "PRIVATE-CURSOR",
                               "processing_complete": False, "has_unprocessed": True,
                               "next_requires_processing": True}}
    untouched = deepcopy(original)
    result = project_meta(original)
    assert result["candidate_workset_progress"] == value
    assert result["pagination"] == {"kind": "semantic_candidates", "workset": True,
                                    "result_total": 13, "processing_complete": False,
                                    "has_unprocessed": True, "next_requires_processing": True,
                                    "has_next_page": True}
    assert "PRIVATE" not in str(result)
    assert original == untouched


@pytest.mark.parametrize("change", [
    {"processed": True}, {"confirmed": -1}, {"unprocessed": 299}, {"visible": 320},
    {"excluded": 0}, {"processing_complete": True}, {"last_batch_count": 21},
    {"next_requires_processing": "true"}, {"version": "unknown"},
])
def test_inconsistent_workset_progress_cannot_be_projected_as_complete_business_results(change):
    assert "candidate_workset_progress" not in project_meta({"candidate_workset_progress": {**progress(), **change}})


def test_ordinary_finite_snapshot_keeps_its_total_and_invalid_new_pagination_flags_are_omitted():
    result = project_meta({"pagination": {"kind": "semantic_candidates", "total": 21,
                                         "result_total": 21, "next_cursor": "cursor",
                                         "workset": "yes", "processing_complete": 1}})
    assert result["pagination"] == {"kind": "semantic_candidates", "total": 21,
                                    "result_total": 21, "has_next_page": True}


def test_frequency_basis_is_explicit_and_cannot_carry_untrusted_source_text():
    assert project_row({"occurrence_count": 4, "confirmed_occurrence_count": 3,
                        "frequency_basis": "engineering_qualified_occurrences"}) == {
        "occurrence_count": 4, "confirmed_occurrence_count": 3,
        "frequency_basis": "engineering_qualified_occurrences"}
    assert "frequency_basis" not in project_row({"frequency_basis": "PRIVATE_RAW_SOURCE"})
