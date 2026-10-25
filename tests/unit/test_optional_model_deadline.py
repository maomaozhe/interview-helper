"""Optional work keeps parent budgets, cancellation and verified partial results."""
from contextlib import contextmanager
from types import SimpleNamespace
import threading

import pytest

from interview_intelligence.providers import runtime
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.search import reranker as module
from interview_intelligence.search.reranker import LLMReranker, RerankedCandidates
from test_scope_evidence_repair import RepairClient, frozen_scope


class Clock:
    now = 100.0

    def monotonic(self):
        return self.now

    def perf_counter(self):
        return self.now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(runtime, "time", clock)
    monkeypatch.setattr(module, "time", clock)
    return clock


def partial_with_result():
    primary, verified = frozen_scope()
    row = {"canonical_question_id": "already-verified", "canonical_text": "设计一套通知服务。",
           "rerank_rank": 1, "relevance_evidence": {"object": "通知", "focus": "设计"}}
    primary.insert(0, row)
    verified.append(row)
    verified.audit["candidates"].insert(0, {"canonical_question_id": "already-verified",
        "decision": "ACCEPTED", "scope_verification_status": "CONFIRMED", "scope_verification_keep": True})
    return primary, verified, row


def assert_partial(result, original, row):
    assert result == [row] and result[0] is row
    assert result.audit["scope_verification_status"] == "PARTIAL"
    assert result.audit["invalid_candidate_count"] == result.audit["invalid_evidence_count"] == 1
    assert result.audit["scope_initial_verification_status"] == "PARTIAL"
    assert result.audit["candidates"][0] is original.audit["candidates"][0]
    assert result.audit["candidates"][1]["decision"] == "INVALID_DESIGN_EVIDENCE"
    assert result.audit["candidates"][-1] is original.audit["candidates"][-1]


@pytest.mark.parametrize("remaining", [14.0, 22.999])
def test_short_remaining_time_does_not_spend_call_tokens_or_lose_verified_rows(clock, remaining):
    primary, verified, row = partial_with_result()
    client = RepairClient()
    limits = runtime.RequestLimits(clock.now + remaining, calls=4, max_tokens=96000, tokens=39252)
    token = runtime.current_limits.set(limits)
    try:
        result = LLMReranker(client=client, model="fixture").repair_scope_evidence("工程设计题", primary, verified)
        assert runtime.current_limits.get() is limits
    finally:
        runtime.current_limits.reset(token)
    assert client.requests == [] and (limits.calls, limits.tokens, limits.token_reservations) == (4, 39252, 0)
    assert result.audit["evidence_repair"]["status"] == "SKIPPED_TIME"
    assert not result.audit["evidence_repair"]["attempted"]
    assert_partial(result, verified, row)


@pytest.mark.parametrize("remaining,timeout", [(23.0, 20.0), (100.0, 30.0)])
def test_repair_timeout_is_bounded_by_parent_reserve_and_cap_without_new_limits(clock, tmp_path, remaining, timeout):
    primary, verified = frozen_scope()
    client = RepairClient()
    limits = runtime.RequestLimits(clock.now + remaining, calls=4, max_tokens=96000, tokens=39252)
    token = runtime.current_limits.set(limits)
    try:
        result = LLMReranker(client=client, model="fixture", call_gate=ModelCallGate(
            tmp_path / 'gate.lock', minimum_interval_seconds=0)).repair_scope_evidence("工程设计题", primary, verified)
        assert runtime.current_limits.get() is limits and runtime.current_model_call_deadline.get() is None
    finally:
        runtime.current_limits.reset(token)
    assert client.requests[0]["timeout"] == timeout
    assert limits.calls == 5 and limits.tokens > 39252 and limits.token_reservations == 0
    assert result.audit["evidence_repair"]["time_policy"]["mode"] == "cooperative"


def test_nested_deadlines_never_extend_outer_limit_and_restore_on_failure(clock):
    limits = runtime.RequestLimits(250, calls=2, tokens=77)
    token = runtime.current_limits.set(limits)
    try:
        with runtime.model_call_deadline(130):
            with runtime.model_call_deadline(200):
                assert runtime.request_timeout(180) == 30
            assert runtime.current_model_call_deadline.get() == 130
            clock.now = 131
            with pytest.raises(runtime.ModelCallDeadlineExceeded):
                runtime.request_timeout(180)
        assert runtime.current_model_call_deadline.get() is None
        assert runtime.request_timeout(180) == 119
    finally:
        runtime.current_limits.reset(token)
    assert (limits.calls, limits.tokens) == (2, 77)


