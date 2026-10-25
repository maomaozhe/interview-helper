from copy import deepcopy
import json
from types import SimpleNamespace
import time

import httpx
from openai import APIError, APIStatusError
import pytest

from interview_intelligence.providers.runtime import RequestLimits, current_limits
from interview_intelligence.search.reranker import LLMReranker
from interview_intelligence.search.retry import RelevanceProviderUnavailable, RelevanceRetry


def provider_error():
    return APIError("stream interrupted", request=httpx.Request("POST", "https://provider.test/v1"), body=None)


class Client:
    def __init__(self, failures=()):
        self.failures = set(failures)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        if len(self.requests) in self.failures:
            raise provider_error()
        payload = json.loads(kwargs["messages"][1]["content"])
        for candidate in payload["candidates"]:
            if isinstance(candidate["question"], dict):
                candidate["selected_evidence"] = next(iter(candidate["question"]), "")
                candidate["question"] = "".join(candidate["question"].values())
        if "verifications" in kwargs["response_format"]["json_schema"]["schema"]["properties"]:
            result = {"verifications": [{"candidate_id": r["id"], "keep": True,
                                         "reason": "题干明确要求独立设计服务"} for r in payload["candidates"]]}
            if "DesignVerification" in kwargs["response_format"]["json_schema"]["schema"]["$defs"]:
                for row, candidate in zip(result["verifications"], payload["candidates"]):
                    row.update(design_unit="NEW_OR_REBUILT_SYSTEM", construction_evidence=candidate["question"],
                               constraint_a="", constraint_b="")
        else:
            fields = kwargs["response_format"]["json_schema"]["schema"]["$defs"]["CandidateRank"]["properties"]
            relation = {"object_scope": "SAME", "focus_scope": "SAME"} if "object_scope" in fields else {
                "object_relation": "EXPLICIT", "focus_relation": "DIRECT"}
            result = {"query_task": "INDEPENDENT_SYSTEM_DESIGN", "query_task_evidence": payload["query"],
                "query_object": payload["query"], "query_focus": payload["query"],
                "query_object_evidence": payload["query"], "query_focus_evidence": payload["query"],
                "rankings": [{"candidate_id": r["id"], "candidate_task": "DESIGN_TASK",
                    "task_evidence": r["question"], "object_evidence": r.get("selected_evidence", r["question"]),
                    "focus_evidence": r.get("selected_evidence", r["question"]), **relation} for r in payload["candidates"]]}
        result = {key: value for key, value in result.items()
                  if key in kwargs["response_format"]["json_schema"]["schema"]["properties"]}
        return SimpleNamespace(model="test", usage=SimpleNamespace(prompt_tokens=20, completion_tokens=30),
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(result)))])


CANDIDATES = [{"canonical_question_id": "one", "canonical_text": "如何设计一个排行榜服务？"}]


def test_primary_transient_retry_keeps_exact_query_candidates_and_audit():
    client, logs = Client([1]), []
    rows = LLMReranker(client=client, model="test", on_call=logs.append).rerank("业务系统设计题", CANDIDATES)
    assert [r["canonical_question_id"] for r in rows] == ["one"]
    assert client.requests[0] == client.requests[1]
    assert len(client.requests) == 3
    assert [(r["status"], r["retry_count"]) for r in logs] == [("FAILED", 0), ("SUCCEEDED", 1), ("SUCCEEDED", 0)]
    assert rows.audit["transport_retry_count"] == 1
    assert rows.audit["transport_retries"] == [{"phase": "PRIMARY", "error_type": "APIError"}]


def test_scope_retry_does_not_repeat_primary_ranking():
    client, logs = Client([2]), []
    rows = LLMReranker(client=client, model="test", on_call=logs.append).rerank("业务系统设计题", CANDIDATES)
    assert len(client.requests) == 3
    assert client.requests[1] == client.requests[2]
    assert [r["operation_type"] for r in logs] == ["RERANK", "RERANK_VERIFY", "RERANK_VERIFY"]
    assert rows.audit["transport_retries"][0]["phase"] == "SCOPE"


