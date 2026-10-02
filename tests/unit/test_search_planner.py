import json
from types import SimpleNamespace

import pytest

from interview_intelligence.contracts import FilterSpec


def test_planner_expands_implicit_topic_without_inventing_filters():
    from interview_intelligence.agent.planner import SearchPlanner
    requests, logs = [], []
    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(model="test-model", usage=None, choices=[SimpleNamespace(
            message=SimpleNamespace(content=json.dumps({"query": "OOM 内存溢出 排查",
                "alternatives": ["内存泄漏 内存持续增长"], "needs_clarification": False, "clarification": None})))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    planner = SearchPlanner(client=client, model="test", on_call=logs.append)
    plan = planner.plan("oom的常见问法有哪些", FilterSpec(company="得物"))
    assert "内存溢出" in plan.retrieval_query
    assert "内存泄漏" in plan.retrieval_query
    assert "topic_l1" not in requests[0]["response_format"]["json_schema"]["schema"]["properties"]
    assert logs[0]["operation_type"] == "SEARCH_PLAN"


def test_planner_rejects_unknown_tools_or_excessively_long_queries():
    from interview_intelligence.agent.planner import SearchPlan
    with pytest.raises(ValueError):
        SearchPlan(query="OOM", alternatives=[], needs_clarification=False, clarification=None,
                   tool="run_shell")
    with pytest.raises(ValueError):
        SearchPlan(query="x" * 400, alternatives=["y" * 400], needs_clarification=False, clarification=None)
