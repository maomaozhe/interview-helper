"""Design units follow user scope; genuine evidence failures stay failures."""
import json

import pytest

from interview_intelligence.search.design_scope import (
    DesignVerification,
    design_candidate_payload,
    design_evidence_schema,
    design_proof_assessment,
    design_scope_instructions,
    design_scope_policy,
    design_scope_required,
    resolve_design_evidence,
)


def assess(text, unit="NEW_OR_REBUILT_SYSTEM", *, query="工程设计题", goal=None, **extra):
    candidate = {"canonical_text": text, **extra.pop("candidate", {})}
    proof = DesignVerification(candidate_id="c0", keep=True, reason="offline fixture", design_unit=unit,
        construction_evidence=text if goal is None else goal, constraint_a=extra.pop("first", ""),
        constraint_b=extra.pop("second", ""))
    return design_proof_assessment(candidate, proof, query=query)


@pytest.mark.parametrize("query", ["工程设计题", "agent工程设计题", "工程场景设计题", "工作流设计题", "业务系统设计题"])
def test_a_design_category_is_not_an_implicit_independent_system_limit(query):
    assert design_scope_required(query)
    assert design_scope_policy(query) == "ENGINEERING_DELIVERY"
    assert "COMPONENT_OR_WORKFLOW" in design_scope_instructions(query)


@pytest.mark.parametrize("query", ["只要独立系统设计题", "主设计任务，不要组件题", "只要完整系统架构题"])
def test_explicit_independent_delivery_limits_still_apply(query):
    assert design_scope_policy(query) == "INDEPENDENT_SYSTEM"
    result = assess("设计调度器内部的任务缓冲队列。", "COMPONENT_OR_WORKFLOW", query=query)
    assert result["grounded"] is False
    assert result["host_reason"] == "DESIGN_UNIT_OUT_OF_SCOPE"


@pytest.mark.parametrize("query", ["不要求独立系统，组件设计也要", "无需独立系统，工程设计题即可", "不限定独立设计，包含机制题"])
def test_negating_an_independent_limit_does_not_reinstate_it(query):
    assert design_scope_policy(query) == "ENGINEERING_DELIVERY"


@pytest.mark.parametrize("query", ["设计模式的原理题", "Redis场景题", "数据库知识题", "算法题"])
def test_unrelated_or_nominal_design_terms_do_not_force_design_verification(query):
    assert not design_scope_required(query)


@pytest.mark.parametrize("text", [
    "做一个支持多人协作的文档助手。",
    "如果要你做一个活动报名工具，你会从哪里开始？",
    "如果要你做专业赛事解说，如何交付？",
    "要做一项会员自动续费能力，需要哪些步骤？",
    "从头做邮件摘要插件，要考虑哪些问题？",
])
def test_colloquial_construction_is_not_limited_to_a_list_of_object_names(text):
    for query in ("工程设计题", "独立系统设计题"):
        assert assess(text, query=query)["grounded"]


@pytest.mark.parametrize("text", [
    "设计执行器内部的任务缓冲队列。",
    "为多租户应用设计权限与审计机制。",
    "如何实现按预约时段推送通知的功能？",
    "多线程提交相同订单时如何保证只扣款一次？",
    "跨服务处理时，如何协调任务状态？",
])
def test_current_component_or_workflow_delivery_does_not_need_two_invented_constraints(text):
    result = assess(text, "COMPONENT_OR_WORKFLOW")
    assert result["grounded"]
    assert result["scope_policy"] == "ENGINEERING_DELIVERY"
    assert result["host_reason"] == "GROUNDED_COMPONENT_OR_WORKFLOW"


@pytest.mark.parametrize("text", [
    "讲讲做过的任务系统项目。", "介绍项目的设计。", "解释系统设计原理。",
    "有没有做幂等？", "怎么实现？", "大量请求。", "线程池为什么有队列？",
    "Redis的设计原理如何实现？", "介绍现有调度器的内部实现原理。",
])
def test_history_principles_existence_and_bare_actions_do_not_prove_delivery(text):
    result = assess(text, "COMPONENT_OR_WORKFLOW")
    assert not result["grounded"]
    assert not result["invalid_evidence_fields"]


def test_parent_bound_new_delivery_is_allowed_only_when_user_scope_allows_components():
    text = "设计一个执行器的任务缓冲队列。"
    assert assess(text)["grounded"]
    assert assess(text, query="只要独立系统设计题")["host_reason"] == "PARENT_BOUND_INTERNAL_COMPONENT"
    assert assess(text, query=None)["host_reason"] == "PARENT_BOUND_INTERNAL_COMPONENT"


