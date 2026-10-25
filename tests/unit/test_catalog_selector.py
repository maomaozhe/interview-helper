import copy
import json
import time
from types import SimpleNamespace

import pytest

from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.providers.budget import CallBudget
from interview_intelligence.providers.runtime import RequestLimits, current_limits
from interview_intelligence.relevance_scope import compile_relevance_scope
from interview_intelligence.search.catalog_selector import CatalogSoftSelector


def scope():
    return compile_relevance_scope(["列出所有工程系统设计题", "具体业务系统设计，候选即可"], candidate_mode=True)


def catalog(count=320):
    return [{"canonical_question_id": f"question-{index:04}",
        "canonical_text": f"为业务需求{index}设计应用功能，包含用户操作和状态变化"} for index in range(count)]


def profile(tmp_path, *, selected=None, fail=False, usage=True, stream=False):
    calls, callbacks = [], []
    def create(**kwargs):
        calls.append(kwargs)
        if fail:
            raise RuntimeError("private provider response must never leak")
        result = selected if selected is not None else ["c10", "c319"]
        measured = SimpleNamespace(prompt_tokens=121, completion_tokens=7) if usage else None
        content = json.dumps({"selected": result})
        if stream:
            class Stream:
                def __iter__(self):
                    yield SimpleNamespace(usage=None, model="resolved", choices=[SimpleNamespace(
                        delta=SimpleNamespace(content=content), finish_reason="stop")])
                    yield SimpleNamespace(usage=measured, model="resolved", choices=[])
                def close(self): self.closed = True
            return Stream()
        return SimpleNamespace(usage=measured, model="resolved", choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=content))])
    def on_call(entry):
        callbacks.append(entry)
        limits = current_limits.get()
        if limits and type(entry.get("input_tokens")) is int and type(entry.get("output_tokens")) is int:
            limits.reconcile_tokens(entry["_token_charge"], entry["input_tokens"] + entry["output_tokens"])
    value = SimpleNamespace(client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        model="shared-model", call_gate=ModelCallGate(tmp_path / "model.lock", minimum_interval_seconds=0),
        timeout_seconds=180, on_call=on_call, budget=None, stream=stream)
    return value, calls, callbacks


@pytest.mark.parametrize("stream", [False, True])
def test_complete_catalog_uses_shared_profile_once_and_settles_actual_usage(tmp_path, stream):
    selected_profile, calls, callbacks = profile(tmp_path, stream=stream)
    selector = CatalogSoftSelector(selected_profile)
    original_scope = scope()
    before = copy.deepcopy(original_scope)
    rows = catalog()
    limits = RequestLimits(time.monotonic() + 180, max_calls=6, max_tokens=96000)
    token = current_limits.set(limits)
    try:
        result = selector.select("具体业务系统设计", rows, original_scope, [row["canonical_question_id"] for row in rows[:10]])
    finally:
        current_limits.reset(token)
    assert original_scope == before
    assert result["attempted"] and result["applied"] and result["selected_ids"] == ["question-0010", "question-0319"]
    assert limits.calls == 1 and limits.tokens == 128 and limits.token_reservations == 0
    assert len(calls) == len(callbacks) == 1
    assert result["provider_admitted"]
    assert result["request_budget"]["before"]["max_calls"] == 6
    assert result["request_budget"]["after"]["tokens"] == 128
    assert callbacks[0]["operation_type"] == "BUSINESS_CATALOG_SELECTION"
    payload = json.loads(calls[0]["messages"][1]["content"])
    assert len(payload["catalog"]) == 320
    assert payload["catalog"][-1]["title"] == rows[-1]["canonical_text"]
    assert payload["relevance_scope"] == before
    assert calls[0]["max_tokens"] == 6144
    assert calls[0]["model"] == "shared-model"


@pytest.mark.parametrize("selected", [["c10", "c10"], ["unknown"], ["c0"], [4], [f"c{i}" for i in range(10, 51)]])
def test_invalid_output_is_failed_soft_selection_not_partial_or_retry(tmp_path, selected):
    value, calls, callbacks = profile(tmp_path, selected=selected)
    result = CatalogSoftSelector(value).select("具体业务系统设计", catalog(), scope(), ["question-0000"])
    assert result["attempted"] and not result["applied"]
    assert result["selected_ids"] == [] and result["reason"] == "screen_failed"
    assert len(calls) == len(callbacks) == 1
    assert callbacks[0]["status"] == "FAILED"


@pytest.mark.parametrize("remaining_calls,max_tokens,expected", [(2, 96000, "request_budget"), (6, 100, "request_budget")])
def test_preflight_cannot_spend_proof_calls_or_exceed_request_tokens(tmp_path, remaining_calls, max_tokens, expected):
    value, calls, callbacks = profile(tmp_path)
    limits = RequestLimits(time.monotonic() + 180, max_calls=remaining_calls, max_tokens=max_tokens)
    token = current_limits.set(limits)
    try:
        result = CatalogSoftSelector(value).select("具体业务系统设计", catalog(), scope(), [])
    finally:
        current_limits.reset(token)
    assert not result["attempted"] and result["reason"] == expected
    assert not calls and not callbacks and limits.calls == limits.tokens == 0


