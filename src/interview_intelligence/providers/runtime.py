"""Request-scoped limits shared by routing, generation, embedding and reranking."""
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event, Lock
from contextlib import contextmanager, nullcontext
import time


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
            if self.calls >= self.max_calls:
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


def request_timeout(default_seconds):
    limits = current_limits.get()
    if limits:
        limits.check()
        return min(default_seconds, max(0.01, limits.deadline - time.monotonic()))
    return default_seconds


@contextmanager
def measured_model_call(gate, timings, token_upper_bound=4096):
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
