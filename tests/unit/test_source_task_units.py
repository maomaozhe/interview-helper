"""Offline source-local proof units; no synthetic model-success claims."""
import pytest

from interview_intelligence.search.citations import primary_wire_payload, resolve_task_option
from interview_intelligence.search.design_scope import (
    DesignVerification, design_candidate_payload, design_proof_assessment,
    engineering_coding_task_allowed, resolve_design_evidence,
)
from interview_intelligence.search.scenario_scope import (
    ScenarioVerification, ScenarioScopeResult, concept_only_evidence, scenario_candidate_payload,
    scenario_evidence_schema, scenario_proof_assessment,
)


def candidate(raw, canonical=None, **source):
    return {"canonical_text": canonical or raw, "source_context": [{
        "source_context_status": "VERIFIED", "original_question": raw,
        "original_question_complete": True, **source}]}


def scenario(row, situation="s0o0", task="s0o0", kind="SOLUTION"):
    payload = scenario_candidate_payload([row])[0]
    proof = ScenarioVerification(candidate_id="c0", keep=True, reason="offline fixture",
        scenario_task=kind, situation_evidence=situation, application_evidence=task)
    return scenario_proof_assessment(row, proof, payload)


def design(row, evidence="s0o0", unit="COMPONENT_OR_WORKFLOW", query="工程场景设计题"):
    payload = design_candidate_payload([row])[0]
    proof = DesignVerification(candidate_id="c0", keep=True, reason="offline fixture",
        design_unit=unit, construction_evidence=evidence, constraint_a="", constraint_b="")
    return design_proof_assessment(row, resolve_design_evidence(proof, payload), query=query)


def test_canonical_rewrite_not_offered_as_scenario_proof_but_object_text_retained():
    row = candidate("队列消息重复怎么办", "消息队列遇到重复消息该如何处理？")
    payload = scenario_candidate_payload([row])[0]
    assert "q0" not in payload["scenario_options"]
    assert "s0o0" in payload["scenario_options"]
    wire = primary_wire_payload([payload])[0]
    assert wire["question"]["q0"] == row["canonical_text"]
    schema = ScenarioScopeResult.model_json_schema()
    scenario_evidence_schema(schema, [payload])
    assert "q0" not in schema["$defs"]["ScenarioVerification"]["properties"]["application_evidence"]["enum"]
    assert scenario(row, "q0", "q0")["status"] == "UNKNOWN"
    assert scenario(row)["status"] == "PASS"


@pytest.mark.parametrize("raw", [
    "缓存中的大型对象怎么拆？", "服务宕机后怎么找到配置？",
    "消费端怎么去保证消息不丢失？", "消息重复上报怎么去重？",
])
def test_applied_verbs_are_source_grounded_without_topic_specific_whitelists(raw):
    row = candidate(raw)
    assert concept_only_evidence(row) is None
    assert scenario(row)["status"] == "PASS"


@pytest.mark.parametrize("raw,situation,task", [
    ("存储集群中，热点数据导致负载不均衡，怎么解决？", "s0o1", "s0o2"),
    ("在处理外部异步回调时，如何保证信号不丢失？", "s0o0", "s0o1"),
])
def test_runtime_state_and_applied_context_need_not_match_a_fault_noun_list(raw, situation, task):
    assert scenario(candidate(raw), situation, task)["status"] == "PASS"


@pytest.mark.parametrize("raw", [
    "如果节点失联，正在执行的任务是不是会重复提交？",
    "假设投递恰好一次，还有必要保留幂等兜底吗？",
    "会不会出现这种情况，存储服务宕机，导致所有任务重新执行？",
])
def test_given_hypothesis_can_ask_for_consequence_or_tradeoff_verification(raw):
    row = candidate(raw)
    payload = scenario_candidate_payload([row])[0]
    ranges = [key for key in payload["scenario_ranges"] if key.startswith("s")]
    assert ranges
    assert any(scenario(row, key, key, "DIAGNOSIS")["status"] == "PASS" for key in ranges)


@pytest.mark.parametrize("raw", [
    "队列是否会重复发送消息？", "有没有兜底机制？",
    "如果从原理角度解释，系统是否会重复发送消息？",
    "如果过期机制运行，是否会清理缓存？",
])
def test_unconditioned_or_mechanism_explanations_do_not_become_hypothesis_tasks(raw):
    row = candidate(raw)
    payload = scenario_candidate_payload([row])[0]
    for key in [k for k in payload["scenario_options"] if k.startswith("s")]:
        assert scenario(row, key, key, "DIAGNOSIS")["status"] != "PASS"


