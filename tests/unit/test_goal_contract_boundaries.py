"""Model-facing goal schemas and bounded evidence retain their trust boundary."""
import pytest

from interview_intelligence.agent.harness import ContextCompiler
from interview_intelligence.agent.query_contract import QuerySpec, query_model_schema, validate_model_plan


def test_modern_model_schema_requires_a_goal_and_rejects_host_source_forgery():
    plan = QuerySpec(action="COUNT").model_dump(mode="json")
    schema = query_model_schema(require_user_goal=True)
    assert "user_goal" in schema["required"]
    assert schema["properties"]["user_goal"] == {"$ref": "#/$defs/UserGoal"}
    with pytest.raises(ValueError, match="QUERY_PLAN_INCOMPLETE"):
        validate_model_plan(plan, require_user_goal=True)
    plan["user_goal"] = {"objective": "核对当前统计并解释依据", "response_mode": "SYNTHESIS", "source": "structured_ui"}
    with pytest.raises(ValueError):
        validate_model_plan(plan, require_user_goal=True)
    plan["user_goal"].pop("source")
    assert validate_model_plan(plan, require_user_goal=True).user_goal.response_mode == "SYNTHESIS"


@pytest.mark.parametrize("references", [[True], [0], [9], [1, 1]])
def test_tool_evidence_identity_rejects_booleans_unbounded_or_duplicate_ordinals(references):
    with pytest.raises(ValueError):
        QuerySpec(action="ANSWER", answer_text="当前证据仍有边界。", evidence_tool_ordinals=references)


def test_early_count_and_coverage_survive_more_than_three_later_reads_without_private_payloads():
    parts = [{"intent": "COUNT", "answer": "首步计数", "facts": {"data": [], "meta": {
        "route": "SQL", "counts": {"canonical_questions": 80},
        "completion": {"tool_ordinal": 2}, "private": "DO_NOT_EXPOSE"}}}]
    for ordinal in range(3, 8):
        parts.append({"intent": "SEARCH", "answer": "候选结果", "facts": {
            "data": [{"canonical_question_id": f"q{ordinal}", "canonical_text": "来源内容"}], "meta": {
                "route": "SEARCH", "candidate_count": 50, "eligible_count": 80,
                "completion": {"tool_ordinal": ordinal},
                "result_completeness": {"scope": "retrieval_candidates", "corpus_complete": False,
                    "verification_complete": True, "returned_count": 4, "private": "DO_NOT_EXPOSE"}}}})
    context = ContextCompiler().compile(message="综合这些证据解释覆盖范围", today="2026-10-19",
        explicit_filters={}, preferences={}, state={}, default_page_size=20,
        requested_pipeline="HYBRID_RERANK", parts=parts)
    evidence = context["completed_tools"]
    assert [part["ordinal"] for part in evidence] == [2, 3, 4, 5, 6, 7]
    assert evidence[0]["meta"]["counts"]["canonical_questions"] == 80
    assert evidence[1]["meta"]["result_completeness"]["corpus_complete"] is False
    assert evidence[1]["data"] == []
    assert evidence[-1]["data"]
    assert "DO_NOT_EXPOSE" not in str(context)
