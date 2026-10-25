"""Source activity roles and blind independent reassessment, without private IDs."""
import json
import hashlib

import pytest

from test_business_scope_verification import Provider, QUERY, candidate, scope
from interview_intelligence.search.business_scope import (
    BUSINESS_SCOPE_CONTRACT, CLARIFIED_ADJUDICATION_CONTRACT, CompactScopeProof,
)
from interview_intelligence.search.reranker import LLMReranker


def run(provider, source):
    return LLMReranker(client=provider, model="fixture").rerank(QUERY, [source], relevance_scope=scope())


@pytest.mark.parametrize("unit,text", [
    ("BUSINESS_WORKFLOW", "设计一个公共空间预约功能，居民预约场地，要求低延迟。"),
    ("AI_APPLICATION", "设计一个AI材料检查助手，申请人提交材料并收到缺项反馈，要求高吞吐。"),
])
def test_positive_activity_and_technical_constraint_are_separate_source_roles(unit, text):
    provider = Provider({text.split("，")[0]: unit},
        primary_overrides={"business_goal_evidence": "s0o2", "business_activity_evidence": "s0o1"},
        scope_overrides={"business_goal_evidence": "s0o2", "business_activity_evidence": "s0o1"})
    result = run(provider, candidate("activity", text))
    assert result[0]["relevance_scope_status"] == "CONFIRMED"
    audit = result.audit["candidates"][0]
    assert audit["business_activity_evidence"] == text.split("，")[1] + "，"
    assert audit["business_goal_evidence"] == "要求" + ("低延迟。" if unit == "BUSINESS_WORKFLOW" else "高吞吐。")
    assert audit["business_scope_contract"] == BUSINESS_SCOPE_CONTRACT
    assert audit["scope_adjudication_contract"] == CLARIFIED_ADJUDICATION_CONTRACT
    assert audit["business_evidence_references"]["business_activity_evidence"] == "s0o1"


@pytest.mark.parametrize("activity", ["", "s9o0", "原问没有的业务活动", "s1o1"])
def test_absent_forged_or_other_occurrence_activity_never_confirms_a_business_claim(activity):
    text = "设计一个公共空间预约功能，居民预约场地，要求低延迟。"
    source = candidate("no-activity", text)
    source["source_context"].append({"source_context_status": "VERIFIED", "original_question_complete": True,
        "original_question": "设计一个通用缓冲组件，支持并发分配与回收。"})
    provider = Provider({"公共空间预约功能": "BUSINESS_WORKFLOW"}, scope_overrides={
        "business_goal_evidence": "s0o2", "business_activity_evidence": activity})
    result = run(provider, source)
    assert result[0]["relevance_scope_status"] == "PENDING"
    assert result.audit["candidates"][0]["business_assessment"]["outcome"] == "INSUFFICIENT"
    assert len(provider.requests) == 3


def test_single_complete_proposition_can_fill_object_goal_and_activity_without_duplicate_quote_rule():
    text = "设计一个居民取件功能。"
    provider = Provider({"居民取件": "BUSINESS_WORKFLOW"}, primary_overrides={
        "business_goal_evidence": "s0o0", "business_activity_evidence": "s0o0"}, scope_overrides={
        "business_goal_evidence": "s0o0", "business_activity_evidence": "s0o0"})
    result = run(provider, candidate("one-source-proposition", text))
    assert result[0]["relevance_scope_status"] == "CONFIRMED"


def test_activity_wire_field_is_required_and_old_wire_is_not_silently_completed():
    raw = {"u": "B", "t": "B", "e": "s0o0", "g": "s0o1", "r": "K", "d": "", "x": ""}
    with pytest.raises(ValueError):
        CompactScopeProof.model_validate(raw)
    assert CompactScopeProof.model_validate({**raw, "p": "s0o1"}).standard()["business_activity_evidence"] == "s0o1"


def test_technical_unit_cannot_also_claim_an_actual_asked_business_activity():
    provider = Provider({"通用缓冲组件": "FOUNDATION_COMPONENT"}, scope_overrides={
        "business_activity_evidence": "s0o1"})
    result = run(provider, candidate("conflicting-roles", "设计一个通用缓冲组件，支持并发投递。"))
    assert result[0]["relevance_scope_status"] == "PENDING"
    assert result.audit["candidates"][0]["scope_host_reason"] == "BUSINESS_ACTIVITY_CONTRADICTS_UNIT"