@pytest.mark.parametrize("raw", [
    "介绍存储内部怎么拆分数据的机制。", "队列底层如何去重，其原理是什么？",
    "解释系统如何找到配置的内部机制。", "如何设计组件的内部原理？",
])
def test_new_action_forms_do_not_turn_existing_mechanism_explanations_into_tasks(raw):
    assert scenario(candidate(raw))["status"] != "PASS"


def test_same_question_range_retains_target_and_task_without_rewriting_history():
    row = candidate("大量请求可能重复提交，怎么处理？")
    sp = scenario_candidate_payload([row])[0]
    dp = design_candidate_payload([row])[0]
    assert sp["scenario_ranges"]["s0o0:s0o1"] == ["s0o0", "s0o1"]
    assert dp["construction_ranges"]["s0o0:s0o1"] == ["s0o0", "s0o1"]
    assert "s0o1" not in dp["construction_options"]  # do not invite a known-invalid bare selection
    assert design(row, "s0o1")["grounded"] is False  # old bare proof stays invalid
    assert design(row, "s0o0:s0o1")["grounded"] is True
    assert scenario(row, "s0o0:s0o1", "s0o0:s0o1")["status"] == "PASS"
    wire = primary_wire_payload([sp])[0]
    assert wire["scenario_ranges"] == sp["scenario_ranges"]


def test_ranges_do_not_join_numbered_questions_other_occurrences_or_neighbor_text():
    row = candidate("1. 给定大量重复请求。2. 怎么处理？")
    payload = scenario_candidate_payload([row])[0]
    assert "s0o0:s0o1" not in payload["scenario_ranges"]
    forged = {"s0o0:s0o1": ["s0o0", "s0o1"]}
    assert resolve_task_option("s0o0:s0o1", payload, forged) is None
    row["source_context"].append({"source_context_status": "VERIFIED", "original_question": "怎么处理？"})
    payload = scenario_candidate_payload([row])[0]
    assert resolve_task_option("s0o0:s1o0", payload, {"s0o0:s1o0": ["s0o0", "s1o0"]}) is None
    row = candidate("给定重复请求。", context_after="请设计一个接口。")
    assert not scenario_candidate_payload([row])[0]["scenario_ranges"]


@pytest.mark.parametrize("raw", [
    "百万连接下服务怎么拆？", "如何兼顾并发、延迟与稳定性？",
    "怎么做心跳检测或连接复用？",
])
def test_broad_engineering_implementation_and_tradeoff_directives(raw):
    assert design(candidate(raw))["grounded"]
    assert design(candidate(raw), unit="NEW_OR_REBUILT_SYSTEM")["grounded"]
    assert not design(candidate(raw), unit="NEW_OR_REBUILT_SYSTEM", query="独立系统设计题")["grounded"]


def test_engineering_coding_form_still_requires_original_delivery_and_broad_scope():
    row = candidate("代码题：设计一个缓存系统，支持可靠读写和故障恢复。")
    assert engineering_coding_task_allowed(row, query="工程场景设计题")
    assert design(row, "s0o1", "NEW_OR_REBUILT_SYSTEM")["grounded"]
    assert not engineering_coding_task_allowed(row, query="独立系统设计题")
    assert not engineering_coding_task_allowed(row, query="系统架构题")
    assert not design(row, "s0o1", "NEW_OR_REBUILT_SYSTEM", "独立系统设计题")["grounded"]
    bad = candidate("代码题：反转数组。", "设计一个可靠存储系统。")
    assert not engineering_coding_task_allowed(bad, query="工程场景设计题")
    assert not design(bad, "q0", "NEW_OR_REBUILT_SYSTEM")["grounded"]


@pytest.mark.parametrize("raw", [
    "代码题：实现二叉树层序遍历。", "写代码解决这个问题。", "手写并查集数据结构。",
    "代码题：实现一个系统中的链表反转。", "在调度服务中手写快速排序算法。",
    "代码题：介绍设计一个通知服务的原理。",
    "介绍做过的缓存系统。", "解释接口设计原理。",
])
def test_coding_or_engineering_nouns_alone_do_not_establish_delivery(raw):
    assert not engineering_coding_task_allowed(candidate(raw), query="工程场景设计题")


@pytest.mark.parametrize("raw,goal", [
    ("代码题：介绍设计一个通知服务的原理。", "设计一个通知服务"),
    ("代码题：解释如何实现一个存储组件的内部机制。", "实现一个存储组件"),
    ("代码题：介绍你做过的项目中设计一个通知服务的过程。", "设计一个通知服务"),
])
def test_coding_canonical_substring_cannot_discard_original_explanation_or_history(raw, goal):
    row = candidate(raw, goal)
    assert goal in raw  # exact occurrence alone does not prove a construction request
    assert not engineering_coding_task_allowed(row, query="工程场景设计题", evidence=goal)
    assert not design(row, "q0", "NEW_OR_REBUILT_SYSTEM")["grounded"]