@pytest.mark.parametrize("unit", ["NEW_OR_REBUILT_SYSTEM", "COMPONENT_OR_WORKFLOW"])
def test_an_unverified_neighbor_or_invented_construction_quote_is_not_recovered(unit):
    text = "当前参数是多少？"
    candidate = {"source_context": [
        {"source_context_status": "VERIFIED", "original_question": text, "context_before": "设计一个任务服务。"},
        {"source_context_status": "UNAVAILABLE", "original_question": "设计一个任务服务。"},
    ]}
    result = assess(text, unit, goal="设计一个任务服务。", candidate=candidate)
    assert not result["grounded"]
    assert result["invalid_evidence_fields"] == ["construction_evidence"]


def test_coupled_model_proof_with_missing_constraints_is_not_auto_promoted_to_construction():
    result = assess("设计支持插件扩展的任务框架。", "COUPLED_MECHANISM")
    assert not result["grounded"]
    assert result["invalid_evidence_fields"] == ["constraint_a", "constraint_b"]


def test_tool_choice_ai_coding_does_not_turn_an_architecture_request_into_a_coding_task():
    text = "如果让你用 AI coding 重做这项业务，你会如何设计执行框架？"
    assert assess(text, goal="如何设计执行框架？")["grounded"]
    assert assess("AI coding题：手写一个任务队列。", "COMPONENT_OR_WORKFLOW")["host_reason"] == "EXPLICIT_CODING_TASK"
    assert assess("编程题：实现一个审批系统。", query="独立系统设计题")["host_reason"] == "EXPLICIT_CODING_TASK"
    assert assess("编程题：实现一个审批系统。", query="工程场景设计题")["grounded"]


def test_component_proof_uses_existing_candidate_local_evidence_ids():
    text = "设计工作流的回滚机制。"
    payload = design_candidate_payload([{"canonical_text": text}])[0]
    proof = DesignVerification(candidate_id="c0", keep=True, reason="fixture", design_unit="COMPONENT_OR_WORKFLOW",
        construction_evidence="q0", constraint_a="", constraint_b="")
    resolved = resolve_design_evidence(proof, payload)
    assert resolved.construction_evidence == text
    assert design_proof_assessment({"canonical_text": text}, resolved, query="工作流设计题")["grounded"]
    schema = {"$defs": {"DesignVerification": DesignVerification.model_json_schema()}}
    design_evidence_schema(schema, [payload])
    assert schema["$defs"]["DesignVerification"]["properties"]["construction_evidence"]["enum"] == ["", "q0"]


@pytest.mark.parametrize("text", [
    "工作流的回滚机制，怎么设计？",
    "希望系统为会员筛选优惠、自动结算，怎么实现？",
    "需要保证任务失败后可恢复，如何实现？",
])
def test_split_target_and_action_can_use_an_exact_same_question_range(text):
    candidate = {"canonical_text": text}
    payload = design_candidate_payload([candidate])[0]
    assert payload["construction_ranges"]
    range_id = next(key for key, parts in payload["construction_ranges"].items()
        if "".join(payload["task_evidence_options"][part]["text"] for part in parts) == text)
    proof = DesignVerification(candidate_id="c0", keep=True, reason="fixture", design_unit="COMPONENT_OR_WORKFLOW",
        construction_evidence=range_id, constraint_a="", constraint_b="")
    resolved = resolve_design_evidence(proof, payload)
    assert resolved.construction_evidence == text
    assert design_proof_assessment(candidate, resolved, query="工程设计题")["grounded"]


@pytest.mark.parametrize("selected", ["q0:s0o0", "s0o0:s1o0", "s0b0:s0b1", "q0:q99"])
def test_ranges_cannot_join_other_questions_neighbor_context_or_unknown_parts(selected):
    candidate = {"canonical_text": "工作流的回滚机制。", "source_context": [
        {"source_context_status": "VERIFIED", "original_question": "怎么设计？", "context_before": "需要构建别的系统。"},
        {"source_context_status": "VERIFIED", "original_question": "另一个问题需要设计。"},
    ]}
    payload = design_candidate_payload([candidate])[0]
    proof = DesignVerification(candidate_id="c0", keep=True, reason="fixture", design_unit="COMPONENT_OR_WORKFLOW",
        construction_evidence=selected, constraint_a="", constraint_b="")
    resolved = resolve_design_evidence(proof, payload)
    assert resolved.construction_evidence == ""
    result = design_proof_assessment(candidate, resolved, query="工程设计题")
    assert result["invalid_evidence_fields"] == ["construction_evidence"]


