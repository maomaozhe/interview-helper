import threading
import time

from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.providers.runtime import RequestLimits, current_limits


def test_cancelled_waiter_leaves_before_holder_finishes_and_does_not_poison_gate(tmp_path):
    gate = ModelCallGate(tmp_path / "shared.lock", minimum_interval_seconds=0)
    limits = RequestLimits(time.monotonic() + 5)
    queued = threading.Event()
    errors = []

    def waiter():
        token = current_limits.set(limits)
        queued.set()
        try:
            with gate.call():
                errors.append("unexpected provider execution")
        except ValueError as error:
            errors.append(str(error))
        finally:
            current_limits.reset(token)

    with gate.call():
        thread = threading.Thread(target=waiter)
        thread.start()
        assert queued.wait(1)
        limits.cancelled.set()
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert errors == ["QUERY_CANCELLED"]
    with gate.call():
        pass


def test_expired_request_does_not_enter_provider(tmp_path):
    gate = ModelCallGate(tmp_path / "shared.lock", minimum_interval_seconds=0)
    token = current_limits.set(RequestLimits(time.monotonic() - 1))
    try:
        try:
            with gate.call():
                raise AssertionError("expired request reached provider")
        except ValueError as error:
            assert str(error) == "QUERY_DEADLINE_EXCEEDED"
    finally:
        current_limits.reset(token)
