"""Reserve whole upper bounds before calls; missing usage retains reservations."""
from eval.validate_gold import require


class EvaluationBudget:
    def __init__(self, max_calls, max_tokens, *, per_call_tokens=16384, on_change=None):
        require(type(max_calls) is int and max_calls > 0 and type(max_tokens) is int and max_tokens > 0, "positive evaluation budget required")
        require(type(per_call_tokens) is int and per_call_tokens > 0, "positive per-call budget required")
        self.max_calls, self.max_tokens = max_calls, max_tokens
        self.used_calls, self.used_tokens = 0, 0
        self.per_call_tokens, self.reservation = per_call_tokens, None
        self.on_change = on_change

    def state(self):
        return {**self.summary(), "per_call_tokens": self.per_call_tokens,
                "reservation": list(self.reservation) if self.reservation is not None else None}

    def restore(self, state):
        require(isinstance(state, dict), "INVALID_BUDGET_CHECKPOINT")
        require((state.get("max_calls"), state.get("max_tokens"), state.get("per_call_tokens")) ==
                (self.max_calls, self.max_tokens, self.per_call_tokens), "BUDGET_CONFIGURATION_CHANGED")
        require(all(type(state.get(k)) is int and state[k] >= 0
                    for k in ("charged_calls", "charged_tokens")), "INVALID_BUDGET_CHECKPOINT")
        reservation = state.get("reservation")
        require(reservation is None or (isinstance(reservation, list) and len(reservation) == 2
                and all(type(v) is int and v >= 0 for v in reservation)
                and reservation[0] <= state["charged_calls"] and reservation[1] <= state["charged_tokens"]),
                "INVALID_BUDGET_RESERVATION")
        self.used_calls, self.used_tokens = state["charged_calls"], state["charged_tokens"]
        self.reservation = tuple(reservation) if reservation is not None else None

    def _persist(self):
        if self.on_change is not None:
            self.on_change(self.state())

    def reserve(self, *, calls=1, tokens=None):
        require(self.reservation is None, "unreconciled evaluation budget reservation")
        tokens = self.per_call_tokens if tokens is None else tokens
        require(type(calls) is int and calls >= 0 and type(tokens) is int and tokens >= 0,
                "nonnegative integer reservation required")
        require(self.used_calls + calls <= self.max_calls and self.used_tokens + tokens <= self.max_tokens, "EVALUATION_BUDGET_EXCEEDED")
        self.used_calls += calls
        self.used_tokens += tokens
        self.reservation = (calls, tokens)
        self._persist()  # Must succeed before a provider/network request starts.

    def before_call(self, *, estimated_input_tokens=0):
        require(type(estimated_input_tokens) is int and estimated_input_tokens >= 0,
                "nonnegative integer input estimate required")
        # Prior failed attempts without usage retain the full upper bound.
        self.reservation = None
        # Production adapters estimate text length / 2. Eight bytes per estimated
        # token conservatively covers UTF-8 input; output is capped by collector.
        self.reserve(tokens=self.per_call_tokens + estimated_input_tokens * 8 + 1024)

    def reconcile(self, *, calls, tokens):
        require(self.reservation is not None, "missing evaluation budget reservation")
        require(type(calls) is int and calls >= 0, "nonnegative integer call usage required")
        reserved_calls, reserved_tokens = self.reservation
        self.used_calls += calls - reserved_calls
        # Untrusted provider telemetry may be absent or malformed. Either case
        # retains the conservative token charge instead of releasing it.
        if type(tokens) is int and tokens >= 0:
            self.used_tokens += tokens - reserved_tokens
        self.reservation = None
        self._persist()
        require(self.used_calls <= self.max_calls and self.used_tokens <= self.max_tokens, "EVALUATION_BUDGET_EXCEEDED")

    def after_call(self, *, input_tokens, output_tokens):
        if self.reservation is not None:
            known = all(type(value) is int and value >= 0 for value in (input_tokens, output_tokens))
            self.reconcile(calls=1, tokens=input_tokens + output_tokens if known else None)

    def summary(self):
        return {"max_calls": self.max_calls, "max_tokens": self.max_tokens,
                "charged_calls": self.used_calls, "charged_tokens": self.used_tokens}