def test_a_verified_original_range_retains_occurrence_local_identity():
    text = "回滚机制，怎么设计？"
    candidate = {"canonical_text": "解释回滚。", "source_context": [
        {"source_context_status": "VERIFIED", "original_question": text, "occurrence_id": "source-a"},
        {"source_context_status": "VERIFIED", "original_question": "另一机制，怎么设计？", "occurrence_id": "source-b"},
    ]}
    payload = design_candidate_payload([candidate])[0]
    assert payload["construction_ranges"]["s0o0:s0o1"] == ["s0o0", "s0o1"]
    proof = DesignVerification(candidate_id="c0", keep=True, reason="fixture", design_unit="COMPONENT_OR_WORKFLOW",
        construction_evidence="s0o0:s0o1", constraint_a="", constraint_b="")
    resolved = resolve_design_evidence(proof, payload)
    assert resolved.construction_evidence == text
    assert all(payload["task_evidence_options"][key]["source_context_index"] == 0
        for key in payload["construction_ranges"][proof.construction_evidence])


def test_colloquial_infrastructure_request_is_a_construction_task():
    assert assess("需要搭什么基础设施来运行这些任务？")["grounded"]


@pytest.mark.parametrize("text", ["解释原理后设计新的事件交付系统。", "介绍现有实现原理，再设计一个新的任务服务。"])
def test_an_explanation_clause_does_not_erase_an_explicit_new_design_task(text):
    assert assess(text, "COMPONENT_OR_WORKFLOW")["grounded"]


def test_construction_ranges_do_not_cross_another_numbered_question():
    payload = design_candidate_payload([{"canonical_text": "1.付款通知工作流，2.怎么设计？"}])[0]
    assert "q0:q1" not in payload["construction_ranges"]


def test_normalization_cannot_promote_an_explicit_project_report_to_new_delivery():
    result = assess("设计通知系统。", candidate={"source_context": [
        {"source_context_status": "VERIFIED", "original_question": "介绍做过的通知系统项目。"}]})
    assert result["host_reason"] == "EXPLICIT_PROJECT_REPORT"


def test_coupled_goal_and_constraints_cannot_be_combined_across_two_originals():
    result = assess("两条不同原问归并后的题目。", "COUPLED_MECHANISM", goal="如何安排事件的交付？",
        first="结果必须一致", second="内存有限", candidate={"source_context": [
            {"source_context_status": "VERIFIED", "original_question": "如何安排事件的交付？"},
            {"source_context_status": "VERIFIED", "original_question": "结果必须一致，内存有限，如何处理请求？"},
        ]})
    assert not result["grounded"]
    assert result["host_reason"] == "CONSTRAINTS_OVERLAP_OR_DIFFERENT_QUESTIONS"
    assert result["invalid_evidence_fields"] == ["constraint_a", "constraint_b"]


def test_coupled_goal_and_constraints_can_share_one_verified_original():
    result = assess("归并后的简短题名。", "COUPLED_MECHANISM", goal="如何安排事件的交付？",
        first="结果必须一致", second="内存有限", candidate={"source_context": [
            {"source_context_status": "VERIFIED", "original_question": "结果必须一致，内存有限，如何安排事件的交付？"},
        ]})
    assert result["grounded"]


@pytest.mark.parametrize("query,expected", [("工程设计题", True), ("只要独立系统设计题", False)])
def test_reranker_passes_original_query_to_the_component_proof(query, expected):
    from interview_intelligence.search.reranker import LLMReranker
    from test_design_scope_proof import ScopeEvidenceClient

    client = ScopeEvidenceClient(design_unit="COMPONENT_OR_WORKFLOW", construction_evidence="q0")
    rows = LLMReranker(client=client, model="offline fixture").rerank(query, [
        {"canonical_question_id": "fixture", "canonical_text": "设计执行器内部的任务队列。"}])
    assert bool(rows) is expected
    assert rows.audit["candidates"][0]["scope_policy"] == design_scope_policy(query)
    assert len(client.requests) == 2


def test_reranker_transports_range_ids_and_does_not_infer_scope_from_the_model_label():
    from interview_intelligence.search.reranker import LLMReranker
    from test_design_scope_proof import ScopeEvidenceClient

    class WrongTaskLabelClient(ScopeEvidenceClient):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            data = json.loads(response.choices[0].message.content)
            if "query_task" in data:
                data["query_task"] = "OTHER"
            response.choices[0].message.content = json.dumps(data)
            return response

    client = WrongTaskLabelClient(design_unit="COMPONENT_OR_WORKFLOW", construction_evidence="q0:q1")
    text = "通知工作流，怎么设计？"
    rows = LLMReranker(client=client, model="offline fixture").rerank("工程设计题", [
        {"canonical_question_id": "fixture", "canonical_text": text}])
    assert len(rows) == 1
    diagnostic = rows.audit["candidates"][0]
    assert diagnostic["construction_evidence"] == text
    assert diagnostic["design_evidence_references"]["construction_evidence"] == "q0:q1"
    payload = json.loads(client.requests[1]["messages"][-1]["content"])
    assert payload["candidates"][0]["construction_ranges"]["q0:q1"] == ["q0", "q1"]