def test_optional_waiter_leaves_gate_without_provider_and_refunds_only_unstarted_tokens(clock, tmp_path):
    gate = ModelCallGate(tmp_path / 'shared.lock', minimum_interval_seconds=0)
    limits = runtime.RequestLimits(250, max_tokens=96000, tokens=77)
    waiting, errors = threading.Event(), []

    def waiter():
        token = runtime.current_limits.set(limits)
        try:
            with runtime.model_call_deadline(120):
                waiting.set()
                with runtime.measured_model_call(gate, {}, token_upper_bound=1000):
                    errors.append("provider incorrectly started")
        except runtime.ModelCallDeadlineExceeded:
            errors.append("child deadline")
        finally:
            assert runtime.current_model_call_deadline.get() is None
            runtime.current_limits.reset(token)

    with gate.call():
        thread = threading.Thread(target=waiter)
        thread.start()
        assert waiting.wait(1)
        # Wait until the existing admission has happened, without advancing a
        # fake clock shared by threading's own timeout implementation.
        for _ in range(100):
            if limits.calls:
                break
            threading.Event().wait(0.001)
        clock.now = 121
        thread.join(timeout=1)
        assert not thread.is_alive()
    assert errors == ["child deadline"]
    assert limits.calls == 1 and limits.tokens == 77 and limits.token_reservations == 0
    with gate.call():
        pass


def test_optional_gate_interval_obeys_child_deadline_without_spending_tokens(clock, tmp_path, monkeypatch):
    from interview_intelligence.providers import gate as gate_module
    monkeypatch.setattr(gate_module, 'time', clock)
    gate = ModelCallGate(tmp_path/'interval.lock', minimum_interval_seconds=2)
    with gate.call():
        pass
    limits = runtime.RequestLimits(250, tokens=77)
    token = runtime.current_limits.set(limits)
    try:
        with pytest.raises(runtime.ModelCallDeadlineExceeded):
            with runtime.model_call_deadline(101):
                with runtime.measured_model_call(gate, {}, token_upper_bound=1000):
                    pytest.fail('optional call reached provider after interval deadline')
        assert (limits.calls, limits.tokens, limits.token_reservations) == (1, 77, 0)
        assert runtime.current_model_call_deadline.get() is None
        with gate.call():
            pass
    finally:
        runtime.current_limits.reset(token)


def test_optional_lock_file_permission_backoff_obeys_deadline_and_releases_gate(clock, tmp_path, monkeypatch):
    from interview_intelligence.providers import gate as gate_module
    import os
    attempts = []
    gate = ModelCallGate(tmp_path/'permission.lock', minimum_interval_seconds=0)

    def denied(*args):
        attempts.append(args)
        raise PermissionError('fixture lock-file race')

    limits = runtime.RequestLimits(250, tokens=77)
    token = runtime.current_limits.set(limits)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(gate_module, 'time', clock)
            patch.setattr(gate_module, 'os', SimpleNamespace(open=denied, O_RDWR=os.O_RDWR, O_CREAT=os.O_CREAT))
            with pytest.raises(runtime.ModelCallDeadlineExceeded):
                with runtime.model_call_deadline(100.1):
                    with runtime.measured_model_call(gate, {}, token_upper_bound=1000):
                        pytest.fail('lock-file backoff started a provider')
        assert len(attempts) == 1 and limits.calls == 1
        assert limits.tokens == 77 and limits.token_reservations == 0
        with gate.call():
            pass
    finally:
        runtime.current_limits.reset(token)


class LateClient(RepairClient):
    def __init__(self, clock, *, streamed=False, after_final=False, delay=32, cancel_on_close=False, limits=None):
        super().__init__()
        self.clock, self.streamed, self.after_final, self.delay = clock, streamed, after_final, delay
        self.closed = False
        self.cancel_on_close, self.limits = cancel_on_close, limits

    def create(self, **kwargs):
        response = super().create(**kwargs)
        if not self.streamed:
            self.clock.now += self.delay
            return response
        owner = self

        class Stream:
            def __iter__(self):
                if not owner.after_final:
                    owner.clock.now += owner.delay
                yield SimpleNamespace(model="fixture", usage=None, choices=[SimpleNamespace(
                    finish_reason="stop", delta=SimpleNamespace(content=response.choices[0].message.content))])
                if owner.after_final:
                    owner.clock.now += owner.delay

            def close(self):
                owner.closed = True
                if owner.cancel_on_close:
                    owner.limits.cancelled.set()

        return Stream()


