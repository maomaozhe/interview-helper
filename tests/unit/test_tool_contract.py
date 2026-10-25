"""Static declaration parity and host compatibility at the Python/Pi boundary."""
import json
from pathlib import Path
import subprocess
import sys
from typing import get_args

import pytest

from interview_intelligence.agent.query_contract import QuerySpec, query_model_schema, validate_model_plan
from interview_intelligence.agent.tool_definitions import COMMON_REQUIRED, TOOL_ACTIONS, TOOL_DEFINITIONS


ROOT = Path(__file__).resolve().parents[2]


def test_generated_pi_declarations_are_current_even_outside_the_repository(tmp_path):
    subprocess.run([sys.executable, str(ROOT / "scripts/sync-tool-contract.py"), "--check"],
                   cwd=tmp_path, check=True, capture_output=True, text=True)


def test_declarations_preserve_legacy_fields_and_expose_action_scope_constraints():
    actions = [action for tool in TOOL_DEFINITIONS.values() for action in tool["actions"]]
    assert len(actions) == len(set(actions))
    assert set(actions) == set(get_args(QuerySpec.model_fields["action"].annotation))
    assert sorted(tool["pi_order"] for tool in TOOL_DEFINITIONS.values()) == list(range(8))
    for tool in TOOL_DEFINITIONS.values():
        declared = set(COMMON_REQUIRED) | set(tool["fields"])
        assert declared <= QuerySpec.model_fields.keys()
        assert set(tool["required"]) | set(tool.get("pi_required_extra", [])) <= declared
    before = json.loads((ROOT / "tests/fixtures/query-tool-contract-before.json").read_text(encoding="utf-8"))
    expected = before["input_schema"]
    task = expected["properties"]["semantic_task"]["anyOf"][0]
    assert task == {"const": "ALGORITHM_PROBLEM", "type": "string"}
    expected["properties"]["semantic_task"]["anyOf"][0] = {
        "enum": ["ALGORITHM_PROBLEM", "ENGINEERING_DESIGN"], "type": "string"}
    actual = query_model_schema()
    assert actual.pop("allOf")
    assert actual["properties"].pop("user_goal")["default"] is None
    assert actual["properties"].pop("evidence_tool_ordinals")["maxItems"] == 8
    goal = actual["$defs"].pop("UserGoal")
    assert goal["properties"]["response_mode"]["enum"] == ["DIRECT_RESULT", "SYNTHESIS"]
    assert "SEARCH must set semantic_task=null" in actual["properties"]["semantic_task"].pop("description")
    assert actual == expected
    assert list(TOOL_ACTIONS) == ["list_questions", "search_questions", "get_question_stats",
                                "get_question_count", "get_question_details", "answer_question",
                                "get_review_state", "record_review"]


@pytest.mark.parametrize("field", ["answer_kind", "answer_basis", "final"])
def test_host_answer_defaults_remain_distinct_from_pi_required_fields(field):
    plan = QuerySpec(action="ANSWER", answer_text="A direct answer.").model_dump(mode="json")
    plan.pop(field)
    answer = validate_model_plan(plan)
    assert answer.answer_kind == "EXPLAIN" and answer.answer_basis == "GENERAL_KNOWLEDGE" and answer.final


def test_model_schema_policy_does_not_mutate_later_requests_or_static_definitions():
    restricted = query_model_schema({"allowed_actions": {"answer_question": ["ANSWER", "RECORD_REVIEW"],
                                                        "unknown_tool": ["LIST"]}})
    assert restricted["properties"]["action"]["enum"] == ["ANSWER"]
    restricted["required"].clear()
    assert query_model_schema()["required"] == list(COMMON_REQUIRED)
    assert TOOL_ACTIONS["answer_question"] == {"ANSWER"}


@pytest.mark.parametrize("action", ["SEARCH", "ANSWER", "DETAILS", "REVIEW_STATE", "RECORD_REVIEW", "CLARIFY"])
def test_python_model_schema_separates_candidate_search_from_complete_sql_scope(action):
    guard = query_model_schema()["allOf"][0]
    sql_actions = guard["if"]["properties"]["action"]["not"]["enum"]
    assert action not in sql_actions and set(sql_actions) == {"LIST", "NEXT", "COUNT", "STATS"}
    assert guard["then"]["properties"]["semantic_task"] == {"type": "null"}
    if action == "SEARCH":
        search = query_model_schema()["allOf"][1]
        assert search["if"]["properties"]["action"]["const"] == action
        assert search["then"]["properties"]["top_n"] == {"type": "null"}
        assert search["then"]["properties"]["page_size"]["maximum"] == 50
        assert search["then"]["properties"]["search_query"]["minLength"] == 1
        for task in ("ENGINEERING_DESIGN", "ALGORITHM_PROBLEM"):
            with pytest.raises(ValueError, match="SEMANTIC_SCOPE_ACTION_CONFLICT"):
                QuerySpec(action=action, search_query="设计任务", semantic_task=task)


@pytest.mark.parametrize("action", ["LIST", "NEXT", "COUNT", "STATS"])
def test_complete_sql_scope_remains_available_for_its_supported_actions(action):
    assert QuerySpec(action=action, semantic_task="ENGINEERING_DESIGN").semantic_task == "ENGINEERING_DESIGN"
    assert QuerySpec(action="SEARCH", search_query="设计任务").semantic_task is None
