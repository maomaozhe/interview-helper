"""One bounded repair of positive design evidence; semantic decisions stay fenced."""
import json
import time
from types import SimpleNamespace

import httpx
from openai import APIError
import pytest

from interview_intelligence.providers.budget import CallBudget
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.providers.runtime import RequestLimits, current_limits
from interview_intelligence.search.reranker import LLMReranker, RerankedCandidates


def frozen_scope():
    primary = RerankedCandidates([
        {"canonical_question_id": "positive", "canonical_text": "记录需要实时更新，如何保证高并发？", "rerank_rank": 3},
        {"canonical_question_id": "negative", "canonical_text": "请介绍自己已有的项目。", "rerank_rank": 8},
    ], {"query_task": "INDEPENDENT_SYSTEM_DESIGN"})
    rows = [
        {"canonical_question_id": "positive", "scope_verification_rank": 2,
         "scope_verification_status": "INVALID_DESIGN_EVIDENCE", "scope_verification_keep": False,
         "model_scope_verification_keep": True, "decision": "INVALID_DESIGN_EVIDENCE",
         "retrieval_provenance": {"stage_ranks": {"broad_rrf": 3}},
         "invalid_design_evidence_fields": ["constraint_b"]},
        {"canonical_question_id": "negative", "scope_verification_rank": 4,
         "scope_verification_status": "SCOPE_REJECTED", "scope_verification_keep": False,
         "model_scope_verification_keep": False, "decision": "SCOPE_REJECTED"},
    ]
    verified = RerankedCandidates([], {"scope_verifier_version": LLMReranker.scope_verifier_version,
        "scope_verification_status": "PARTIAL", "invalid_candidate_count": 1,
        "invalid_evidence_count": 1, "invalid_schema_count": 0, "candidate_count": 50, "candidates": rows})
    return primary, verified


class RepairClient:
    def __init__(self, *, bad=False, failure=False, keep=True, overlap=False):
        self.bad, self.failure, self.keep, self.overlap = bad, failure, keep, overlap
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.failure:
            raise APIError("fixture disconnect", request=httpx.Request("POST", "https://fixture.test"), body=None)
        contract = kwargs["response_format"]["json_schema"]
        assert contract["strict"] is True and "DesignVerification" in contract["schema"]["$defs"]
        body = json.loads(kwargs["messages"][-1]["content"])
        assert len(body["candidates"]) == 1
        assert body["candidates"][0]["constraint_options"] == ["q0", "q1"]
        result = {"verifications": [{"candidate_id": "c0", "keep": self.keep, "reason": "fixture scope decision",
            "design_unit": "COUPLED_MECHANISM", "construction_evidence": "q0",
            "constraint_a": "q0", "constraint_b": "q0" if self.overlap else "q9" if self.bad else "q1"}]}
        return SimpleNamespace(model="fixture", usage=None, choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(content=json.dumps(result)))])


