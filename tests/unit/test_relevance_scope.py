"""Authorization is attached to the actual object and survives corrections."""
from copy import deepcopy

import pytest

from interview_intelligence.relevance_scope import (
    compile_relevance_scope, scope_digest, semantic_messages, validate_relevance_scope,
)


@pytest.mark.parametrize("message", ["这类题再找一些", "这些问题继续检索更多。", "同类题目再查一批"])
def test_content_free_followup_preserves_original_scope_and_soft_recall(message):
    original = "查具体业务系统设计题"
    assert semantic_messages([original, message]) == [original]
    assert compile_relevance_scope([original, message], candidate_mode=True) == compile_relevance_scope(
        [original], candidate_mode=True)


@pytest.mark.parametrize("message", ["这类题再找一些AI业务应用", "这些问题继续检索订单设计", "同类题目再查一批，排除AI"])
def test_followup_with_additional_conditions_is_still_semantic_language(message):
    assert semantic_messages([message]) == [message]


@pytest.mark.parametrize("text,excluded", [
    ("不包括AI", ["AI"]), ("不包含AI", ["AI"]), ("不要AI", ["AI"]),
    ("排除 AI 和 Agent", ["AGENT", "AI"]), ("不含AI、Agent和MCP", ["AGENT", "AI", "MCP"]),
    ("不要AI但保留Agent应用", ["AI"]), ("不要Agent但保留AI应用", ["AGENT"]),
    ("排除AI但保留Agent应用", ["AI"]), ("保留AI但排除MCP", ["MCP"]),
    ("不要Cache题 AI应用可接受", []), ("不要基础组件而是AI业务应用", []),
    ("不要Redis题和Agent实现原理", []), ("不看缓存 AI应用也可以", []),
    ("不排除AI", []), ("不要排除AI", []), ("别擅自排除AI", []),
    ("不要随便排除AI", []), ("不能排除AI", []), ("不要不包括AI", []),
    ("AI应用不要", ["AI"]), ("AI应用也要", []), ("Agent系统可接受", []),
    ("exclude AI and Agent", ["AGENT", "AI"]), ("without MCP", ["MCP"]),
    ("do not include AI", ["AI"]), ("don't include Agent", ["AGENT"]),
    ("do not exclude AI", []), ("don't exclude Agent", []), ("not exclude MCP", []),
    ("exclude AI but allow Agent applications", ["AI"]),
    ("exclude AI, Agent and MCP", ["AGENT", "AI", "MCP"]),
    ("not include AI", ["AI"]), ("not excluding AI", []),
    ("不用排除AI", []), ("不应该排除AI", []),
    ("别擅自加传统或排除AI", []),
    ("do not include cache AI applications are allowed", []),
    ("为什么排除AI", []), ("why exclude AI", []),
    ("不要AI瞎编题目", []), ("不要AI生成的答案，只找库里的题", []),
    ("不要AI把原话改写了", []), ("exclude AI generated answers", []),
    ("不要AI来决定分类", []), ("不要AI去判断业务范围", []),
    ("不要AI来生成答案", []), ("不要AI类系统设计题", ["AI"]),
])
def test_domain_authorization_binds_each_local_action_to_its_own_object(text, excluded):
    scope = compile_relevance_scope(["具体业务系统设计", text], candidate_mode=True)
    assert scope["excluded_domains"] == excluded
    assert scope["target_unit"] == "BUSINESS_APPLICATION"
    assert not scope["independent_only"]
    for item in scope["exclusion_evidence"]:
        assert item["quote"] in scope["source_messages"][item["message_index"]]
    assert validate_relevance_scope(scope) == scope


