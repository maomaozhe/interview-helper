import json
import pytest

from interview_intelligence.search.design_scope import (
    DesignVerification, design_unit_grounded, design_candidate_payload, GIVEN_CONSTRAINT,
)
from interview_intelligence.search.reranker import LLMReranker
from test_relevance_retry import Client


def proof(unit, goal, first="", second=""):
    return DesignVerification(candidate_id="c0", keep=True, reason="fixture", design_unit=unit,
        construction_evidence=goal, constraint_a=first, constraint_b=second)


@pytest.mark.parametrize("text", ["如何设计分布式 ID？", "如果要实现一个监控埋点系统，如何处理大量日志？",
                                "如何将服务改造成分布式系统？", "Design an event delivery service."])
def test_new_or_rebuilt_goal_requires_a_current_construction_directive(text):
    assert design_unit_grounded({"canonical_text": text}, proof("NEW_OR_REBUILT_SYSTEM", text))


@pytest.mark.parametrize("text", [
    "如果要做example.test这个网页，请说明从头到尾的实现流程。",
    "做一个招聘网站，需要考虑哪些模块？",
    "从头做一个订单系统，会怎么划分服务？",
    "从头开始做消息推送服务，需要考虑哪些问题？",
    "要做一个关注功能，支持关注和取消关注。",
])
def test_explicit_new_delivery_accepts_colloquial_build_directives(text):
    assert design_unit_grounded({"canonical_text": text}, proof("NEW_OR_REBUILT_SYSTEM", text))


@pytest.mark.parametrize("text", [
    "介绍做过的网站项目和遇到的问题。",
    "你做过example.test这个网页吗？讲讲已有实现。",
    "这个服务如何做到高性能？",
    "这个系统需要做到高性能，如何优化？",
])
def test_project_history_and_performance_wording_are_not_new_delivery_directives(text):
    assert not design_unit_grounded({"canonical_text": text}, proof("NEW_OR_REBUILT_SYSTEM", text))


@pytest.mark.parametrize("text", ["千万连接下服务怎么拆？", "消息重复上报怎么去重？", "响应时间变大如何优化？"])
def test_high_similarity_does_not_turn_a_local_operation_into_a_new_design_goal(text):
    assert not design_unit_grounded({"canonical_text": text}, proof("NEW_OR_REBUILT_SYSTEM", text))


def test_coupled_mechanism_accepts_two_grounded_requirements_without_a_design_keyword():
    text = "资源不变化时结果一致，机器内存有限，如何兼顾并发与延迟？"
    assert design_unit_grounded({"canonical_text": text}, proof("COUPLED_MECHANISM",
        "如何兼顾并发与延迟？", "资源不变化时结果一致", "机器内存有限"))


@pytest.mark.parametrize("first,second", [("机器内存有限", "机器内存有限"),
    ("机器内存有限", "内存有限"), ("机器内存有限", "不存在的条件")])
def test_same_overlapping_or_invented_requirements_do_not_prove_a_closed_mechanism(first, second):
    text = "机器内存有限，如何兼顾并发与延迟？"
    assert not design_unit_grounded({"canonical_text": text}, proof("COUPLED_MECHANISM", text, first, second))


def test_construction_goal_cannot_be_borrowed_from_neighbor_or_unverified_original():
    candidate = {"canonical_text": "并发数多少？", "source_context": [
        {"source_context_status": "VERIFIED", "original_question": "并发数多少？", "context_before": "设计一个服务。"},
        {"source_context_status": "UNAVAILABLE", "original_question": "设计一个服务。"}]}
    assert not design_unit_grounded(candidate, proof("NEW_OR_REBUILT_SYSTEM", "设计一个服务。"))


def test_verified_current_original_can_supply_the_construction_goal():
    candidate = {"canonical_text": "如何保证吞吐？", "source_context": [
        {"source_context_status": "VERIFIED", "original_question": "重新设计消息服务以满足吞吐需求。"}]}
    assert design_unit_grounded(candidate, proof("NEW_OR_REBUILT_SYSTEM", "重新设计消息服务"))


