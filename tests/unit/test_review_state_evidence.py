"""Fresh review facts survive bounded Python/Pi answer context consistently."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from interview_intelligence.agent.evidence_projection import project_review_states
from interview_intelligence.agent.harness import ContextCompiler


CASES = json.loads((Path(__file__).parents[1] / "fixtures/review-state-evidence.json").read_text())["cases"]


@pytest.mark.parametrize("scenario", CASES, ids=lambda case: case["id"])
def test_review_state_evidence_retains_all_status_totals_with_only_five_details(scenario):
    facts = deepcopy(scenario["input"])
    records = facts.get("states", facts)
    for state in records.values():
        if isinstance(state, dict):
            state.update(note="PRIVATE_NOTE" * 2000, raw_question="PRIVATE_RAW_QUESTION" * 2000,
                         last_reviewed_at="PRIVATE_TIMESTAMP", private_metadata={"raw": "PRIVATE_EXTRA"})
    original = deepcopy(facts)
    part = {"intent": "REVIEW_STATE", "answer": "Read the requested review states.",
            "facts": {"data": [], "meta": {"route": "SQL"}, "states": facts}}
    context = ContextCompiler().compile(message="根据这些题的状态安排复习", today="2026-10-19",
        explicit_filters={}, preferences={}, state={}, parts=[part], default_page_size=20,
        requested_pipeline="HYBRID_RERANK")
    assert context["completed_tools"][0]["states"] == scenario["expected"]
    assert "PRIVATE" not in json.dumps(context)
    assert facts == original


def test_review_state_status_summary_survives_more_than_three_subsequent_tools():
    first = {"intent": "REVIEW_STATE", "answer": "Read review state.", "facts": {
        "data": [], "meta": {"route": "SQL"}, "states": deepcopy(CASES[0]["input"])}}
    later = {"intent": "COUNT", "answer": "Counted.", "facts": {"data": [],
        "meta": {"route": "SQL", "counts": {"canonical_questions": 10}}}}
    context = ContextCompiler().compile(message="根据状态和题库证据给建议", today="2026-10-19",
        explicit_filters={}, preferences={}, state={}, parts=[first, *[deepcopy(later) for _ in range(4)]],
        default_page_size=20, requested_pipeline="HYBRID_RERANK")
    assert context["completed_tools"][0]["answer"] == ""
    assert context["completed_tools"][0]["states"] == CASES[0]["expected"]


@pytest.mark.parametrize("value", [None, [], "PRIVATE_SOURCE", {"states": []}, {"states": None},
                                   {"states": {str(index): {} for index in range(1001)}}])
def test_invalid_or_unbounded_review_state_shape_is_not_model_evidence(value):
    assert project_review_states(value) is None


@pytest.mark.parametrize("change", [{"status": False}, {"status": "PRIVATE_STATUS"},
                                    {"version": 2 ** 53}, {"review_count": "0"}])
def test_invalid_state_facts_are_explicitly_unknown(change):
    result = project_review_states({"states": {"q0": {"status": "UNSEEN", "review_count": 0, "version": 0, **change}}})
    assert result["valid_count"] == 0 and result["invalid_count"] == 1
    assert result["summary_complete"] is False
    assert result["status_counts"] == {"UNSEEN": 0, "WEAK": 0, "REVIEWED": 0, "MASTERED": 0}