@pytest.mark.parametrize("message,target", [
    ("具体业务系统设计", "BUSINESS_APPLICATION"),
    ("business application design", "BUSINESS_APPLICATION"),
    ("不要具体业务系统设计", "ANY_ENGINEERING"),
    ("不要AI业务系统设计", "ANY_ENGINEERING"),
    ("不要传统业务系统设计", "ANY_ENGINEERING"),
    ("不包括电商业务系统设计", "ANY_ENGINEERING"),
    ("exclude AI business systems", "ANY_ENGINEERING"),
    ("不要Cache题 AI业务应用可接受", "BUSINESS_APPLICATION"),
    ("不要基础组件而是业务应用设计", "BUSINESS_APPLICATION"),
    ("不只业务系统设计", "ANY_ENGINEERING"),
    ("不限于具体业务系统设计", "ANY_ENGINEERING"),
    ("取消业务系统设计限定", "ANY_ENGINEERING"),
    ("业务系统设计以外的题", "ANY_ENGINEERING"),
    ("不需要业务系统设计", "ANY_ENGINEERING"),
    ("not limited to business systems", "ANY_ENGINEERING"),
    ("not only business applications", "ANY_ENGINEERING"),
])
def test_business_target_negations_cannot_become_a_positive_business_restriction(message, target):
    scope = compile_relevance_scope([message], candidate_mode=True)
    assert scope["target_unit"] == target
    assert bool(scope["target_evidence"]) is (target == "BUSINESS_APPLICATION")


@pytest.mark.parametrize("message,independent", [
    ("只看独立系统设计题", True), ("只要完整系统设计", True),
    ("independent systems", True), ("只看端到端系统设计", True),
    ("不要限定独立系统", False), ("不要求独立系统", False),
    ("不要再限定独立系统", False), ("不需要只看独立系统", False),
    ("不限定完整系统", False),
    ("无需独立", False), ("不必是独立系统", False),
    ("不只看独立系统", False), ("独立系统不是必要条件", False),
    ("do not require independent systems", False), ("not necessarily independent system", False),
    ("not only independent systems", False), ("don't limit to independent systems", False),
])
def test_independence_is_retained_without_business_target_and_never_inferred_from_negation(message, independent):
    scope = compile_relevance_scope([message], candidate_mode=True)
    assert scope["independent_only"] is independent
    if independent:
        assert compile_relevance_scope([message])["independent_only"] is True


def test_later_corrections_and_repeated_messages_keep_order_and_new_target_evidence():
    messages = ["具体业务系统设计", "排除AI", "包含AI", "排除AI", "不只业务系统设计", "business application design"]
    scope = compile_relevance_scope(messages, candidate_mode=True)
    assert scope["source_messages"] == messages
    assert scope["target_unit"] == "BUSINESS_APPLICATION"
    assert scope["target_evidence"] == [{"message_index": 5, "quote": "business application design"}]
    assert scope["exclusion_evidence"] == [{"domain": "AI", "message_index": 3, "quote": "排除AI"}]
    cleared = compile_relevance_scope([*messages, "AI也要", "不要求独立"], candidate_mode=True)
    assert cleared["excluded_domains"] == [] and cleared["independent_only"] is False
    assert cleared["scope_hash"] != scope["scope_hash"]
    assert compile_relevance_scope(["只看独立系统", "不限定完整系统"], candidate_mode=True)["independent_only"] is False


@pytest.mark.parametrize("first,correction,expected", [
    ("排除AI和Agent", "Agent业务应用保留", ["AI"]),
    ("排除AI", "AI业务应用也要", []),
    ("排除AI", "AI相关业务应用可接受", []),
    ("排除Agent", "为什么排除AI？不要排除Agent", []),
    ("exclude Agent", "why exclude AI? include Agent", []),
])
def test_independent_audit_corrections_preserve_local_scope_and_do_not_inherit_prior_question_prefix(first, correction, expected):
    scope = compile_relevance_scope(["具体业务系统设计", first, correction], candidate_mode=True)
    assert scope["excluded_domains"] == expected


@pytest.mark.parametrize("correction,excluded", [
    ("不要AI业务应用", ["AI"]), ("不用排除AI业务应用", []),
    ("不要AI业务应用生成答案", []), ("不要传统业务系统", []),
])
def test_negating_or_allowing_a_qualified_subdomain_does_not_cancel_the_existing_business_target(correction, excluded):
    scope = compile_relevance_scope(["具体业务系统设计", correction], candidate_mode=True)
    assert scope["target_unit"] == "BUSINESS_APPLICATION"
    assert scope["excluded_domains"] == excluded


