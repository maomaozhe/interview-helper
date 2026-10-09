"""Partial candidate schema failures cannot masquerade as a verified empty set."""
import pytest

from interview_intelligence.agent.query_contract import QuerySpec
from test_conversation_quality import app_fixture


@pytest.mark.parametrize("has_results,invalid_count,status,verification,executed,expected", [
    (True, 2, "COMPLETED_PARTIAL", "PARTIAL", "HYBRID_RERANK", "VERIFIED"),
    (False, 2, "COMPLETED_PARTIAL", "PARTIAL", "HYBRID_RERANK", "UNAVAILABLE"),
    (False, 0, "COMPLETED", "COMPLETE", "HYBRID_RERANK", "VERIFIED"),
    (True, 2, "FAILED", "PARTIAL", "HYBRID", "UNAVAILABLE"),
    (True, 0, "COMPLETED_PARTIAL", "PARTIAL", "HYBRID_RERANK", "UNAVAILABLE"),
    (True, 2, "COMPLETED", "PARTIAL", "HYBRID_RERANK", "UNAVAILABLE"),
])
def test_partial_schema_delivery_and_empty_verification(tmp_path, has_results, invalid_count, status,
                                                        verification, executed, expected):
    class Retriever:
        calls = 0

        def retrieve(self, query, eligible, pipeline, top_k):
            self.calls += 1
            return {"data": [{"canonical_question_id": eligible[0]}] if has_results else [],
                    "meta": {"pipeline": executed, "rerank_status": status,
                             "candidate_verification_status": verification, "invalid_candidate_count": invalid_count}}

    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="SEARCH", search_query="系统设计")

    retriever = Retriever()
    client, _, _, _ = app_fixture(tmp_path, Planner(), retriever)
    body = {"message": "找系统设计相关题", "request_id": "partial-schema-result"}
    response = client.post("/api/query", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["meta"]["relevance_status"] == expected
    assert len(result["data"]) == int(has_results and expected == "VERIFIED")
    assert result["meta"]["invalid_candidate_count"] == invalid_count
    assert result["meta"]["candidate_verification_status"] == verification
    warnings = result["warnings"]
    if status == "COMPLETED_PARTIAL" and invalid_count:
        assert any("2 个结构异常候选" in warning and "可能不完整" in warning for warning in warnings)
    if expected == "UNAVAILABLE":
        assert "核验未完成" in result["meta"]["answer"]
    elif not has_results:
        assert not warnings
    assert client.post("/api/query", json=body).json() == result
    assert retriever.calls == 1
