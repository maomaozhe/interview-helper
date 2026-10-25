"""Whole-request limits and independently pinned runtime proof must gate HTTP calls."""
from copy import deepcopy
import json

import httpx
import pytest

from eval.budget import EvaluationBudget
from eval.collect import HttpQueryCollector
from eval.common import digest, write_json
from eval.runtime_identity import runtime_fingerprint
from eval.validate_gold import GoldValidationError


def context():
    return {"evaluation_host": True, "user_id": "eval-budget", "snapshot": {"corpus_revision": 1},
            "state_count": 0, "event_count": 0, "conversation_count": 0, "states": [], "write_events": [],
            "model_calls": [], "model_gate_shared": True, "query_planning_max_calls": 4,
            "request_max_model_calls": 6, "request_max_tokens": 96000}


def proof_context():
    result = context()
    def identity(names):
        files = {name: digest(name.encode()) for name in names}
        return {"schema": "runtime_file_identity_v1", "files": files, "sha256": digest(files)}
    def process(pid):
        return {"pid": pid, "start_ticks": 10, "boot_id": "boot", "executable": "/usr/bin/runtime"}
    observed = {"api": {"identity": identity(["application/api.py", "application/config.py", "resources/prompts/p.md"]),
                        "process": process(11)},
                "pi": {"identity": identity(["pi/runtime.mjs", "dependencies/@earendil-works/pi-agent-core/x.js",
                                              "dependencies/@earendil-works/pi-ai/x.js"]), "process": process(22)}}
    lock = {"expected_path": "/deployment/state/model-call.lock"}
    for role, pid in (("api", 11), ("worker", 33)):
        lock[role] = {"path": lock["expected_path"], "device": 1, "inode": 2, "process": process(pid)}
    proof = {"schema": "runtime_deployment_measurement_v1", "method": "independent_process_files",
             "measurement_id": "operator-probe", "measured_at": "2026-10-15", **deepcopy(observed), "lock": lock}
    evidence = {"schema": "runtime_evidence_v1", "measurement": proof, "measurement_sha256": digest(proof),
                "observed": observed, "runtime_fingerprint": runtime_fingerprint(observed), "status": "REMOTE_SOURCE_BOUND"}
    result.update(runtime_evidence=evidence, runtime_fingerprint=evidence["runtime_fingerprint"], model_gate_identity=deepcopy(lock))
    return result


def test_twelve_turn_whole_budget_is_reserved_before_post_and_thirteenth_is_refused():
    base, posts, returned = context(), [], {}
    budget = EvaluationBudget(72, 1152000)
    def handle(request):
        if request.method == "GET":
            result = deepcopy(base)
            key = request.url.params.get("request_id")
            if key in returned:
                result.update(run_status="SUCCEEDED", model_attempts=6, model_calls=[{"id": key + str(i), "input_tokens": 8000,
                    "output_tokens": 8000, "operation_type": "QUERY_PLAN"} for i in range(6)])
            return httpx.Response(200, json=result)
        assert budget.reservation == (6, 96000)
        payload = json.loads(request.content)
        posts.append(payload)
        returned[payload["request_id"]] = True
        return httpx.Response(200, json={"data": [], "meta": {"conversation_id": str(len(posts)), "conversation_version": 1}})
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handle)) as client:
        collector = HttpQueryCollector(client, {"snapshot": base["snapshot"]}, budget)
        for index in range(12):
            assert not collector({"id": str(index), "message": "read"}, [])["failed"]
        result = collector({"id": "over-budget", "message": "read"}, [])
    assert result["failed"] and result["turns"][0]["error_code"] == "EVALUATION_BUDGET_EXCEEDED"
    assert len(posts) == 12 and budget.used_calls == 72 and budget.used_tokens == 1152000


@pytest.mark.parametrize("missing", ["request_max_model_calls", "request_max_tokens", "query_planning_max_calls"])
def test_planning_only_or_missing_whole_request_limits_cannot_start(missing):
    base = context()
    del base[missing]
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=base))) as client:
        with pytest.raises(GoldValidationError, match="WHOLE_REQUEST_LIMITS_REQUIRED"):
            HttpQueryCollector(client, {"snapshot": base["snapshot"]}, EvaluationBudget(72, 1152000))