@pytest.mark.parametrize("streamed,after_final,delay", [(False, False, 32), (True, False, 39), (True, True, 32)])
def test_optional_late_response_dribble_and_late_eof_preserve_partial_and_charge_unknown(clock, tmp_path, streamed, after_final, delay):
    primary, verified, row = partial_with_result()
    limits = runtime.RequestLimits(250, max_tokens=96000, tokens=10000)
    client = LateClient(clock, streamed=streamed, after_final=after_final, delay=delay)
    logs = []
    ranker = LLMReranker(client=client, model="fixture", stream=streamed, on_call=logs.append,
        call_gate=ModelCallGate(tmp_path / 'gate.lock', minimum_interval_seconds=0))
    token = runtime.current_limits.set(limits)
    try:
        result = ranker.repair_scope_evidence("工程设计题", primary, verified)
        assert runtime.current_model_call_deadline.get() is None
        assert runtime.current_limits.get() is limits
    finally:
        runtime.current_limits.reset(token)
    assert len(client.requests) == limits.calls == len(logs) == 1
    assert result.audit['evidence_repair']['status'] == 'TIMED_OUT'
    assert logs[0]['status'] == 'FAILED' and logs[0]['input_tokens'] is None
    assert limits.tokens == 10000 + logs[0]['_token_charge'] and limits.token_reservations == 0
    assert logs[0]['provider_ms'] >= delay * 1000  # Explicit cooperative-overrun limitation.
    if streamed:
        assert client.closed
    assert_partial(result, verified, row)


@pytest.mark.parametrize("cancel", [False, True])
def test_parent_expiry_or_cancel_is_never_softened_as_optional_failure(clock, tmp_path, cancel):
    primary, verified, _ = partial_with_result()
    limits = runtime.RequestLimits(123 if not cancel else 250, max_tokens=96000)
    client = LateClient(clock, streamed=True, delay=32, cancel_on_close=cancel, limits=limits)
    ranker = LLMReranker(client=client, model='fixture', stream=True,
        call_gate=ModelCallGate(tmp_path/'gate.lock', minimum_interval_seconds=0))
    token = runtime.current_limits.set(limits)
    try:
        with pytest.raises(ValueError, match='QUERY_CANCELLED' if cancel else 'QUERY_DEADLINE_EXCEEDED'):
            ranker.repair_scope_evidence('工程设计题', primary, verified)
        assert runtime.current_model_call_deadline.get() is None
    finally:
        runtime.current_limits.reset(token)
    assert len(client.requests) == 1 and client.closed


def test_inherited_short_child_budget_skips_optional_work(clock):
    primary, verified, row = partial_with_result()
    client = RepairClient()
    with runtime.model_call_deadline(114):
        result = LLMReranker(client=client, model='fixture').repair_scope_evidence('工程设计题', primary, verified)
    assert client.requests == [] and result.audit['evidence_repair']['status'] == 'SKIPPED_TIME'
    assert_partial(result, verified, row)


def test_mandatory_scope_failure_keeps_parent_deadline_and_has_no_soft_repair_authority(clock, tmp_path):
    primary, _ = frozen_scope()
    client = LateClient(clock, streamed=True, delay=32)
    ranker = LLMReranker(client=client, model='fixture', stream=True,
        call_gate=ModelCallGate(tmp_path/'gate.lock', minimum_interval_seconds=0))
    limits = runtime.RequestLimits(123, max_tokens=96000)
    token = runtime.current_limits.set(limits)
    try:
        with pytest.raises(ValueError, match='QUERY_DEADLINE_EXCEEDED'):
            ranker._verify_scope('工程设计题', RerankedCandidates(primary[:1], primary.audit))
        assert runtime.current_model_call_deadline.get() is None
    finally:
        runtime.current_limits.reset(token)
    assert len(client.requests) == 1 and client.closed
