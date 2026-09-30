from interview_intelligence.agent.service import AgentService
from test_analytics import seed_corpus


def test_importance_question_uses_topic_overview_tool():
    db, _, _ = seed_corpus()
    result = AgentService(db).chat("Redis 哪些题是核心、常见、长尾？")
    assert result["tool_trace"][0]["name"] == "get_topic_overview"
    assert result["facts"]["groups"]
