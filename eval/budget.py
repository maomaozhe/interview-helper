"""Reserve whole upper bounds before calls; missing usage retains reservations."""
from eval.validate_gold import require


class EvaluationBudget:
    def __init__(self, max_calls, max_tokens, *, per_call_tokens=16384):
        require(type(max_calls) is int and max_calls > 0 and type(max_tokens) is int and max_tokens > 0, "positive evaluation budget required")
        self.max_calls, self.max_tokens = max_calls, max_tokens
        self.used_calls, self.used_tokens = 0, 0
        self.per_call_tokens, self.reservation = per_call_tokens, None

    def reserve(self, *, calls=1, tokens=None):
        require(self.reservation is None, "unreconciled evaluation budget reservation")
        tokens = self.per_call_tokens if tokens is None else tokens
        require(self.used_calls + calls <= self.max_calls and self.used_tokens + tokens <= self.max_tokens, "EVALUATION_BUDGET_EXCEEDED")
        self.used_calls += calls
        self.used_tokens += tokens
        self.reservation = (calls, tokens)

    def before_call(self, *, estimated_input_tokens=0):
        # Prior failed attempts without usage retain the full upper bound.
        self.reservation = None
        # Production adapters estimate text length / 2. Eight bytes per estimated
        # token conservatively covers UTF-8 input; output is capped by collector.
        self.reserve(tokens=self.per_call_tokens + estimated_input_tokens * 8 + 1024)

    def reconcile(self, *, calls, tokens):
        require(self.reservation is not None, "missing evaluation budget reservation")
        reserved_calls, reserved_tokens = self.reservation
        self.used_calls += calls - reserved_calls
        if tokens is not None:
            self.used_tokens += tokens - reserved_tokens
        self.reservation = None
        require(self.used_calls <= self.max_calls and self.used_tokens <= self.max_tokens, "EVALUATION_BUDGET_EXCEEDED")

    def after_call(self, *, input_tokens, output_tokens):
        if self.reservation is not None:
            self.reconcile(calls=1, tokens=input_tokens + output_tokens if type(input_tokens) is int and type(output_tokens) is int else None)

    def summary(self):
        return {"max_calls": self.max_calls, "max_tokens": self.max_tokens,
                "charged_calls": self.used_calls, "charged_tokens": self.used_tokens}
