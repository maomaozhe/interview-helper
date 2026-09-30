"""Conservative per-run model-call and token caps."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CallBudget:
    max_calls: int
    max_tokens: int
    used_calls: int = 0
    used_tokens: int = 0

    def before_call(self, *, estimated_input_tokens: int = 0) -> None:
        if self.used_calls >= self.max_calls:
            raise ValueError("MODEL_CALL_BUDGET_EXCEEDED")
        if self.used_tokens + estimated_input_tokens > self.max_tokens:
            raise ValueError("MODEL_TOKEN_BUDGET_EXCEEDED")
        self.used_calls += 1

    def after_call(self, *, input_tokens: int | None, output_tokens: int | None) -> None:
        if input_tokens is not None:
            self.used_tokens += input_tokens
        if output_tokens is not None:
            self.used_tokens += output_tokens