@pytest.mark.parametrize("after_post", [False, True])
def test_deployment_change_before_or_after_turn_prevents_unverified_continuation(tmp_path, after_post):
    base, posts, calls = proof_context(), [], 0
    proof = tmp_path / "proof.json"
    write_json(proof, base["runtime_evidence"]["measurement"])
    budget = EvaluationBudget(72, 1152000)
    def handle(request):
        nonlocal calls
        if request.method == "GET":
            calls += 1
            result = deepcopy(base)
            if (after_post and posts) or (not after_post and calls >= 3):
                result["model_gate_identity"]["worker"]["inode"] += 1
            return httpx.Response(200, json=result)
        posts.append(request)
        return httpx.Response(200, json={"data": [], "meta": {"conversation_id": "c", "conversation_version": 1}})
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handle)) as client:
        collector = HttpQueryCollector(client, {"snapshot": base["snapshot"]}, budget, deployment_evidence=proof)
        assert collector.context["runtime_verification"] == "REMOTE_SOURCE_BOUND"
        result = collector({"id": "a", "message": "read"}, [])
    assert result["failed"] and len(posts) == int(after_post)
    assert budget.used_calls == (6 if after_post else 0)
    assert budget.used_tokens == (96000 if after_post else 0)
    assert bool(result["telemetry_unavailable"]) is after_post


def test_custom_lock_self_report_without_independent_proof_is_rejected():
    base = proof_context()
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=base))) as client:
        with pytest.raises(GoldValidationError, match="INDEPENDENT_DEPLOYMENT_PROOF_REQUIRED"):
            HttpQueryCollector(client, {"snapshot": base["snapshot"]}, EvaluationBudget(72, 1152000))


@pytest.mark.parametrize("attempts", [None, -1, True, 1.0, 0, 2])
def test_successful_partial_result_without_complete_attempt_ledger_keeps_whole_charge(attempts):
    base, posts = context(), []
    budget = EvaluationBudget(72, 1152000)
    def handle(request):
        if request.method == "GET":
            requested = bool(request.url.params.get("request_id"))
            return httpx.Response(200, json={**base, "run_status": "SUCCEEDED" if requested else None,
                "model_attempts": attempts if requested else None,
                "model_calls": [{"id": "persisted-call", "input_tokens": 7, "output_tokens": 5}] if requested else []})
        posts.append(request)
        return httpx.Response(200, json={"data": [], "meta": {"conversation_id": "c", "conversation_version": 1}})
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handle)) as client:
        result = HttpQueryCollector(client, {"snapshot": base["snapshot"]}, budget)(
            {"id": "a", "turns": [{"message": "first"}, {"message": "next"}]}, [])
    assert len(posts) == 1 and result["failed"] and result["telemetry_unavailable"]
    assert result["turns"][0]["error_code"] == "MODEL_CALL_LEDGER_INCOMPLETE"
    assert budget.used_calls == 6 and budget.used_tokens == 96000


@pytest.mark.parametrize("inputs,outputs,operation,charge", [
    (0, 0, "QUERY_PLAN", 0), (3, None, "EMBEDDING", 3),
    (None, 5, "QUERY_PLAN", 96000), (3, None, "QUERY_PLAN", 96000),
    (-1, 5, "QUERY_PLAN", 96000), (3, False, "EMBEDDING", 96000),
])
def test_complete_call_ledger_does_not_make_unknown_usage_known(inputs, outputs, operation, charge):
    base = context()
    budget = EvaluationBudget(72, 1152000)
    def handle(request):
        if request.method == "GET":
            requested = bool(request.url.params.get("request_id"))
            return httpx.Response(200, json={**base, "run_status": "SUCCEEDED" if requested else None,
                "model_attempts": 1 if requested else None, "model_calls": [{"id": "paid-call",
                    "input_tokens": inputs, "output_tokens": outputs, "operation_type": operation}] if requested else []})
        return httpx.Response(200, json={"data": [], "meta": {"conversation_id": "c", "conversation_version": 1}})
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handle)) as client:
        result = HttpQueryCollector(client, {"snapshot": base["snapshot"]}, budget)({"id": "a", "message": "read"}, [])
    assert not result["failed"] and not result["telemetry_unavailable"]
    assert budget.used_calls == 1 and budget.used_tokens == charge


def test_successful_zero_attempt_receipt_can_prove_no_calls():
    base = context()
    base.update(run_status="SUCCEEDED", model_attempts=0)
    assert HttpQueryCollector.call_ledger_complete(base)
    base["model_calls"] = [{"id": "unexpected"}]
    assert not HttpQueryCollector.call_ledger_complete(base)
