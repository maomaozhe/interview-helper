"""Validate partial query-plan expectations without executing or rewriting them."""
import json

from pydantic import TypeAdapter, ValidationError

from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.contracts import FilterSpec


def query_reference_issues(cases):
    issues = []
    for case in cases:
        for index, turn in enumerate(case.get("turns", [case])):
            plan = turn.get("expected_plan", {})
            filters = {k.removeprefix("filters."): v for k, v in plan.items() if k.startswith("filters.")}
            try:
                FilterSpec.model_validate_json(json.dumps(filters), strict=True)
            except ValidationError as error:
                issues.append({"id": case["id"], "turn": index, "kind": "invalid_filter_reference",
                               "errors": error.errors(include_url=False, include_context=False)})
            for name, value in plan.items():
                if name.startswith("filters."):
                    continue
                if name not in QuerySpec.model_fields:
                    issues.append({"id": case["id"], "turn": index, "kind": "unknown_plan_reference", "field": name})
                    continue
                try:
                    TypeAdapter(QuerySpec.model_fields[name].rebuild_annotation()).validate_json(json.dumps(value), strict=True)
                except ValidationError:
                    issues.append({"id": case["id"], "turn": index, "kind": "invalid_plan_reference", "field": name, "value": value})
    return issues
