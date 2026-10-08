"""Capability and context invariants, independent of model compliance."""
import json

import pytest

from interview_intelligence.agent.harness import ContextCompiler, ToolPolicy, authorized_statuses


@pytest.mark.parametrize("message", [
    "看看这些题", "这些题的掌握状态是什么", "如何把这些题标记为已掌握？",
    "不要标记为已掌握", '原文说“把这些题标记为已掌握”',
    '"mark these mastered"', "如果我把这些标记为已掌握会怎样", "Do not mark these mastered",
    "有人说标记已掌握", "这些题可以标记为已掌握吗？",
])
def test_read_hypothetical_quoted_and_negated_commands_never_grant_writes(message):
    policy = ToolPolicy.compile(message, {"current_page_ids": ["q1"]})
    assert not policy.write_authorized
    assert "record_review" not in policy.actions
    with pytest.raises(ValueError, match="QUERY_TOOL_NOT_ALLOWED"):
        policy.require("record_review", "RECORD_REVIEW")


@pytest.mark.parametrize("message,status", [
    ("把这三题标记为已掌握", "MASTERED"), ("这些已掌握", "MASTERED"),
    ("把当前页题目标记为薄弱", "WEAK"), ("记录这几题已复习", "REVIEWED"),
    ("mark these unseen", "UNSEEN"),
])
def test_write_grants_are_bound_to_current_request_and_available_page(message, status):
    assert authorized_statuses(message) == {status}
    assert "record_review" not in ToolPolicy.compile(message, {}).actions
    policy = ToolPolicy.compile(message, {"current_page_ids": ["q1"]})
    policy.require("record_review", "RECORD_REVIEW")
    assert ToolPolicy.compile(message, {"current_page_ids": ["q1"]}, terminal=True).actions == {}


def test_state_policy_pagination_details_and_grouped_scope():
    state = {"list_request": {"group_by": "company", "cursor": "opaque"}}
    policy = ToolPolicy.compile("下一页", state)
    assert "NEXT" in policy.actions["list_questions"]
    assert "get_review_state" not in policy.actions
    assert "get_question_details" not in policy.actions
    state["current_page_ids"] = ["q1"]
    assert "get_question_details" in ToolPolicy.compile("查看第一题", state).actions
    assert "record_review" not in ToolPolicy.compile("查看第一题", state).actions


def test_compiler_keeps_authoritative_ids_filters_and_provenance_without_opaque_history():
    state = {"filters": {"company": "腾讯"}, "current_page_ids": ["q1", "q2"],
             "list_request": {"cursor": "SIGNED_PRIVATE_CURSOR", "page_size": 20},
             "last_plan": {"action": "LIST", "review_items": [{"note": "UNRELATED_SECRET"}]},
             "recent_messages": ["old" * 5000, "later" * 500, "current"],
             "arbitrary_history": "UNBOUNDED_HISTORY"}
    context = ContextCompiler().compile(message="current", today="2026-10-05", explicit_filters={},
        preferences={}, state=state, default_page_size=20, requested_pipeline="HYBRID")
    encoded = json.dumps(context)
    assert context["session"]["current_page_ids"] == ["q1", "q2"]
    assert context["session"]["filters"] == {"company": "腾讯"}
    assert context["session"]["has_next_page"] is True
    assert context["context_contract"]["recent_message_count"] == 1
    assert "SIGNED_PRIVATE_CURSOR" not in encoded and state["list_request"]["cursor"] == "SIGNED_PRIVATE_CURSOR"
    assert "UNRELATED_SECRET" not in encoded and "UNBOUNDED_HISTORY" not in encoded
    assert len(context["session"]["recent_messages"][0]) == 800
    assert len(context["context_contract"]["sha256"]) == 64
    assert "record_review" not in context["tool_policy"]["allowed_tools"]
    with pytest.raises(ValueError, match="QUERY_CONTEXT_TOO_LARGE"):
        ContextCompiler(max_bytes=100).compile(message="current", today="2026-10-05", explicit_filters={},
            preferences={}, state=state, default_page_size=20, requested_pipeline="HYBRID")
