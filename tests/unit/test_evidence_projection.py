"""Frozen host evidence semantics, including native Python compatibility edges."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from interview_intelligence.agent.harness import ContextCompiler


def projection_cases():
    row = {"canonical_question_id": "q1", "key": "topic", "occurrence_count": 0,
           "interview_count": False, "source_document_count": None, "company_count": 2,
           "canonical_text": "Question " + "x" * 450, "private_path": "OMIT_ROW_PATH",
           "sources": [{"quote": "q" * 180, "end_line": 6, "private_note": "OMIT_SOURCE",
                        "start_line": 5, "revision_id": f"r{i}"} for i in range(3)]}
    meta = {"frequency_ranking": {"scope": "candidate_pool"}, "private_note": "OMIT_META",
            "route": "SEARCH", "corpus_revision": 3, "task_annotation_revision": 2,
            "user_state_revision": 1, "applied_filters": {"company": None}, "group_by": "topic",
            "topic_level": "L2", "sort": "frequency", "counts": {"occurrences": 9},
            "sample_counts": {"occurrences": 4}, "total_groups": 2, "count_scope": "filtered_corpus",
            "search_ranking": "frequency", "review_statuses": ["WEAK"], "review_order": "AFTER_TOP_N",
            "relevance_status": "VERIFIED", "returned_count": 2, "semantic_task": "ALGORITHM_PROBLEM",
            "semantic_scope": {"status": "COMPLETE"}, "pagination": {
                "top_n": None, "offset": False, "returned": "2", "page_size": 20, "total": 12,
                "result_total": 8, "has_next_page": True, "next_cursor": "", "private_note": "OMIT_CURSOR"}}
    return {
        "full": (row, meta),
        "empty": ({"canonical_text": "", "sources": []}, {"pagination": {}}),
        "null": ({"canonical_question_id": None, "canonical_text": None, "sources": None}, {"pagination": None}),
        "preprojected_pagination": ({}, {"pagination": {"total": "12", "returned": False,
                                                       "page_size": 0, "has_next_page": True}}),
        "cursor": ({}, {"pagination": {"total": 0, "next_cursor": "OPAQUE_CURSOR", "has_next_page": False}}),
        "unicode": ({"canonical_text": "a" * 399 + "🙂tail",
                     "sources": [{"quote": "b" * 159 + "🙂tail"}]}, {}),
        "native_slices": ({"canonical_text": ["a", "b"], "sources": [{}]}, {"route": None, "counts": False}),
    }


def fingerprint(value):
    # Keep key order and surrogate/code-point distinctions in this frozen output.
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("name", projection_cases())
def test_host_projection_matches_original_output_without_mutating_facts(name):
    before = json.loads((Path(__file__).parents[1] / "fixtures/evidence-projection-host-before.json").read_text())
    row, meta = projection_cases()[name]
    original = deepcopy((row, meta))
    projected_meta = ContextCompiler.evidence_meta(meta)
    if name == "full":
        # The source fixture already supplied this field. Keeping it is an
        # intentional contract addition; all historical fields remain frozen.
        assert projected_meta.pop("semantic_scope") == {"status": "COMPLETE"}
        # Answer synthesis now also sees the returned-page count. It remains
        # distinct from the eligible universe and the verified candidate pool.
        assert projected_meta.pop("returned_count") == 2
    actual = {"row": fingerprint(ContextCompiler.evidence_row(row)),
              "meta": fingerprint(projected_meta)}
    assert actual == before[name]
    assert (row, meta) == original


@pytest.mark.parametrize("row,error", [
    ({"canonical_text": 123}, TypeError),
    ({"sources": [{"quote": None}]}, TypeError),
    ({"sources": [{"quote": 123}]}, TypeError),
    ({"sources": [None]}, AttributeError),
])
def test_host_keeps_existing_native_type_errors(row, error):
    original = deepcopy(row)
    with pytest.raises(error):
        ContextCompiler.evidence_row(row)
    assert row == original


def test_host_does_not_silently_normalize_invalid_pagination():
    with pytest.raises(AttributeError):
        ContextCompiler.evidence_meta({"pagination": [1]})
