"""Request-scoped limits shared by routing, generation, embedding and reranking."""
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event, Lock
from contextlib import contextmanager, nullcontext
import math
import time


DEFAULT_QUERY_DEADLINE_SECONDS = 600
DEFAULT_QUERY_MAX_MODEL_CALLS = 8
DEFAULT_QUERY_MAX_TOTAL_MODEL_CALLS = 24
DEFAULT_QUERY_MAX_TOKENS = 256000


@dataclass
class RequestLimits:
    deadline: float
    max_calls: int = 6
    calls: int = 0
    cancelled: Event = field(default_factory=Event)
    lock: Lock = field(default_factory=Lock)
    max_tokens: int = 32000
    tokens: int = 0
    token_reservations: int = 0

    def check(self):
        if self.cancelled.is_set():
            raise ValueError("QUERY_CANCELLED")
        if time.monotonic() >= self.deadline:
            raise ValueError("QUERY_DEADLINE_EXCEEDED")

    def attempt(self):
        with self.lock:
            self.check()
            if self.calls >= self.max_calls - current_call_reserve.get():
                raise ValueError("QUERY_MODEL_BUDGET_EXCEEDED")
            self.calls += 1

    def reserve_tokens(self, upper_bound):
        with self.lock:
            self.check()
            if self.tokens+self.token_reservations+upper_bound>self.max_tokens:
                raise ValueError("QUERY_TOKEN_BUDGET_EXCEEDED")
            self.token_reservations+=upper_bound
        return upper_bound

    def settle_tokens(self, reserved, actual=None):
        with self.lock:
            self.token_reservations-=reserved
            # Unknown usage is charged conservatively, never treated as zero.
            self.tokens+=reserved if actual is None else max(0,actual)
            if self.tokens>self.max_tokens: raise ValueError("QUERY_TOKEN_BUDGET_EXCEEDED")

    def reconcile_tokens(self, assumed, actual):
        with self.lock:
            self.tokens+=actual-assumed
            if self.tokens>self.max_tokens: raise ValueError("QUERY_TOKEN_BUDGET_EXCEEDED")


current_limits = ContextVar("query_request_limits", default=None)
current_call_reserve = ContextVar("query_completion_call_reserve", default=0)
current_model_call_deadline = ContextVar("model_call_deadline", default=None)


class ModelCallDeadlineExceeded(ValueError):
    """An optional call exceeded its cooperative deadline; parent limits remain authoritative."""

    def __init__(self):
        super().__init__("MODEL_CALL_DEADLINE_EXCEEDED")


def check_model_call_deadline():
    limits = current_limits.get()
    if limits:
        limits.check()
    deadline = current_model_call_deadline.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise ModelCallDeadlineExceeded()


@contextmanager
def model_call_deadline(deadline):
    """Restrict observable work without replacing parent counters or cancellation.

    The synchronous transport's read timeout is per I/O, so this is not a
    preemptive wall-clock timer for a blocked read. No background call survives
    this context; its caller waits for transport cleanup before returning.
    """
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError("invalid model call deadline")
    previous = current_model_call_deadline.get()
    token = current_model_call_deadline.set(min(previous, deadline) if previous is not None else deadline)
    try:
        check_model_call_deadline()
        yield
    finally:
        current_model_call_deadline.reset(token)


def request_timeout(default_seconds):
    check_model_call_deadline()
    limits = current_limits.get()
    timeout = default_seconds
    if limits:
        timeout = min(timeout, max(0.01, limits.deadline - time.monotonic()))
    deadline = current_model_call_deadline.get()
    if deadline is not None:
        timeout = min(timeout, max(0.01, deadline - time.monotonic()))
    return timeout


@contextmanager
def measured_model_call(gate, timings, token_upper_bound=4096):
    check_model_call_deadline()
    limits=current_limits.get()
    reserved=limits.reserve_tokens(token_upper_bound) if limits else 0
    started=None
    try:
        with gate.call() if gate is not None else nullcontext() as waiting:
            timings.update(waiting or {"queue_ms":0,"interval_ms":0})
            started=time.perf_counter()
            yield
    finally:
        if started: timings["provider_ms"]=int((time.perf_counter()-started)*1000)
        if limits:
            limits.settle_tokens(reserved,None if started else 0)
            timings["_token_charge"]=reserved if started else 0