@pytest.mark.parametrize("failures,expected_calls,phase", [([1, 2], 2, "PRIMARY"), ([1, 3], 3, "SCOPE")])
def test_total_retry_cap_is_shared_by_primary_and_scope(failures, expected_calls, phase):
    client = Client(failures)
    with pytest.raises(RelevanceProviderUnavailable) as caught:
        LLMReranker(client=client, model="test").rerank("业务系统设计题", CANDIDATES)
    assert len(client.requests) == expected_calls
    assert caught.value.diagnostics == {"phase": phase, "transport_retry_count": 1, "error_type": "APIError"}


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_invalid_configuration_and_permission_errors_are_not_retried(status):
    calls = []
    error = APIStatusError("configuration rejected", response=httpx.Response(status,
        request=httpx.Request("POST", "https://provider.test/v1")), body=None)
    def call():
        calls.append(1)
        raise error
    with pytest.raises(APIStatusError):
        RelevanceRetry().run(call, phase="PRIMARY", token_upper_bound=100, required_calls=2)
    assert len(calls) == 1


@pytest.mark.parametrize("reason", ["QUERY_CANCELLED", "QUERY_DEADLINE_EXCEEDED", "QUERY_TOKEN_BUDGET_EXCEEDED",
                                   "QUERY_MODEL_BUDGET_EXCEEDED", "STREAM_INCOMPLETE", "MODEL_OUTPUT_TRUNCATED",
                                   "reranker returned a different candidate set"])
def test_budget_control_and_invalid_outputs_are_not_retried(reason):
    calls = []
    def call():
        calls.append(1)
        raise ValueError(reason)
    with pytest.raises(ValueError, match=reason):
        RelevanceRetry().run(call, phase="PRIMARY", token_upper_bound=100, required_calls=2)
    assert len(calls) == 1


@pytest.mark.parametrize("calls,tokens", [(5, 0), (3, 95000)])
def test_insufficient_remaining_calls_or_tokens_prevents_provider_retry(calls, tokens):
    limits = RequestLimits(deadline=time.monotonic()+30, max_calls=6, calls=calls, max_tokens=96000, tokens=tokens)
    invoked = []
    def call():
        invoked.append(1)
        raise provider_error()
    token = current_limits.set(limits)
    try:
        with pytest.raises(RelevanceProviderUnavailable):
            RelevanceRetry().run(call, phase="PRIMARY", token_upper_bound=2000, required_calls=2)
    finally:
        current_limits.reset(token)
    assert len(invoked) == 1 and limits.tokens == tokens


def test_failed_unknown_usage_remains_charged_and_blocks_unaffordable_retry():
    logs, client = [], Client([1])
    reranker = LLMReranker(client=client, model="test", on_call=logs.append)
    bound = reranker._primary_request("业务系统设计题", CANDIDATES)["token_upper_bound"]
    limits = RequestLimits(deadline=time.monotonic()+30, max_calls=6, max_tokens=bound * 2 - 1)
    token = current_limits.set(limits)
    try:
        with pytest.raises(RelevanceProviderUnavailable):
            reranker.rerank("业务系统设计题", CANDIDATES)
    finally:
        current_limits.reset(token)
    assert len(client.requests) == 1
    assert limits.tokens == bound and limits.token_reservations == 0
    assert logs[0]["input_tokens"] is None and logs[0]["output_tokens"] is None


def test_retry_state_does_not_leak_to_following_queries():
    client, logs = Client([1, 4]), []
    reranker = LLMReranker(client=client, model="test", on_call=logs.append)
    first = reranker.rerank("业务系统设计题", CANDIDATES)
    second = reranker.rerank("业务系统设计题", CANDIDATES)
    assert first.audit["transport_retry_count"] == second.audit["transport_retry_count"] == 1
    assert len(client.requests) == 6