def test_verifier_keep_true_cannot_bypass_the_host_design_goal_check():
    candidate = {"canonical_question_id": "local", "canonical_text": "千万连接下服务怎么拆？"}
    rows = LLMReranker(client=Client(), model="fixture").rerank("独立系统设计题", [candidate])
    assert rows == []
    diagnostic = rows.audit["candidates"][0]
    assert diagnostic["model_scope_verification_keep"] is True
    assert diagnostic["design_unit_grounded"] is False
    assert diagnostic["decision"] == "SCOPE_REJECTED"


def test_internal_component_is_not_upgraded_by_a_design_directive():
    text = "设计执行器内部的任务缓冲队列。"
    assert not design_unit_grounded({"canonical_text": text}, proof("INTERNAL_COMPONENT", text))


def test_claiming_new_system_cannot_promote_a_parent_bound_internal_container():
    text = "设计一个执行器的任务缓冲队列，你会怎么设计？"
    assert not design_unit_grounded({"canonical_text": text}, proof("NEW_OR_REBUILT_SYSTEM", text))


def test_component_and_operation_lists_are_not_given_mechanism_constraints():
    text = "抢购如何保证性能？分布式锁、异步解耦、任务队列、防刷分别怎么做？"
    assert not design_unit_grounded({"canonical_text": text}, proof("COUPLED_MECHANISM",
        "抢购如何保证性能？", "分布式锁、异步解耦、任务队列", "防刷分别怎么做？"))


def test_service_with_a_support_modifier_remains_an_independent_delivery():
    text = "设计一个支持支付服务的订单系统。"
    assert design_unit_grounded({"canonical_text": text}, proof("NEW_OR_REBUILT_SYSTEM", text))


@pytest.mark.parametrize("text,first,second", [
    ("排名需要实时更新，如何保证高并发？", "排名需要实时更新", "如何保证高并发？"),
    ("传输中断后，后续要怎么恢复？如果存储数据过大怎么办？", "传输中断后，后续要怎么恢复？", "如果存储数据过大怎么办？")])
def test_observable_behavior_and_recovery_conditions_are_given_constraints(text, first, second):
    assert design_unit_grounded({"canonical_text": text}, proof("COUPLED_MECHANISM", text, first, second))


def test_normalized_system_wording_cannot_erase_a_verified_coding_task():
    candidate = {"canonical_text": "设计一个数据服务。", "source_context": [
        {"source_context_status": "VERIFIED", "original_question": "代码题：设计一个数据服务。"}]}
    assert not design_unit_grounded(candidate, proof("NEW_OR_REBUILT_SYSTEM", "设计一个数据服务。"))


def test_neighbor_coding_section_does_not_reclassify_the_current_design():
    candidate = {"canonical_text": "设计一个数据服务。", "source_context": [
        {"source_context_status": "VERIFIED", "original_question": "设计一个数据服务。", "context_before": "代码题：数组排序。"}]}
    assert design_unit_grounded(candidate, proof("NEW_OR_REBUILT_SYSTEM", "设计一个数据服务。"))


def test_a_verified_design_original_is_not_erased_by_another_coding_occurrence():
    candidate = {"canonical_text": "设计一个数据服务。", "source_context": [
        {"source_context_status": "VERIFIED", "original_question": "编程题：设计一个数据服务。"},
        {"source_context_status": "VERIFIED", "original_question": "设计一个数据服务。"}]}
    assert design_unit_grounded(candidate, proof("NEW_OR_REBUILT_SYSTEM", "设计一个数据服务。"))


class ScopeEvidenceClient(Client):
    def __init__(self, **verification):
        super().__init__()
        self.verification = verification

    def create(self, **kwargs):
        response = super().create(**kwargs)
        result = json.loads(response.choices[0].message.content)
        if "verifications" in result:
            for row in result["verifications"]:
                row.update(self.verification)
            response.choices[0].message.content = json.dumps(result)
        return response