@pytest.mark.parametrize("primary", [
    {"candidate_task": "LOCAL_DETAIL"},
    {"semantic_unit": "FOUNDATION_COMPONENT", "delivery_role": "TECHNICAL_CAPABILITY",
     "technical_goal_evidence": "s0o2", "rejection_basis": "TARGET_UNIT"},
])
def test_initial_independent_scope_is_blind_to_provisional_primary_semantics(primary):
    provider = Provider({"公共空间预约功能": "BUSINESS_WORKFLOW"}, primary_overrides=primary,
        scope_overrides={"scope_query_relation": {"o": "INSTANCE", "f": "SAME", "e": "s0o0", "h": "s0o1", "g": 3}})
    result = run(provider, candidate("blind", "设计一个公共空间预约功能，居民预约场地，要求低延迟。"))
    assert result[0]["relevance_scope_status"] == "CONFIRMED" and len(provider.requests) == 2
    body = json.loads(provider.requests[1]["messages"][1]["content"])
    assert "repair_feedback" not in body and "primary_diagnostics" not in body
    assert result.audit["candidates"][0]["primary_diagnostic"]["candidate_task"] == primary.get("candidate_task", "DESIGN_TASK")


def test_repair_conflict_rechecks_roles_jointly_without_claiming_new_source_material():
    provider = Provider({"公共空间预约功能": "BUSINESS_WORKFLOW"}, scope_overrides={"scope_candidate_task": "L"})
    result = run(provider, candidate("joint-conflict", "设计一个公共空间预约功能，居民预约场地。"))
    assert result[0]["relevance_scope_status"] == "PENDING" and len(provider.requests) == 3
    feedback = json.loads(provider.requests[2]["messages"][1]["content"])["repair_feedback"]
    assert feedback["version"] == "host_scope_feedback_v2"
    row = feedback["candidates"][0]
    assert {"scope_candidate_task", "semantic_unit", "delivery_role", "business_activity_evidence",
            "artifact_goal_relation"} <= set(row["failed_fields"])
    assert row["source_material"] == "UNCHANGED_HOST_SOURCES"
    assert row["prior"]["scope"]["business_activity_evidence"] == "s0o1"


def test_scoped_primary_uses_dedicated_task_and_broad_class_contract():
    provider = Provider({"公共空间预约功能": "BUSINESS_WORKFLOW"})
    run(provider, candidate("specific-contract", "设计一个公共空间预约功能，居民预约场地。"))
    primary = provider.requests[0]["messages"][0]["content"]
    scope_prompt = provider.requests[1]["messages"][0]["content"]
    assert "具体应用、功能或业务流程是INSTANCE" in primary
    assert "技术对象也可要求设计" in primary
    assert "p仅供B/W/A正类" in primary and "技术指标本身不证明业务活动" in primary
    assert "首次独立复核每题都重审q" in scope_prompt
    assert "当前开放设计或实现方案" in scope_prompt


def test_prompt_identity_hashes_exactly_the_system_messages_used_for_scoped_requests():
    ranker = LLMReranker(client=object(), model="no-provider")
    rows = [candidate("identity", "设计一个公共空间预约功能，居民预约场地。")]
    primary = ranker._primary_request(QUERY, rows, relevance_scope=scope())
    secondary = ranker._scope_request(QUERY, rows, relevance_scope=scope())
    identity = ranker.clarified_prompt_identity()
    assert identity == {
        "reranker_prompt_hash": hashlib.sha256(primary["messages"][0]["content"].encode()).hexdigest(),
        "scope_verifier_prompt_hash": hashlib.sha256(secondary["messages"][0]["content"].encode()).hexdigest(),
    }
    assert identity["reranker_prompt_hash"] != hashlib.sha256(ranker.system_prompt.encode()).hexdigest()
    assert identity["scope_verifier_prompt_hash"] != hashlib.sha256(ranker.scope_verifier_prompt.encode()).hexdigest()