def test_full_primary_request_declines_retry_when_complete_cost_exceeds_original_96k():
    from interview_intelligence.search.citations import primary_candidate_payload
    text = "设计一个独立存储服务：" + "可靠存储并发读写" * 14 + "。"
    candidates = [{"canonical_question_id": str(index), "canonical_text": text} for index in range(50)]
    legacy_bound = 6144 + len(json.dumps(primary_candidate_payload(candidates), ensure_ascii=False).encode("utf-8"))
    assert 13000 + legacy_bound * 2 > 96000
    client, logs = Client([1]), []
    limits = RequestLimits(deadline=time.monotonic()+30, max_calls=6, max_tokens=96000, tokens=13000)
    reranker = LLMReranker(client=client, model="test", on_call=logs.append)
    full_bound = reranker._primary_request("业务系统设计题", candidates)["token_upper_bound"]
    assert 13000 + full_bound <= 96000 < 13000 + full_bound * 2
    token = current_limits.set(limits)
    try:
        with pytest.raises(RelevanceProviderUnavailable):
            reranker.rerank("业务系统设计题", candidates)
    finally:
        current_limits.reset(token)
    assert len(client.requests) == len(logs) == 1
    assert logs[0]["_token_charge"] == full_bound and limits.token_reservations == 0
    assert limits.tokens == 13000 + full_bound < limits.max_tokens == 96000


def test_full_scope_request_must_fit_96k_when_all_prior_usage_is_unknown():
    class UnknownClient(Client):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            response.usage = None
            return response
    text = "设计一个独立存储服务：" + "可靠存储并发读写" * 14 + "。"
    candidates = [{"canonical_question_id": str(index), "canonical_text": text} for index in range(50)]
    # Isolate scope admission: the primary succeeds with unknown usage. A
    # failed primary would already block its retry under the complete bound.
    client, logs = UnknownClient(), []
    limits = RequestLimits(deadline=time.monotonic()+30, max_calls=6, max_tokens=96000, tokens=14000)
    token = current_limits.set(limits)
    try:
        with pytest.raises(ValueError, match="QUERY_TOKEN_BUDGET_EXCEEDED"):
            LLMReranker(client=client, model="test", on_call=logs.append).rerank("业务系统设计题", candidates)
    finally:
        current_limits.reset(token)
    assert len(client.requests) == 1 and len(logs) == 2
    assert all(entry["input_tokens"] is None for entry in logs)
    assert limits.tokens == 14000 + sum(entry.get("_token_charge", 0) for entry in logs)
    assert limits.token_reservations == 0 and limits.max_tokens == 96000


def test_exhausted_relevance_provider_returns_no_unverified_search_candidates():
    from test_elasticsearch_retriever import Encoder, FakeClient
    from interview_intelligence.search.elasticsearch import ElasticsearchRetriever
    class Reranker:
        version = "test"
        fail_closed = True
        def rerank(self, query, candidates):
            raise RelevanceProviderUnavailable(phase="PRIMARY", retry_count=1, error=provider_error())
    result = ElasticsearchRetriever("http://unused", Encoder(), client=FakeClient(), reranker=Reranker()).retrieve(
        "业务系统设计", ["a", "b"], "HYBRID_RERANK", 20)
    assert result["data"] == []
    assert result["meta"]["rerank_status"] == "FAILED"
    assert result["meta"]["candidate_count"] == 2
    assert result["meta"]["rerank_audit"]["verification_failure"]["transport_retry_count"] == 1


def test_personal_model_boundary_preserves_safe_terminal_relevance_failure(monkeypatch):
    from interview_intelligence.tenant_retriever import PersonalReranker
    error = RelevanceProviderUnavailable(phase="SCOPE", retry_count=1, error=provider_error())
    def fail(self, query, candidates):
        raise error from provider_error()
    monkeypatch.setattr(LLMReranker, "rerank", fail)
    with pytest.raises(RelevanceProviderUnavailable) as caught:
        PersonalReranker(client=Client(), model="test").rerank("业务系统设计", CANDIDATES)
    assert caught.value is not error
    assert caught.value.diagnostics == error.diagnostics
    assert caught.value.__cause__ is None
    assert str(caught.value) == "RELEVANCE_PROVIDER_UNAVAILABLE"