@pytest.mark.parametrize("text,goal,first,second", [
    ("事件列表需要实时刷新，如何保证高吞吐？数据结构如何组织？存储如何选型？", "q0", "q0", "q1"),
    ("任务结果在资源未变化时一致，机器内存有限，如何兼顾并发和响应时间？", "q2", "q0", "q1"),
])
def test_scope_selects_two_nonoverlapping_given_requirements_without_copying_or_adding_keywords(text, goal, first, second):
    client = ScopeEvidenceClient(design_unit="COUPLED_MECHANISM", construction_evidence=goal,
                                 constraint_a=first, constraint_b=second)
    rows = LLMReranker(client=client, model="fixture").rerank("业务系统设计题", [
        {"canonical_question_id": "anonymous", "canonical_text": text}])
    assert len(rows) == 1 and rows.audit["invalid_candidate_count"] == 0
    diagnostic = rows.audit["candidates"][0]
    assert diagnostic["scope_host_reason"] == "GROUNDED_COUPLED_REQUIREMENTS"
    assert diagnostic["design_evidence_references"]["constraint_a"] == first
    request = json.loads(client.requests[1]["messages"][-1]["content"])
    candidate = request["candidates"][0]
    assert all(GIVEN_CONSTRAINT.search(candidate["question"][key]) for key in candidate["constraint_options"])
    assert "q2" not in candidate["constraint_options"]
    schema = client.requests[1]["response_format"]["json_schema"]["schema"]["$defs"]["DesignVerification"]
    assert schema["properties"]["constraint_a"]["enum"] == ["", "q0", "q1"]
    assert "canonical_evidence_options" not in candidate and "task_evidence_options" not in candidate


@pytest.mark.parametrize("field,value", [
    ("construction_evidence", "从题目不存在的目标构建系统"),
    ("construction_evidence", "s0b0"),
    ("constraint_a", "任务结果在资源没变化时一致"),
    ("constraint_a", "q9"),
    ("constraint_a", "s1o0"),
    ("constraint_b", ""),
])
def test_missing_rewritten_or_out_of_scope_design_quotes_are_partial_evidence_failures(field, value):
    text = "任务结果在资源未变化时一致，机器内存有限，如何兼顾并发和响应时间？"
    verification = dict(design_unit="COUPLED_MECHANISM", construction_evidence="q2", constraint_a="q0", constraint_b="q1")
    verification[field] = value
    client = ScopeEvidenceClient(**verification)
    rows = LLMReranker(client=client, model="fixture").rerank("业务系统设计题", [
        {"canonical_question_id": "anonymous", "canonical_text": text, "source_context": [
            {"source_context_status": "VERIFIED", "original_question": text, "context_before": "构建另外一个系统。"},
            {"source_context_status": "UNAVAILABLE", "original_question": "机器内存有限"}]}])
    assert rows == [] and rows.audit["candidate_verification_status"] == "PARTIAL"
    assert rows.audit["invalid_candidate_count"] == rows.audit["invalid_evidence_count"] == 1
    assert rows.audit["scope_invalid_evidence_count"] == 1 and rows.audit["invalid_schema_count"] == 0
    diagnostic = rows.audit["candidates"][0]
    assert diagnostic["decision"] == diagnostic["scope_verification_status"] == "INVALID_DESIGN_EVIDENCE"
    assert diagnostic["model_scope_verification_keep"] is True and diagnostic["scope_verification_keep"] is False
    assert field in diagnostic["invalid_design_evidence_fields"]


