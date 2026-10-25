"""Applied-task evidence is source bound; fake clients never call a provider."""
import json
import time
from types import SimpleNamespace

import pytest

from interview_intelligence.providers.budget import CallBudget
from interview_intelligence.providers.runtime import RequestLimits, current_limits
from interview_intelligence.search.reranker import LLMReranker, RerankedCandidates
from interview_intelligence.search.scenario_scope import (
    ScenarioVerification, concept_only_evidence, scenario_candidate_payload,
    scenario_proof_assessment, scenario_scope_required,
)
from interview_intelligence.search.candidate_prompt import ALGORITHM_PROBLEM_SCOPE_INSTRUCTIONS, SOURCE_CONTEXT_INSTRUCTIONS


def candidate(question, *, original=None, before="", extra=()):
    return {"canonical_question_id": "fixture", "canonical_text": question, "source_context": [
        {"source_context_status": "VERIFIED", "original_question": original or question, "original_question_complete": True,
         "context_before": before}, *extra]}


def proof(row, *, situation="s0o0", task="s0o0", kind="SOLUTION", keep=True):
    item = ScenarioVerification(candidate_id="c0", keep=keep, reason="offline fixture",
        scenario_task=kind, situation_evidence=situation, application_evidence=task)
    return scenario_proof_assessment(row, item, scenario_candidate_payload([row])[0])


@pytest.mark.parametrize("question", [
    "说明缓存穿透、击穿、雪崩各有什么区别。", "上述三个概念是什么意思？",
    "缓存服务在高并发情况下采用什么工作原理？", "某队列延迟投递底层原理与内部如何实现？",
    "缓存的设计原理是什么？", "缓存的设计思想是什么？",
    "缓存的设计是什么？",
    "队列内部如何保证顺序性，其底层原理是什么？",
    "介绍消息队列如何保证消息不丢失的机制。",
    "解释队列如何确保顺序性的工作方式。",
    "讲讲缓存怎么避免击穿的内部机制。",
])
def test_concept_and_existing_mechanism_explanations_cannot_claim_applied_design(question):
    row = candidate(question)
    assert concept_only_evidence(row)["status"] == "REJECT"
    assert proof(row, kind="DESIGN")["status"] == "REJECT"


@pytest.mark.parametrize("question", [
    "缓存击穿如何解决？", "如何保证消息不丢失？", "消息积压怎样处理？",
    "如何实现消息可靠投递？", "缓存故障如何诊断和定位？", "设计一个限流器满足突发流量要求。",
    "消息队列有哪些策略保证消息不丢失？",
])
def test_diagnosis_solution_and_construction_are_protected_without_type_labels(question):
    row = candidate(question)
    assert concept_only_evidence(row) is None
    assert proof(row)["status"] == "PASS"


def test_mixed_concept_and_actual_new_design_is_not_rejected():
    row = candidate("先说明缓存原理，再设计一个限流服务。")
    assert concept_only_evidence(row) is None
    assert proof(row, situation="s0o1", task="s0o1", kind="DESIGN")["status"] == "PASS"


@pytest.mark.parametrize("question,index", [
    ("介绍消息不丢失的机制，并设计一个可靠队列。", "s0o1"),
    ("介绍消息不丢失的机制，遇到消息丢失如何排查？", "s0o1"),
    ("介绍消息队列的原理并设计一个可靠队列。", "s0o0"),
])
def test_explanation_frame_does_not_erase_separate_applied_task(question,index):
    row = candidate(question)
    assert concept_only_evidence(row) is None
    assert proof(row,situation=index,task=index)["status"] == "PASS"


def test_current_original_overrides_canonical_rewrite_and_cannot_borrow_neighbor_solution():
    row = candidate("缓存异常如何解决？", original="这几个概念是什么意思？", before="前一题：缓存异常如何解决？")
    assert proof(row, situation="q0", task="q0")["status"] == "REJECT"
    other = candidate("运行环境不详。", before="消息积压如何处理？")
    result = proof(other, situation="s0b0", task="s0b0")
    assert result["status"] == "UNKNOWN" and result["host_reason"] == "SCENARIO_EVIDENCE_NOT_GROUNDED"


def test_claims_cannot_join_two_occurrences_or_forged_ids():
    row = candidate("发生消息积压。", extra=[{"source_context_status": "VERIFIED", "original_question": "如何解决消息延迟？"}])
    assert proof(row, task="s1o0")["host_reason"] == "SCENARIO_CLAIMS_FROM_DIFFERENT_QUESTIONS"
    assert proof(row, task="s9o0")["status"] == "UNKNOWN"


def test_missing_new_fields_in_old_outputs_is_unknown_not_new_positive_or_negative():
    row = candidate("消息积压如何处理？")
    result = scenario_proof_assessment(row, None, scenario_candidate_payload([row])[0])
    assert result["status"] == "UNKNOWN" and result["host_reason"] == "SCENARIO_PROOF_MISSING"
    assert proof(row, kind="CONCEPT", keep=False)["status"] == "UNKNOWN"


@pytest.mark.parametrize("complete", [False, None])
def test_verified_truncated_concept_prefix_cannot_prove_whole_question_negative(complete):
    prefix = "先介绍消息队列如何保证消息不丢失的内部机制。"
    full = prefix + "历史背景和已知条件。" * 25 + "现在遇到消息丢失该如何排查？"
    row = candidate(full, original=prefix)
    row["source_context"][0]["original_question_complete"] = complete
    assert concept_only_evidence(row) is None
    result = proof(row, kind="CONCEPT", keep=False)
    assert result["status"] == "UNKNOWN"
    assert proof(row, kind="DESIGN")["status"] == "UNKNOWN"