def test_unknown_usage_and_transport_failure_keep_conservative_charge(tmp_path):
    value, calls, callbacks = profile(tmp_path, fail=True)
    limits = RequestLimits(time.monotonic() + 180, max_tokens=96000)
    token = current_limits.set(limits)
    try:
        result = CatalogSoftSelector(value).select("具体业务系统设计", catalog(), scope(), [])
    finally:
        current_limits.reset(token)
    assert result["attempted"] and not result["applied"]
    assert limits.calls == 1 and limits.tokens == result["prepared"]["token_upper_bound"]
    assert limits.token_reservations == 0 and len(callbacks) == 1
    assert "private provider" not in json.dumps(result)


def test_oversize_input_is_never_truncated_into_first_catalog_prefix(tmp_path):
    value, calls, callbacks = profile(tmp_path)
    rows = catalog(2)
    rows[0]["canonical_text"] = "完整标题" * 60000
    result = CatalogSoftSelector(value).select("具体业务系统设计", rows, scope(), [])
    assert not result["attempted"] and not calls and not callbacks
    assert result["reason"] == "catalog_input_capacity"


def test_any_engineering_or_unselected_mode_cannot_start_catalog(tmp_path):
    value, calls, _ = profile(tmp_path)
    engineering = compile_relevance_scope(["列出所有工程系统设计题，不要AI"])
    with pytest.raises(ValueError, match="CATALOG_SCOPE_NOT_AUTHORIZED"):
        CatalogSoftSelector(value).select("工程系统设计", catalog(), engineering, [])
    assert not calls


def test_budget_before_call_failure_does_not_run_after_call_or_provider(tmp_path):
    value, calls, callbacks = profile(tmp_path)
    class Budget:
        used_calls = used_tokens = 0
        max_calls, max_tokens = 6, 96000
        after_count = 0
        def before_call(self, **kwargs): raise ValueError("MODEL_CALL_BUDGET_EXCEEDED")
        def after_call(self, **kwargs): self.after_count += 1
    value.budget = Budget()
    result = CatalogSoftSelector(value).select("具体业务系统设计", catalog(), scope(), [])
    assert result["attempted"] and not result["provider_admitted"]
    assert not result["applied"] and not calls and value.budget.after_count == 0
    assert len(callbacks) == 1 and callbacks[0]["status"] == "FAILED"


def test_invalid_catalog_preparation_is_explicit_unavailable_without_provider(tmp_path):
    value, calls, callbacks = profile(tmp_path)
    rows = catalog(1) * 2
    result = CatalogSoftSelector(value).select("具体业务系统设计", rows, scope(), [])
    assert result["reason"] == "catalog_preparation_failed"
    assert not result["attempted"] and not result["provider_admitted"] and not calls and not callbacks


def test_profile_budget_also_retains_unknown_usage_charge(tmp_path):
    value, calls, callbacks = profile(tmp_path, usage=False)
    value.budget = CallBudget(max_calls=6, max_tokens=96000)
    result = CatalogSoftSelector(value).select("具体业务系统设计", catalog(), scope(), [])
    assert result["applied"] and result["provider_admitted"]
    assert value.budget.used_calls == 1
    assert value.budget.used_tokens == result["prepared"]["token_upper_bound"]


def test_host_exclusion_and_independent_authority_precedes_default_but_catalog_is_not_hard_filtered(tmp_path):
    value, _, _ = profile(tmp_path)
    selector = CatalogSoftSelector(value)
    restricted = compile_relevance_scope(["具体业务系统设计，只要独立完整系统，排除AI"], candidate_mode=True)
    assert restricted["excluded_domains"] == ["AI"] and restricted["independent_only"] is True
    rows = catalog()
    rows[0]["canonical_text"] = "设计AI辅助用户申请和预约应用"
    prepared = selector.prepare("具体业务系统设计", rows, restricted, [])
    prompt, payload = prepared["messages"][0]["content"], json.loads(prepared["messages"][1]["content"])
    assert "仅按host原话授权执行明确排除和独立要求" in prompt
    assert "无这些限制时" in prompt
    assert payload["relevance_scope"] == restricted
    assert len(payload["catalog"]) == 320 and payload["catalog"][0]["title"] == rows[0]["canonical_text"]


@pytest.mark.parametrize("prompt,completion", [(7, None), (None, 9), (True, 3), (7, False), (-1, 3), (7, -1)])
def test_partial_or_invalid_usage_keeps_all_accounting_conservative_without_rewriting_sdk(tmp_path, prompt, completion):
    value, _, callbacks = profile(tmp_path)
    original_usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)
    sdk_response = SimpleNamespace(usage=original_usage, model="resolved", choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content='{"selected":["c319"]}'))])
    value.client.chat.completions.create = lambda **kwargs: sdk_response
    value.budget = CallBudget(max_calls=6, max_tokens=96000)
    limits = RequestLimits(time.monotonic() + 180, max_calls=6, max_tokens=96000)
    token = current_limits.set(limits)
    try:
        result = CatalogSoftSelector(value).select("具体业务系统设计", catalog(), scope(), [])
    finally:
        current_limits.reset(token)
    assert result["applied"] and result["provider_admitted"] and not result["provider"]["usage_complete"]
    assert callbacks[0]["input_tokens"] is callbacks[0]["output_tokens"] is None
    assert limits.tokens == value.budget.used_tokens == result["prepared"]["token_upper_bound"]
    assert limits.token_reservations == 0 and len(callbacks) == 1
    assert sdk_response.usage is original_usage
    assert (sdk_response.usage.prompt_tokens, sdk_response.usage.completion_tokens) == (prompt, completion)
