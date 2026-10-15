"""Response projection preserves the executor's authoritative objects."""
from copy import deepcopy

from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.agent.query_response import build_query_response


def test_response_preserves_facts_and_warnings_without_mutating_inputs():
    plan = QuerySpec(action="SEARCH", search_query="Redis", filters={"company": "字节"})
    facts = {"data": [{"canonical_question_id": "q1"}],
             "meta": {"relevance_status": "VERIFIED", "timings": {"tool_ms": 12}}}
    warnings = ["fixture warning"]
    original = deepcopy((plan.model_dump(mode="json"), facts, warnings))

    response = build_query_response(plan, facts, search_ranking="frequency",
        tool_name="search_questions", prompt_version="fixture", warnings=warnings)

    assert response["facts"] is facts
    assert response["warnings"] is warnings
    assert (plan.model_dump(mode="json"), facts, warnings) == original
    # Later host receipt enrichment remains visible without copying facts, while
    # independent plan snapshots cannot overwrite each other or the input plan.
    facts["meta"]["harness"] = {"context_version": "fixture"}
    assert response["facts"]["meta"]["harness"] is facts["meta"]["harness"]
    response["planning"]["spec"]["filters"]["company"] = "腾讯"
    assert response["tool_trace"][0]["parameters"]["filters"]["company"] == "字节"
    assert plan.filters.company == "字节"