def test_reranker_does_not_accept_a_concept_explanation_with_a_claimed_component_type():
    from interview_intelligence.search.reranker import LLMReranker
    from test_design_scope_proof import ScopeEvidenceClient

    rows = LLMReranker(client=ScopeEvidenceClient(design_unit="COMPONENT_OR_WORKFLOW", construction_evidence="q0"),
        model="offline fixture").rerank("工程设计题", [
            {"canonical_question_id": "fixture", "canonical_text": "Redis的设计原理如何实现？"}])
    assert not rows
    assert rows.audit["candidates"][0]["scope_host_reason"] == "NO_DELIVERY_TASK_DIRECTIVE"


@pytest.mark.parametrize("unit", ["NEW_OR_REBUILT_SYSTEM", "COMPONENT_OR_WORKFLOW"])
def test_verified_original_cannot_gain_a_construction_task_from_canonical(unit):
    candidate = {"source_context": [
        {"source_context_status": "VERIFIED", "original_question": "通知系统的原理是什么？"}]}
    result = assess("设计一个通知系统。", unit, candidate=candidate)
    assert result["grounded"] is False
    assert result["invalid_evidence_fields"] == ["construction_evidence"]
    payload = design_candidate_payload([{"canonical_text": "设计一个通知系统。", **candidate}])[0]
    assert "q0" not in payload["construction_options"]
    assert "s0o0" in payload["construction_options"]


def test_canonical_coupled_assembly_cannot_join_different_original_tasks():
    result = assess("如何安排事件交付？结果必须一致，内存有限。", "COUPLED_MECHANISM",
        goal="如何安排事件交付？", first="结果必须一致", second="内存有限", candidate={"source_context": [
            {"source_context_status": "VERIFIED", "original_question": "如何安排事件交付？"},
            {"source_context_status": "VERIFIED", "original_question": "结果必须一致，内存有限，如何处理？"},
        ]})
    assert not result["grounded"]
    assert result["host_reason"] == "CONSTRAINTS_OVERLAP_OR_DIFFERENT_QUESTIONS"


def test_canonical_range_needs_one_supporting_original_even_if_parts_are_each_grounded():
    candidate = {"canonical_text": "通知工作流，怎么设计？", "source_context": [
        {"source_context_status": "VERIFIED", "original_question": "通知工作流，原理是什么？"},
        {"source_context_status": "VERIFIED", "original_question": "审批系统，怎么设计？"},
    ]}
    payload = design_candidate_payload([candidate])[0]
    assert {"q0", "q1"} <= set(payload["task_evidence_options"])
    assert "q0" in payload["construction_options"]
    assert "q1" not in payload["construction_options"]  # bare task alone is not a construction goal
    assert "q0:q1" not in payload["construction_ranges"]


@pytest.mark.parametrize("unit", ["NEW_OR_REBUILT_SYSTEM", "COMPONENT_OR_WORKFLOW"])
def test_full_reranker_rejects_canonical_only_task_when_raw_question_is_conceptual(unit):
    from interview_intelligence.search.reranker import LLMReranker
    from test_design_scope_proof import ScopeEvidenceClient

    client = ScopeEvidenceClient(design_unit=unit, construction_evidence="q0")
    rows = LLMReranker(client=client, model="offline fixture").rerank("工程设计题", [{
        "canonical_question_id": "fixture", "canonical_text": "设计一个通知系统。", "source_context": [
            {"source_context_status": "VERIFIED", "original_question": "通知系统的原理是什么？",
             "occurrence_id": "raw-a"}],
    }])
    assert not rows
    assert rows.audit["candidate_verification_status"] != "COMPLETE"
    assert rows.audit["candidates"][0]["invalid_design_evidence_fields"] == ["construction_evidence"]


@pytest.mark.parametrize("canonical,original,reference", [
    ("设计一个通知系统。", "设计一个通知系统。", "q0"),
    ("设计一个通知系统。", "做一个事件通知工具。", "s0o0"),
    ("设计一个通知系统。", "通知工作流，怎么设计？", "s0o0:s0o1"),
])
def test_full_reranker_preserves_same_original_or_original_selected_design(canonical, original, reference):
    from interview_intelligence.search.reranker import LLMReranker
    from test_design_scope_proof import ScopeEvidenceClient

    client = ScopeEvidenceClient(design_unit="COMPONENT_OR_WORKFLOW", construction_evidence=reference)
    rows = LLMReranker(client=client, model="offline fixture").rerank("工程设计题", [{
        "canonical_question_id": "fixture", "canonical_text": canonical, "source_context": [
            {"source_context_status": "VERIFIED", "original_question": original, "occurrence_id": "raw-a"}],
    }])
    assert len(rows) == 1
    assert rows.audit["candidate_verification_status"] == "COMPLETE"
    assert rows.audit["candidates"][0]["construction_evidence"] == original
