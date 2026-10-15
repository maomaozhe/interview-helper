"""One request-scoped retry for transient relevance-provider failures."""
from contextvars import ContextVar
from dataclasses import dataclass, field

import httpx
from openai import APIError, APIStatusError

from interview_intelligence.providers.runtime import current_limits


rerank_retry_count = ContextVar("rerank_retry_count", default=0)


class RelevanceProviderUnavailable(ValueError):
    def __init__(self, *, phase, retry_count, error):
        super().__init__("RELEVANCE_PROVIDER_UNAVAILABLE")
        self.diagnostics = {"phase": phase, "transport_retry_count": retry_count,
                            "error_type": type(error).__name__}


def _transient(error):
    if isinstance(error, APIStatusError):
        return error.status_code in {408, 429} or error.status_code >= 500
    return isinstance(error, (APIError, httpx.TransportError))


def _provider_failure(error):
    # Older deployed rerankers wrap required-verification failures. Unwrap
    # only these known envelopes; semantic and budget errors remain fatal.
    while isinstance(error, ValueError) and str(error) in {
        "RERANK_TASK_SHAPE_FAILED_CLOSED", "RERANK_SCOPE_VERIFICATION_FAILED_CLOSED",
        "REQUIRED_RELEVANCE_VERIFICATION_FAILED",
    } and error.__cause__ is not None:
        error = error.__cause__
    return error


@dataclass
class RelevanceRetry:
    retry_count: int = 0
    diagnostics: list[dict] = field(default_factory=list)

    def run(self, operation, *, phase, token_upper_bound, required_calls=1):
        for attempt in range(2):
            token = rerank_retry_count.set(attempt)
            try:
                return operation()
            except Exception as error:
                failure = _provider_failure(error)
                if not _transient(failure):
                    raise
                limits = current_limits.get()
                permitted = self.retry_count == 0
                if limits:
                    limits.check()
                    with limits.lock:
                        permitted = permitted and (
                            limits.calls + required_calls <= limits.max_calls
                            and limits.tokens + limits.token_reservations + token_upper_bound <= limits.max_tokens
                        )
                if attempt or not permitted:
                    raise RelevanceProviderUnavailable(phase=phase, retry_count=self.retry_count, error=failure) from error
                self.retry_count = 1
                self.diagnostics.append({"phase": phase, "error_type": type(failure).__name__})
            finally:
                rerank_retry_count.reset(token)
