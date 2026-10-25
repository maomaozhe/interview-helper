"""Answer synthesis receives scope facts without promoting candidate counts."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from interview_intelligence.agent.evidence_projection import (
    COVERAGE_EVIDENCE,
    project_meta,
)
from interview_intelligence.agent.harness import ContextCompiler


CASES = json.loads((Path(__file__).parents[1] / "fixtures/answer-coverage-evidence.json").read_text())["cases"]


@pytest.mark.parametrize("scenario", CASES, ids=lambda case: case["id"])
def test_completed_tool_context_preserves_bounded_coverage_and_no_private_material(scenario):
    expected = scenario["meta"]
    meta = deepcopy(expected)
    meta.update(private_metadata={"source": "PRIVATE_FULL_SOURCE" * 2000},
                candidate_ids=["PRIVATE_CANDIDATE"], rerank_audit={"candidates": ["PRIVATE_AUDIT"]},
                next_cursor="PRIVATE_SIGNED_CURSOR")
    meta["result_completeness"].update(private_metadata={"source": "PRIVATE_NESTED_SOURCE" * 2000},
                                       next_cursor="PRIVATE_NESTED_CURSOR")
    part = {"intent": "SEARCH", "answer": "Bounded retrieval receipt", "facts": {"data": [], "meta": meta}}
    original = deepcopy(part)

    context = ContextCompiler().compile(message="判断这些结果是否完整，并说明依据", today="2026-10-19",
        explicit_filters={}, preferences={}, state={}, parts=[part], default_page_size=20,
        requested_pipeline="HYBRID_RERANK")

    assert context["completed_tools"][0]["meta"] == expected
    assert "PRIVATE" not in json.dumps(context)
    assert part == original


@pytest.mark.parametrize("field", COVERAGE_EVIDENCE["count_fields"])
@pytest.mark.parametrize("invalid", [True, False, -1, 1.5, "4", None, {}, [], 2 ** 53])
def test_coverage_count_rejects_invalid_or_unbounded_values_in_both_locations(field, invalid):
    result = project_meta({field: invalid, "result_completeness": {field: invalid, "corpus_complete": False}})
    assert field not in result
    assert result["result_completeness"] == {"corpus_complete": False}


def test_coverage_zeros_safe_integer_upper_bound_and_false_survive_without_defaulting_unknowns():
    maximum = COVERAGE_EVIDENCE["max_count"]
    result = project_meta({"candidate_count": maximum, "eligible_count": None, "returned_count": 0,
        "result_completeness": {"candidate_count": maximum, "eligible_count": None,
            "returned_count": 0, "corpus_complete": False, "verification_complete": False}})
    assert result == {"candidate_count": maximum, "returned_count": 0, "result_completeness": {
        "candidate_count": maximum, "returned_count": 0, "corpus_complete": False, "verification_complete": False}}


@pytest.mark.parametrize("invalid", [None, [], "PRIVATE_SOURCE", 0, False])
def test_non_object_completeness_never_reaches_model(invalid):
    assert project_meta({"result_completeness": invalid}) == {}


def test_completeness_requires_exact_boolean_and_known_enumerations_without_recursive_passthrough():
    meta = {"candidate_verification_status": {"raw": "PRIVATE_SOURCE"},
        "verification_basis": "PRIVATE_SOURCE", "result_completeness": {
            "corpus_complete": 1, "verification_complete": "false", "scope": ["retrieval_candidates"],
            "reason": "PRIVATE_SOURCE", "counts": {"all": "PRIVATE_SOURCE"},
            "candidate_ids": ["PRIVATE_SOURCE"], "candidate_count": 50, "returned_count": 0}}
    assert project_meta(meta) == {"result_completeness": {"candidate_count": 50, "returned_count": 0}}


def test_optional_excerpt_compaction_retains_all_coverage_facts_for_the_answer():
    meta = deepcopy(CASES[1]["meta"])
    part = {"intent": "SEARCH", "answer": "Bounded retrieval receipt", "facts": {"data": [], "meta": meta}}
    arguments = dict(message="解释检索边界", today="2026-10-19", explicit_filters={}, preferences={}, state={},
                     parts=[part], default_page_size=20, requested_pipeline="HYBRID_RERANK")
    baseline = ContextCompiler().compile(**arguments)
    part["facts"]["data"] = [{"canonical_question_id": f"q{i}", "canonical_text": "x" * 400}
                              for i in range(5)]
    context = ContextCompiler(max_bytes=baseline["context_contract"]["bytes"] + 500).compile(**arguments)
    assert context["context_contract"]["evidence_compacted"] is True
    assert context["completed_tools"][0]["meta"] == meta
    assert len(context["completed_tools"][0]["data"]) < 5
