import pytest

from interview_intelligence.search.design_scope import DesignVerification, design_unit_grounded
from interview_intelligence.search.reranker import LLMReranker
from test_relevance_retry import Client


def proof(unit, goal, first="", second=""):
    return DesignVerification(candidate_id="c0", keep=True, reason="fixture", design_unit=unit,
        construction_evidence=goal, constraint_a=first, constraint_b=second)


@pytest.mark.parametrize("text", ["如何设计分布式 ID？", "如果要实现一个监控埋点系统，如何处理大量日志？",
                                "如何将服务改造成分布式系统？", "Design an event delivery service."])
def test_new_or_rebuilt_goal_requires_a_current_construction_directive(text):
    assert design_unit_grounded({"canonical_text": text}, proof("NEW_OR_REBUILT_SYSTEM", text))


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
    rows = LLMReranker(client=Client(), model="fixture").rerank("业务系统设计题", [candidate])
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