def test_coding_raw_principle_explanation_cannot_pass_positive_direct_scope_proof():
    row = candidate("代码题：介绍设计一个通知服务的原理。")
    assert not design(row, "s0o1", "NEW_OR_REBUILT_SYSTEM")["grounded"]


@pytest.mark.parametrize("raw,goal", [
    ("代码题：介绍通知服务的原理，再设计一个告警组件。", "设计一个告警组件"),
    ("代码题：先说明缓存的内部机制，然后实现一个缓存服务。", "实现一个缓存服务"),
])
def test_coding_mixed_explanation_keeps_separate_real_construction(raw, goal):
    row = candidate(raw, goal)
    assert engineering_coding_task_allowed(row, query="工程场景设计题", evidence=goal)
    assert design(row, "q0", "NEW_OR_REBUILT_SYSTEM")["grounded"]
    assert not design(row, "q0", "NEW_OR_REBUILT_SYSTEM", "独立系统设计题")["grounded"]


@pytest.mark.parametrize("unit", ["NEW_OR_REBUILT_SYSTEM", "COMPONENT_OR_WORKFLOW", "COUPLED_MECHANISM"])
def test_all_design_units_reject_canonical_phrase_inside_original_explanation(unit):
    row = candidate("介绍设计一个通知服务的原理，支持重复投递，要求状态一致。", "设计一个通知服务")
    proof = DesignVerification(candidate_id="c0", keep=True, reason="offline fixture", design_unit=unit,
        construction_evidence="q0", constraint_a="支持重复投递", constraint_b="要求状态一致")
    resolved = resolve_design_evidence(proof, design_candidate_payload([row])[0])
    result = design_proof_assessment(row, resolved, query="工程场景设计题")
    assert not result["grounded"]
    assert result["host_reason"] == "ORIGINAL_DELIVERY_TASK_NOT_ESTABLISHED"
    assert result["invalid_evidence_fields"] == []  # complete explanation, not a clipped-prefix claim


@pytest.mark.parametrize("unit", ["NEW_OR_REBUILT_SYSTEM", "COMPONENT_OR_WORKFLOW"])
def test_another_target_cannot_rescue_explanatory_goal_but_its_own_task_remains_valid(unit):
    raw = "介绍设计一个通知服务的原理，再实现一个缓存组件。"
    row = candidate(raw, "设计一个通知服务")
    assert not design(row, "q0", unit)["grounded"]
    assert not design(row, "s0o0", unit)["grounded"]  # its trailing comma must not borrow the next clause
    assert design(row, "s0o1", unit)["grounded"]


@pytest.mark.parametrize("unit", ["NEW_OR_REBUILT_SYSTEM", "COMPONENT_OR_WORKFLOW"])
def test_same_original_repeated_words_retain_the_selected_source_offset(unit):
    row = candidate("介绍设计一个通知服务的原理；随后请设计一个通知服务。", "设计一个通知服务")
    assert not design(row, "s0o0", unit)["grounded"]
    assert design(row, "s0o1", unit)["grounded"]
    # A canonical/literal phrase has no claimed occurrence offset; it can use
    # the actual second construction, never invent one absent from the source.
    assert design(row, "q0", unit)["grounded"]


@pytest.mark.parametrize("unit", ["NEW_OR_REBUILT_SYSTEM", "COMPONENT_OR_WORKFLOW"])
def test_same_words_in_other_occurrence_cannot_rescue_selected_explanation(unit):
    row = candidate("介绍设计一个通知服务的原理。", "设计一个通知服务", occurrence_id="a")
    row["source_context"].append({"source_context_status": "VERIFIED", "original_question": "请设计一个通知服务。",
                                  "original_question_complete": True, "occurrence_id": "b"})
    assert not design(row, "s0o0", unit)["grounded"]
    assert design(row, "s1o0", unit)["grounded"]


@pytest.mark.parametrize("raw,goal,unit", [
    ("解释已有原理后设计一个通知服务。", "设计一个通知服务", "NEW_OR_REBUILT_SYSTEM"),
    ("解释已有原理后实现一个审批工作流。", "实现一个审批工作流", "COMPONENT_OR_WORKFLOW"),
    ("解释已有原理，如何优化通知接口？", "如何优化通知接口？", "COMPONENT_OR_WORKFLOW"),
    ("解释已有原理，如何处理重复请求？", "如何处理重复请求？", "COMPONENT_OR_WORKFLOW"),
])
def test_mixed_explanation_preserves_the_selected_actual_delivery_clause(raw, goal, unit):
    assert design(candidate(raw, goal), "q0", unit)["grounded"]