def test_control_messages_are_never_semantic_authorization_or_extra_source_ordinals():
    inputs = ["列出所有工程系统设计题", "下一页", "具体业务系统设计", "按题意查找候选", "重新检索", "全量分页"]
    assert semantic_messages(inputs) == [inputs[0], inputs[2]]
    scope = compile_relevance_scope(inputs, candidate_mode=True)
    assert scope["target_evidence"][0]["message_index"] == 1
    assert scope["retrieval_mode"] == "CANDIDATES"


def test_validate_recomputes_authorization_even_if_an_attacker_rehashes_a_forged_scope():
    original = compile_relevance_scope(["具体业务系统设计", "不要Cache题 AI应用可接受"], candidate_mode=True)
    forged = deepcopy(original)
    forged["excluded_domains"] = ["AI"]
    forged["exclusion_evidence"] = [{"domain": "AI", "message_index": 1, "quote": "不要Cache题 AI应用可接受"}]
    forged["scope_hash"] = scope_digest(forged)
    with pytest.raises(ValueError, match="RELEVANCE_SCOPE_EXCLUSION_UNAUTHORIZED"):
        validate_relevance_scope(forged)
    forged = deepcopy(original)
    forged["independent_only"] = True
    forged["independence_evidence"] = [{"message_index": 0, "quote": "具体业务系统设计"}]
    forged["scope_hash"] = scope_digest(forged)
    with pytest.raises(ValueError, match="RELEVANCE_SCOPE_INDEPENDENCE_UNGROUNDED"):
        validate_relevance_scope(forged)


@pytest.mark.parametrize("message", ["不只业务系统设计", "不要具体业务系统设计", "not only business applications"])
def test_validator_cannot_accept_a_positive_quote_cut_out_of_its_negative_context(message):
    scope = compile_relevance_scope([message], candidate_mode=True)
    scope["target_unit"] = "BUSINESS_APPLICATION"
    quote = "business applications" if "business" in message else "具体业务系统设计" if "具体" in message else "业务系统设计"
    scope["target_evidence"] = [{"message_index": 0, "quote": quote}]
    scope["scope_hash"] = scope_digest(scope)
    with pytest.raises(ValueError, match="RELEVANCE_SCOPE_TARGET_UNGROUNDED"):
        validate_relevance_scope(scope)


@pytest.mark.parametrize("changes", [
    {"traditional_only": True}, {"scope_hash": "0" * 64}, {"scope_hash": {"raw": "payload"}},
    {"scope_hash": "a" * 63}, {"scope_hash": "A" * 64}, {"version": "query_relevance_scope_v2"},
    {"excluded_domains": ["TRADITIONAL"]}, {"excluded_domains": [{"domain": "AI"}]},
    {"independent_only": 1}, {"target_unit": "TRADITIONAL_BUSINESS"},
    {"target_unit": {"evil": "business"}}, {"retrieval_mode": ["CANDIDATES"]},
    {"target_evidence": [{"message_index": True, "quote": "具体业务系统设计"}]},
    {"target_evidence": [{"message_index": 0, "quote": "具体业务系统设计", "raw_source": "private"}]},
])
def test_unknown_fields_hash_payloads_and_untyped_authorization_are_rejected(changes):
    scope = compile_relevance_scope(["具体业务系统设计"], candidate_mode=True)
    scope.update(changes)
    with pytest.raises(ValueError):
        validate_relevance_scope(scope)


def test_validation_is_a_deep_copy_and_does_not_change_or_trust_the_original_object():
    scope = compile_relevance_scope(["具体业务系统设计", "不要AI但保留Agent"], candidate_mode=True)
    before = deepcopy(scope)
    validated = validate_relevance_scope(scope)
    validated["source_messages"].append("改了副本")
    assert scope == before
