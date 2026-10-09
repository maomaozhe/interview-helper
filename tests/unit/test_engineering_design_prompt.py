"""Engineering-design scope is available to the planner without losing provenance.

These offline checks cover prompt delivery and context boundaries. Actual model
interpretation and retrieval quality require the separate live evaluation.
"""
from copy import deepcopy
import json
import tomllib
from pathlib import Path

import pytest

from interview_intelligence.agent.harness import ContextCompiler
from interview_intelligence.agent.query_contract import validate_model_plan
from interview_intelligence.config import Settings
from interview_intelligence.resources import resource_path


def test_default_prompt_delivers_broad_example_and_narrow_object_rules():
    version = Settings().query_prompt_version
    prompt = resource_path(f"prompts/{version}.md").read_text(encoding="utf-8")
    assert version == "query_agent_v13"
    assert "排行榜仅为题型示例" in prompt
    assert "只要排行榜/专门找排行榜设计题" in prompt
    assert "排行榜如何设计”是具体解答" in prompt
    assert "coding_focus描述编程任务，不是系统设计领域" in prompt
    assert "题库SYSTEM_DESIGN标签不保证题干符合工程设计意图" in prompt
    assert "纯技术原理、知识点比较、项目经历介绍" in prompt
    manifest = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    included = manifest["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert included[f"prompts/{version}.md"] == f"interview_intelligence/resources/prompts/{version}.md"
    assert len(prompt.encode("utf-8")) <= 14_000


def test_complete_design_search_example_validates_without_inferred_hard_filters():
    prompt = resource_path(f"prompts/{Settings().query_prompt_version}.md").read_text(encoding="utf-8")
    example = next(line for line in prompt.splitlines() if line.startswith('{"action":"SEARCH"'))
    plan = validate_model_plan(json.loads(example))
    assert plan.action == "SEARCH" and plan.final is True
    assert plan.pipeline == "HYBRID_RERANK" and plan.top_n is None
    assert plan.preferred_question_type == "SYSTEM_DESIGN"
    assert all(value is None for key, value in plan.filters.model_dump().items() if key != "date_basis")
    assert len(plan.lexical_facets) == 2  # Leave the third branch for the user's actual example.
    assert plan.search_query != plan.relevance_query


@pytest.mark.parametrize("message", [
    "我要的是工程系统设计题，类似设计一个排行榜这种",
    "只要排行榜设计题，不要其他业务系统",
    "排行榜如何设计？",
])
def test_current_scope_and_previous_expansion_reach_the_planner_separately(message):
    state = {
        "filters": {"topic_l1": "AI", "topic_l2": "Agent", "question_type": "SYSTEM_DESIGN"},
        "last_plan": {"action": "SEARCH", "search_query": "排行榜 排名 TopN Redis",
            "relevance_query": "排行榜系统设计", "lexical_facets": ["排行榜", "系统设计"]},
        "last_response": {"message": "工程系统设计题", "intent": "SEARCH", "answer": "返回 1 道题。"},
        "recent_messages": ["agent场景相关的系统设计题", "工程系统设计题"],
    }
    before = deepcopy(state)
    context = ContextCompiler().compile(message=message, today="2026-10-08", explicit_filters={},
        preferences={}, state=state, default_page_size=20, requested_pipeline="HYBRID_RERANK")
    assert context["message"] == message
    assert context["explicit_filters"] == {}
    assert context["session"]["recent_messages"] == state["recent_messages"]
    assert context["session"]["last_plan"]["relevance_query"] == "排行榜系统设计"
    assert "search_query" not in context["session"]["last_plan"]
    assert context["retrieval_expansions"]["previous_plan"] == {
        "search_query": "排行榜 排名 TopN Redis", "lexical_facets": ["排行榜", "系统设计"]}
    assert context["relevance_intent"]["previous_target_kind"] == "model_reformulation"
    assert context["relevance_intent"]["allow_inferred_requirements"] is False
    assert context["relevance_intent"]["user_sources"] == ["message", "session.recent_messages"]
    assert context["tool_policy"]["allowed_actions"]["search_questions"] == ["SEARCH"]
    assert context["tool_policy"]["allowed_actions"]["answer_question"] == ["ANSWER"]
    assert "record_review" not in context["tool_policy"]["allowed_actions"]
    assert state == before


def test_explicit_ui_constraint_keeps_its_source_during_a_design_correction():
    explicit = {"company": "腾讯", "question_type": "SYSTEM_DESIGN"}
    context = ContextCompiler().compile(
        message="我要工程系统设计题，例如排行榜这种", today="2026-10-08",
        explicit_filters=explicit, preferences={},
        state={"filters": {"topic_l1": "AI", "coding_focus": "ENGINEERING"}},
        default_page_size=20, requested_pipeline="HYBRID_RERANK")
    assert context["explicit_filters"] == explicit
    assert "coding_focus" not in context["explicit_filters"]
    assert context["context_contract"]["precedence"].index("explicit_filters") < (
        context["context_contract"]["precedence"].index("session"))
