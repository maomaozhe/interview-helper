from interview_intelligence.agent.service import AgentService
from test_analytics import seed_corpus


def test_direct_detail_request_uses_detail_tool_with_sources():
    db, fast_id, _ = seed_corpus()
    result = AgentService(db).chat("Redis为什么快？这题的详情和原文来源")
    assert result["intent"] == "DETAIL"
    assert result["tool_trace"][0]["name"] == "get_question_detail"
    assert result["facts"]["canonical_question_id"] == fast_id
    assert result["facts"]["sources"]
