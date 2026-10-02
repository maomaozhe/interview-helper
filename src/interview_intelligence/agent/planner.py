"""Bounded semantic query planning; filters and tool permissions remain in code."""
from contextlib import nullcontext
import json
import time

from pydantic import Field, model_validator

from interview_intelligence.contracts import StrictModel
from interview_intelligence.resources import resource_path


class SearchPlan(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    alternatives: list[str] = Field(max_length=2)
    needs_clarification: bool
    clarification: str | None

    @property
    def retrieval_query(self):
        return " ".join(dict.fromkeys([self.query, *self.alternatives]))

    @model_validator(mode="after")
    def bounded_plan(self):
        if len(self.retrieval_query) > 500 or any(not value.strip() for value in self.alternatives):
            raise ValueError("planned retrieval query must be nonempty and at most 500 characters")
        if self.needs_clarification and not self.clarification:
            raise ValueError("clarification text is required")
        return self


class SearchPlanner:
    version = "search_plan_v1"

    def __init__(self, *, client, model, budget=None, call_gate=None, on_call=None):
        self.client, self.model = client, model
        self.budget, self.call_gate, self.on_call = budget, call_gate, on_call
        self.prompt = resource_path("prompts/search_plan_v1.md").read_text(encoding="utf-8")

    def plan(self, message, filters):
        if self.budget:
            self.budget.before_call(estimated_input_tokens=(len(message) + len(self.prompt)) // 2)
        started, response, error = time.perf_counter(), None, None
        try:
            with self.call_gate.call() if self.call_gate else nullcontext():
                response = self.client.chat.completions.create(model=self.model, temperature=0,
                    messages=[{"role": "system", "content": self.prompt}, {"role": "user", "content":
                        json.dumps({"message": message, "explicit_filters": filters.model_dump(mode="json")}, ensure_ascii=False)}],
                    response_format={"type": "json_schema", "json_schema": {"name": self.version,
                        "strict": True, "schema": SearchPlan.model_json_schema()}})
            if getattr(response.choices[0], "finish_reason", None) == "length":
                raise ValueError("MODEL_OUTPUT_TRUNCATED")
            return SearchPlan.model_validate_json(response.choices[0].message.content)
        except Exception as failure:
            error = failure
            raise
        finally:
            usage = getattr(response, "usage", None)
            if self.budget:
                self.budget.after_call(input_tokens=getattr(usage, "prompt_tokens", None),
                                       output_tokens=getattr(usage, "completion_tokens", None))
            if self.on_call:
                self.on_call({"operation_type": "SEARCH_PLAN", "model": self.model,
                    "model_revision": getattr(response, "model", None), "prompt_version": self.version,
                    "input_tokens": getattr(usage, "prompt_tokens", None),
                    "output_tokens": getattr(usage, "completion_tokens", None),
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "status": "FAILED" if error else "SUCCEEDED", "retry_count": 0,
                    "error_code": type(error).__name__ if error else None})