@pytest.mark.parametrize("first,second,reason", [
    ("事件列表需要实时刷新", "数据结构如何组织？存储如何选型？", "CONSTRAINT_NOT_A_GIVEN_REQUIREMENT"),
])
def test_grounded_but_ineligible_or_overlapping_constraints_remain_semantic_scope_rejections(first, second, reason):
    text = "事件列表需要实时刷新，如何保证高吞吐？数据结构如何组织？存储如何选型？"
    rows = LLMReranker(client=ScopeEvidenceClient(design_unit="COUPLED_MECHANISM", construction_evidence="q0",
        constraint_a=first, constraint_b=second), model="fixture").rerank("业务系统设计题", [
            {"canonical_question_id": "anonymous", "canonical_text": text}])
    assert rows == [] and rows.audit["candidate_verification_status"] == "COMPLETE"
    assert rows.audit["invalid_candidate_count"] == 0
    assert rows.audit["candidates"][0]["decision"] == "SCOPE_REJECTED"
    assert rows.audit["candidates"][0]["scope_host_reason"] == reason


@pytest.mark.parametrize("first,second,original,reason,invalid_fields", [
    ("q0", "q0", "记录需要实时更新，机器内存有限。",
     "CONSTRAINTS_OVERLAP_OR_DIFFERENT_QUESTIONS", ["constraint_a", "constraint_b"]),
    ("记录需要实时更新", "需要实时更新", "记录需要实时更新，机器内存有限。",
     "CONSTRAINTS_OVERLAP_OR_DIFFERENT_QUESTIONS", ["constraint_a", "constraint_b"]),
    ("q0", "s0o0", "机器内存有限，如何安排处理？",
     "CONSTRUCTION_EVIDENCE_NOT_GROUNDED", ["construction_evidence"]),
])
def test_identical_contained_or_cross_text_constraint_choices_are_evidence_failures(first, second, original, reason, invalid_fields):
    text = "记录需要实时更新，如何安排处理？"
    rows = LLMReranker(client=ScopeEvidenceClient(design_unit="COUPLED_MECHANISM", construction_evidence="q0",
        constraint_a=first, constraint_b=second), model="fixture").rerank("业务系统设计题", [
            {"canonical_question_id": "anonymous", "canonical_text": text, "source_context": [
                {"source_context_status": "VERIFIED", "original_question": original}]}])
    assert rows == [] and rows.audit["candidate_verification_status"] == "PARTIAL"
    assert rows.audit["invalid_evidence_count"] == rows.audit["invalid_candidate_count"] == 1
    row = rows.audit["candidates"][0]
    assert row["decision"] == "INVALID_DESIGN_EVIDENCE"
    assert row["scope_host_reason"] == reason
    assert row["invalid_design_evidence_fields"] == invalid_fields


def test_explicit_scope_rejection_does_not_require_positive_proof_or_count_as_partial():
    rows = LLMReranker(client=ScopeEvidenceClient(keep=False, design_unit="UNKNOWN", construction_evidence="",
        constraint_a="", constraint_b=""), model="fixture").rerank("业务系统设计题", [
            {"canonical_question_id": "anonymous", "canonical_text": "介绍已有业务系统。"}])
    assert rows == [] and rows.audit["invalid_candidate_count"] == 0
    assert rows.audit["candidates"][0]["decision"] == "SCOPE_REJECTED"


def test_constraint_choices_exclude_neighbors_and_unverified_originals():
    item = design_candidate_payload([{"canonical_text": "当前目标如何处理？", "source_context": [
        {"source_context_status": "VERIFIED", "original_question": "内存有限，要求并发。", "context_before": "其他服务需要实时更新。"},
        {"source_context_status": "UNAVAILABLE", "original_question": "必须高可用。"}]}])[0]
    assert set(item["constraint_options"]) == {"s0o0", "s0o1"}
    assert not any(key.startswith(("s0b", "s1")) for key in item["construction_options"])


def test_primary_prompt_distinguishes_business_implementation_and_wide_mechanism_instances():
    client = ScopeEvidenceClient()
    LLMReranker(client=client, model="fixture").rerank("业务系统设计题", [
        {"canonical_question_id": "anonymous", "canonical_text": "实现一个在线预约功能。"}])
    prompt = client.requests[0]["messages"][0]["content"]
    assert "不能仅凭‘实现’二字判CODE_TASK" in prompt
    assert "约束闭环机制，本身就是该宽类别的具体INSTANCE" in prompt
