"""Response projection preserves the executor's authoritative objects."""
from copy import deepcopy
import pytest

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


@pytest.mark.parametrize("returned", [0, 2])
def test_search_answer_does_not_claim_whole_corpus_coverage(returned):
    response = build_query_response(QuerySpec(action="SEARCH", search_query="业务系统设计"),
        {"data": [{}] * returned, "meta": {"relevance_status": "VERIFIED", "timings": {"tool_ms": 1}}},
        search_ranking="relevance", tool_name="search_questions", prompt_version="fixture", warnings=[])
    assert f"本次候选中找到 {returned} 道" in response["answer"]
    assert "不代表题库全部匹配题" in response["answer"]


def test_partial_search_answer_surfaces_incomplete_verification():
    response = build_query_response(QuerySpec(action="SEARCH", search_query="系统设计"),
        {"data": [{}], "meta": {"relevance_status": "VERIFIED", "candidate_verification_status": "PARTIAL",
                                 "timings": {"tool_ms": 1}}},
        search_ranking="frequency", tool_name="search_questions", prompt_version="fixture", warnings=[])
    assert "证据或格式异常未能完成核验" in response["answer"]
    assert "结果可能不完整" in response["answer"]
    assert "不代表题库全部匹配题" in response["answer"]


def test_empty_sql_scope_answer_distinguishes_proven_empty_from_search_miss():
    response = build_query_response(QuerySpec(action="SEARCH", search_query="系统设计"),
        {"data": [], "meta": {"relevance_status": "VERIFIED", "verification_basis": "empty_sql_scope",
                               "timings": {"tool_ms": 1}}},
        search_ranking="relevance", tool_name="search_questions", prompt_version="fixture", warnings=[])
    assert response["answer"] == "当前筛选范围没有可检索题目。"