@pytest.mark.parametrize("unit", ["NEW_OR_REBUILT_SYSTEM", "COMPONENT_OR_WORKFLOW"])
def test_truncated_explanation_tail_is_unknown_but_complete_prefix_task_can_pass(unit):
    clipped = candidate("介绍设计一个通知服务的", "设计一个通知服务", original_question_complete=False)
    result = design(clipped, "q0", unit)
    assert not result["grounded"] and result["invalid_evidence_fields"] == ["construction_evidence"]
    prefix = candidate("请设计一个通知服务。后续条件", "设计一个通知服务", original_question_complete=False)
    assert design(prefix, "s0o0", unit)["grounded"]


def test_coupled_observable_goal_keeps_same_source_constraints_and_origin_is_private():
    row = candidate("解释既有原理，事件列表需要实时刷新，如何保证高吞吐？")
    proof = DesignVerification(candidate_id="c0", keep=True, reason="offline fixture", design_unit="COUPLED_MECHANISM",
        construction_evidence="s0o1", constraint_a="s0o1", constraint_b="s0o2")
    payload = design_candidate_payload([row])[0]
    resolved = resolve_design_evidence(proof, payload)
    assert resolved._evidence_origins["construction_evidence"]["source_context_index"] == 0
    assert "_evidence_origins" not in resolved.model_dump()
    assert "_evidence_origins" not in DesignVerification.model_json_schema()["properties"]
    assert design_proof_assessment(row, resolved, query="工程场景设计题")["grounded"]
    row["source_context"].append({**row["source_context"][0], "occurrence_id": "second"})
    payload = design_candidate_payload([row])[0]
    split = resolve_design_evidence(proof.model_copy(update={"constraint_b": "s1o2"}), payload)
    assert not design_proof_assessment(row, split, query="工程场景设计题")["grounded"]


def test_selected_cross_comma_task_range_keeps_its_actual_goal_and_construction():
    row = candidate("需要一个通知服务，如何设计？")
    assert design(row, "s0o0:s0o1", "NEW_OR_REBUILT_SYSTEM")["grounded"]


_LONG_EXPLANATIONS = [
    "介绍设计一个通知服务时如何结合消息队列完成事件订阅与持久化存储以及失败恢复和重复投递处理并保持请求状态一致和接收端幂等以及不同租户权限隔离和多实例并发访问并考虑容量限制与稳定性还有网络中断后的恢复和补偿机制以及状态回溯和日志记录的原理。",
    "介绍设计一个通知服务时" + "retry_" * 23 + "的原理。",
]


@pytest.mark.parametrize("raw", _LONG_EXPLANATIONS)
@pytest.mark.parametrize("unit", ["NEW_OR_REBUILT_SYSTEM", "COMPONENT_OR_WORKFLOW"])
def test_complete_long_explanation_cannot_lose_its_frame_after_100_characters(raw, unit):
    assert len(raw) > 100 and len(raw.encode("utf-8")) <= 500
    result = design(candidate(raw, "设计一个通知服务"), "q0", unit)
    assert not result["grounded"]
    assert result["host_reason"] == "ORIGINAL_DELIVERY_TASK_NOT_ESTABLISHED"
    assert result["invalid_evidence_fields"] == []


@pytest.mark.parametrize("raw", _LONG_EXPLANATIONS)
@pytest.mark.parametrize("unit", ["NEW_OR_REBUILT_SYSTEM", "COMPONENT_OR_WORKFLOW"])
def test_long_explanation_does_not_cross_into_subsequent_actual_construction(raw, unit):
    mixed = raw + "再设计一个缓存组件。"
    assert len(mixed.encode("utf-8")) <= 500
    assert design(candidate(mixed, "设计一个缓存组件"), "q0", unit)["grounded"]


def test_truncated_original_cannot_use_canonical_tail_to_construct_a_proof():
    row = candidate("介绍旧系统的内部机制。", "介绍旧系统的内部机制，再设计一个新组件。", original_question_complete=False)
    payload = scenario_candidate_payload([row])[0]
    assert "q1" not in payload["scenario_options"]
    assert scenario(row, "q1", "q1", "DESIGN")["status"] == "UNKNOWN"
    assert not design(row, "q1", "NEW_OR_REBUILT_SYSTEM")["grounded"]
