import json
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest

from interview_intelligence.agent.task_annotation import resilient_model_labels
from interview_intelligence.config import Settings
from interview_intelligence.providers.budget import CallBudget
from interview_intelligence.providers.gate import ModelCallGate


def run_provider(monkeypatch, tmp_path, *, max_calls=5):
    requests, telemetry, recoveries = [], [], []
    batch = [SimpleNamespace(id=f"occ-{i}", raw_question=f"原题{i}", source_spans=[])
             for i in range(21)]
    payload = [{"occurrence_id": row.id, "raw_question": row.raw_question} for row in batch]

    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        items = json.loads(body["messages"][1]["content"])
        result = [{"i": row["i"], "f": "VERBAL", "c": "NONE", "p": .99} for row in items]
        if len(items) > 10:
            result[-1]["f"] = "MIXED"
        return httpx.Response(200, json={"model": "test", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps({"items": result})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}})

    client_type = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs:
        client_type(transport=httpx.MockTransport(provider), **kwargs))

    class Audit:
        def begin(self): return nullcontext()
        def add(self, entry): telemetry.append(entry)

    database = SimpleNamespace(session=lambda: nullcontext(Audit()))
    settings = Settings(model_api_key="test", model_base_url="http://model.test/v1",
                        model_lock_path=tmp_path / "gate", model_min_interval_seconds=0)
    budget = CallBudget(max_calls=max_calls, max_tokens=1_000_000)
    call = lambda: resilient_model_labels(database, settings,
        ModelCallGate(settings.model_lock_path, minimum_interval_seconds=0), budget,
        "instructions", payload, batch, "test", on_recovery=recoveries.append)
    return call, requests, telemetry, recoveries, batch, budget


def test_schema_failure_recovers_without_losing_ids_or_failed_call_receipts(monkeypatch, tmp_path):
    call, requests, telemetry, recoveries, batch, budget = run_provider(monkeypatch, tmp_path)
    result = call()
    assert [item.occurrence_id for item in result.items] == [row.id for row in batch]
    assert [len(json.loads(r["messages"][1]["content"])) for r in requests] == [21, 21, 10, 10, 1]
    assert [entry.status for entry in telemetry] == ["FAILED", "FAILED", "SUCCEEDED", "SUCCEEDED", "SUCCEEDED"]
    assert budget.used_calls == 5 and len(recoveries) == 1
    assert recoveries[0]["details"][0]["loc"] == ("items", 20, "f")
    assert all(item.evidence_quote == row.raw_question for item, row in zip(result.items, batch))


def test_recovery_does_not_bypass_budget_or_return_partial_batch(monkeypatch, tmp_path):
    call, requests, telemetry, recoveries, _, budget = run_provider(monkeypatch, tmp_path, max_calls=4)
    with pytest.raises(ValueError, match="BUDGET"):
        call()
    assert len(requests) == budget.used_calls == 4
    assert len(telemetry) == 4 and len(recoveries) == 1