def test_truncated_prefix_with_actual_task_can_prove_positive_but_not_pure_concept():
    row = candidate("消息丢失如何排查？")
    row["source_context"][0]["original_question_complete"] = False
    assert proof(row)["status"] == "PASS"


class OfflineClient:
    def __init__(self, *, kind="SOLUTION", task="s0o0", situation="s0o0", legacy=False, primary=False):
        self.requests = []
        self.kind, self.task, self.situation, self.legacy, self.primary = kind, task, situation, legacy, primary
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        body = json.loads(kwargs["messages"][-1]["content"])
        if self.primary:
            response = {"query_task": "OTHER", "query_task_evidence": body["query"], "query_object": "测试对象",
                "query_focus": "测试任务", "query_object_evidence": body["query"], "query_focus_evidence": body["query"],
                "rankings": [{"candidate_id": "c0", "candidate_task": "KNOWLEDGE", "task_evidence": "s0o0",
                    "object_evidence": "q0", "focus_evidence": "q0", "object_scope": "SAME", "focus_scope": "SAME"}]}
        else:
            row = {"candidate_id": "c0", "keep": True, "reason": "offline fixture"}
            if not self.legacy:
                row.update(scenario_task=self.kind, situation_evidence=self.situation, application_evidence=self.task)
            response = {"verifications": [row]}
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(response)))])


def test_other_task_cannot_bypass_original_scenario_and_knowledge_is_not_banned():
    client = OfflineClient(primary=True)
    reranker = LLMReranker(client=client, model="offline")
    bad = reranker._rank_candidates("缓存场景题", [candidate("缓存的设计原理是什么？")])
    assert not bad and bad.audit["candidates"][0]["decision"] == "SCENARIO_REJECTED"
    good = reranker._rank_candidates("缓存场景题", [candidate("缓存击穿如何解决？")])
    assert len(good) == 1 and good.audit["candidates"][0]["candidate_task"] == "KNOWLEDGE"
    client = OfflineClient()
    result = LLMReranker(client=client, model="offline")._verify_scope("缓存场景题", good)
    assert len(result) == 1 and result.audit["scope_verification_status"] == "COMPLETE"
    assert "ScenarioVerification" in client.requests[0]["response_format"]["json_schema"]["schema"]["$defs"]


@pytest.mark.parametrize("legacy,task", [(True, "s0o0"), (False, "missing")])
def test_legacy_or_invalid_scope_proof_excludes_row_and_marks_partial(legacy, task):
    rows = RerankedCandidates([candidate("消息积压如何处理？")], {"query_task": "OTHER"})
    client = OfflineClient(legacy=legacy, task=task)
    reranker = LLMReranker(client=client, model="offline")
    result = reranker._verify_scope("消息队列场景题", rows)
    assert not result and result.audit["scope_verification_status"] == "PARTIAL"
    assert reranker.repair_scope_evidence("消息队列场景题", rows, result) is result
    assert len(client.requests) == 1


def test_original_query_selects_only_applicable_rules_and_preserves_sources():
    rows = RerankedCandidates([candidate("消息积压如何处理？")], {"query_task": "OTHER"})
    reranker = LLMReranker(client=OfflineClient(), model="offline")
    scenario = reranker._scope_request("消息队列场景题", rows)
    assert SOURCE_CONTEXT_INSTRUCTIONS in scenario["messages"][0]["content"]
    assert ALGORITHM_PROBLEM_SCOPE_INSTRUCTIONS not in scenario["messages"][0]["content"]
    assert "DesignVerification" not in scenario["response_format"]["json_schema"]["schema"]["$defs"]
    assert "DesignVerification" in reranker._scope_request("工程场景设计题", rows)["response_format"]["json_schema"]["schema"]["$defs"]
    assert ALGORITHM_PROBLEM_SCOPE_INSTRUCTIONS in reranker._scope_request("算法题", rows)["messages"][0]["content"]
    assert not scenario_scope_required("介绍应用场景")
    assert not scenario_scope_required("不要场景题，只要原理题")
    assert not scenario_scope_required("缓存场景题和原理题都要")


def test_primary_complete_request_bound_is_used_and_unknown_usage_charged():
    client = OfflineClient(primary=True)
    reranker = LLMReranker(client=client, model="offline")
    rows = [candidate("缓存击穿如何解决？")]
    prepared = reranker._primary_request("缓存场景题", rows)
    expected = len(json.dumps({key: prepared[key] for key in ("messages", "response_format")},
                             ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert prepared["input_token_upper_bound"] == expected
    limits = RequestLimits(deadline=time.monotonic() + 30, max_tokens=96000)
    token = current_limits.set(limits)
    try:
        reranker._rank_candidates("缓存场景题", rows)
    finally:
        current_limits.reset(token)
    assert limits.tokens == prepared["token_upper_bound"] == expected + reranker.max_output_tokens
    assert client.requests[0]["messages"] == prepared["messages"]
    assert client.requests[0]["response_format"] == prepared["response_format"]


def test_primary_system_and_schema_budget_rejects_before_provider():
    client = OfflineClient(primary=True)
    reranker = LLMReranker(client=client, model="offline", budget=CallBudget(max_calls=6, max_tokens=2000))
    with pytest.raises(ValueError, match="MODEL_TOKEN_BUDGET_EXCEEDED"):
        reranker._rank_candidates("缓存场景题", [candidate("缓存击穿如何解决？")])
    assert not client.requests and reranker.budget.used_calls == 0