def test_one_positive_quote_repair_preserves_initial_failure_order_provenance_and_budget(tmp_path):
    primary, verified = frozen_scope()
    client, logs = RepairClient(), []
    limits = RequestLimits(deadline=time.monotonic() + 30, max_calls=6, calls=5, max_tokens=96000, tokens=40000)
    reranker = LLMReranker(client=client, model="fixture", on_call=logs.append,
        call_gate=ModelCallGate(tmp_path / "repair.lock", minimum_interval_seconds=0))
    token = current_limits.set(limits)
    try:
        result = reranker.repair_scope_evidence("独立业务设计题", primary, verified)
        assert reranker.repair_scope_evidence("独立业务设计题", primary, result) is result
    finally:
        current_limits.reset(token)
    assert len(client.requests) == 1 and limits.calls == 6
    assert limits.tokens > 40000 + 6144 and limits.token_reservations == 0
    assert logs[0]["operation_type"] == "RERANK_EVIDENCE_REPAIR" and logs[0]["retry_count"] == 0
    assert logs[0]["input_tokens"] is None and logs[0]["_token_charge"] > 6144
    request = client.requests[0]
    full_input_bytes = len(json.dumps({"messages": request["messages"], "response_format": request["response_format"]},
                                     ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert logs[0]["_token_charge"] == full_input_bytes + request["max_tokens"]
    assert [row["canonical_question_id"] for row in result] == ["positive"]
    assert result[0]["rerank_rank"] == 3 and result.audit["candidate_count"] == 50
    assert result.audit["invalid_candidate_count"] == 0 and result.audit["scope_verification_status"] == "COMPLETE"
    assert result.audit["scope_initial_verification_status"] == "PARTIAL"
    assert result.audit["scope_initial_invalid_evidence_count"] == 1
    repaired, negative = result.audit["candidates"]
    assert repaired["initial_diagnostic"]["decision"] == "INVALID_DESIGN_EVIDENCE"
    assert repaired["evidence_repair_status"] == "RECOVERED" and repaired["scope_verification_rank"] == 2
    assert repaired["retrieval_provenance"] == verified.audit["candidates"][0]["retrieval_provenance"]
    assert negative is verified.audit["candidates"][1] and negative["decision"] == "SCOPE_REJECTED"
    assert result.audit["evidence_repair"]["repair_count"] == result.audit["evidence_repair"]["recovered_count"] == 1


@pytest.mark.parametrize("calls,tokens,reservations", [(6, 1000, 0), (5, 95000, 0), (5, 90000, 5000)])
def test_insufficient_existing_call_or_token_budget_skips_without_reset(calls, tokens, reservations):
    primary, verified = frozen_scope()
    client = RepairClient()
    limits = RequestLimits(deadline=time.monotonic() + 30, max_calls=6, calls=calls,
        max_tokens=96000, tokens=tokens, token_reservations=reservations)
    token = current_limits.set(limits)
    try:
        result = LLMReranker(client=client, model="fixture").repair_scope_evidence("设计题", primary, verified)
    finally:
        current_limits.reset(token)
    assert client.requests == []
    assert (limits.calls, limits.tokens, limits.token_reservations) == (calls, tokens, reservations)
    assert result.audit["scope_verification_status"] == "PARTIAL" and result.audit["invalid_evidence_count"] == 1
    assert result.audit["evidence_repair"]["status"] == "SKIPPED_BUDGET"
    assert result.audit["evidence_repair"]["repair_count"] == 0


@pytest.mark.parametrize("failure", [False, True], ids=["still-invalid-proof", "transport-failure"])
def test_failed_single_repair_retains_partial_and_never_transport_retries(tmp_path, failure):
    primary, verified = frozen_scope()
    client = RepairClient(bad=True, failure=failure)
    limits = RequestLimits(deadline=time.monotonic() + 30, max_calls=6, calls=5, max_tokens=96000, tokens=10000)
    token = current_limits.set(limits)
    try:
        result = LLMReranker(client=client, model="fixture",
            call_gate=ModelCallGate(tmp_path / "repair.lock", minimum_interval_seconds=0)).repair_scope_evidence(
                "设计题", primary, verified)
    finally:
        current_limits.reset(token)
    assert len(client.requests) == 1 and limits.calls == 6 and limits.tokens > 10000 + 6144
    assert result == [] and result.audit["invalid_candidate_count"] == result.audit["invalid_evidence_count"] == 1
    assert result.audit["scope_verification_status"] == "PARTIAL"
    assert result.audit["evidence_repair"]["unresolved_count"] == 1
    assert result.audit["candidates"][0]["initial_diagnostic"]["decision"] == "INVALID_DESIGN_EVIDENCE"


def test_per_run_call_budget_and_deadline_still_apply_before_repair():
    primary, verified = frozen_scope()
    client = RepairClient()
    result = LLMReranker(client=client, model="fixture", budget=CallBudget(max_calls=0, max_tokens=96000)).repair_scope_evidence(
        "设计题", primary, verified)
    assert result.audit["evidence_repair"]["status"] == "SKIPPED_BUDGET" and client.requests == []
    token = current_limits.set(RequestLimits(deadline=time.monotonic() - 1))
    try:
        with pytest.raises(ValueError, match="QUERY_DEADLINE_EXCEEDED"):
            LLMReranker(client=client, model="fixture").repair_scope_evidence("设计题", primary, verified)
    finally:
        current_limits.reset(token)
    assert client.requests == []


def test_only_explicit_positive_design_failures_have_repair_authority():
    primary, verified = frozen_scope()
    client = RepairClient()
    verified.audit["candidates"][0]["model_scope_verification_keep"] = False
    assert LLMReranker(client=client, model="fixture").repair_scope_evidence("设计题", primary, verified) is verified
    assert client.requests == []
    primary, verified = frozen_scope()
    with pytest.raises(ValueError, match="DESIGN_EVIDENCE_REPAIR_SCOPE_REQUIRED"):
        LLMReranker(client=client, model="fixture").repair_scope_evidence("原理题", primary, verified)
    primary.audit["query_task"] = "OTHER"
    result = LLMReranker(client=client, model="fixture").repair_scope_evidence("工程设计题", primary, verified)
    assert result.audit["evidence_repair"]["recovered_count"] == 1
    assert len(client.requests) == 1


@pytest.mark.parametrize("decision", ["SCOPE_REJECTED", "INVALID_SCHEMA", "INVALID_TASK_EVIDENCE"])
def test_semantic_policy_schema_and_primary_failures_are_never_repaired(decision):
    primary, verified = frozen_scope()
    verified.audit["candidates"][0]["decision"] = decision
    client = RepairClient()
    assert LLMReranker(client=client, model="fixture").repair_scope_evidence("设计题", primary, verified) is verified
    assert client.requests == []


def test_existing_outer_verification_status_is_updated_but_initial_partial_is_retained():
    primary, verified = frozen_scope()
    verified.audit["candidate_verification_status"] = "PARTIAL"
    result = LLMReranker(client=RepairClient(), model="fixture").repair_scope_evidence("设计题", primary, verified)
    assert result.audit["candidate_verification_status"] == result.audit["scope_verification_status"] == "COMPLETE"
    assert result.audit["scope_initial_verification_status"] == "PARTIAL"


def test_remaining_tokens_between_old_payload_only_and_full_request_bounds_skip_repair():
    primary, verified = frozen_scope()
    client = RepairClient()
    reranker = LLMReranker(client=client, model="fixture")
    selected = RerankedCandidates(primary[:1], {"query_task": "INDEPENDENT_SYSTEM_DESIGN"})
    prepared = reranker._scope_request("设计题", selected)
    old = reranker.max_output_tokens + len(json.dumps(prepared["payload"], ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")) + len("设计题".encode("utf-8"))
    available = (old + prepared["token_upper_bound"]) // 2
    assert old < available < prepared["token_upper_bound"]
    limits = RequestLimits(deadline=time.monotonic() + 30, max_tokens=96000, tokens=96000 - available)
    token = current_limits.set(limits)
    try:
        result = reranker.repair_scope_evidence("设计题", primary, verified)
    finally:
        current_limits.reset(token)
    assert client.requests == [] and limits.tokens == 96000 - available
    assert result.audit["evidence_repair"]["status"] == "SKIPPED_BUDGET"


def test_repaired_overlapping_requirements_remain_unresolved_partial_evidence():
    primary, verified = frozen_scope()
    client = RepairClient(overlap=True)
    result = LLMReranker(client=client, model="fixture").repair_scope_evidence("设计题", primary, verified)
    assert len(client.requests) == 1
    assert result == [] and result.audit["scope_verification_status"] == "PARTIAL"
    assert result.audit["invalid_evidence_count"] == result.audit["invalid_candidate_count"] == 1
    row = result.audit["candidates"][0]
    assert row["decision"] == "INVALID_DESIGN_EVIDENCE"
    assert row["invalid_design_evidence_fields"] == ["constraint_a", "constraint_b"]
    assert result.audit["evidence_repair"]["resolved_count"] == 0
    assert result.audit["evidence_repair"]["unresolved_count"] == 1
