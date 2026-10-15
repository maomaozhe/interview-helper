"""Checkpoint audit boundaries without any model or service requests."""
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from eval.common import digest, read_json, read_jsonl, write_json, write_jsonl
from eval.validate_gold import GoldValidationError
from evals.project_quality.user_query_alignment import load_checkpoint, verify_checkpoint_requests


@pytest.fixture
def checkpoint_fixture(tmp_path):
    snapshot = {"corpus_revision": 293, "indexed_revision": 293,
                "task_annotation_revision": 30, "as_of": "2026-10-05"}
    bundle = {"manifest": {"snapshot": snapshot}, "cases": [
        {"id": "case-" + str(i), "turns": [{"message": "真实问法" + str(i)}]} for i in range(3)]}
    seal = {"actor": "eval-checkpoint", "runtime_sha256": "api-runtime"}
    old_root = tmp_path / "parent-runtime"
    source = old_root / "evaluator" / "fixture.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"# immutable parent evaluator\n")
    files = {"fixture.py": digest(source.read_bytes())}
    write_json(old_root / "evaluator-seal.json", {"files": files, "sha256": digest(files)})
    parent = tmp_path / "parent"
    parent.mkdir()
    protocol = {"gold_sha256": digest(bundle), "runtime_sha256": seal["runtime_sha256"],
        "base_url": "http://fixture", "max_calls": 160, "max_tokens": 5000000,
        "runtime_root": str(old_root), "evaluator_sha256": digest(files)}
    before = {"user_id": seal["actor"], "snapshot": snapshot, "state_count": 0, "event_count": 0,
              "conversation_count": 0, "query_max_model_calls": 4, "query_max_tokens": 96000}
    write_json(parent / "protocol.json", protocol)
    write_json(parent / "before.json", before)
    rows = []
    for i, case in enumerate(bundle["cases"][:2]):
        call = {"id": "model-call-" + str(i), "operation_type": "QUERY_PLAN", "status": "SUCCEEDED",
                "input_tokens": 10, "output_tokens": 3}
        meta = {"request_id": "eval-" + "a" * 32 + "-" + digest(case["id"])[:12] + "-0",
                "conversation_id": "conversation-" + str(i), "conversation_version": 1, "run_id": "run-" + str(i)}
        turn = {"failed": False, "run_status": "SUCCEEDED", "result": {"meta": meta}, "model_calls": [call]}
        rows.append({"id": case["id"], "actor": seal["actor"], "snapshot": snapshot,
                     "failed": False, "model_calls": [call], "turns": [turn]})
    write_jsonl(parent / "predictions.partial.jsonl", rows)
    def save(updated):
        write_jsonl(parent / "predictions.partial.jsonl", updated)
    def load(**options):
        return load_checkpoint(parent, bundle, seal, **{
            "base_url": "http://fixture", "max_calls": 160, "max_tokens": 5000000, **options})
    return {"parent": parent, "bundle": bundle, "seal": seal, "rows": rows, "save": save, "load": load}


def test_complete_prefix_restores_spend_hashes_and_remaining_suffix(checkpoint_fixture):
    f = checkpoint_fixture
    rows, seen, requests, identity = f["load"]()
    assert rows == f["rows"] and seen == {"model-call-0", "model-call-1"}
    assert identity["charged_calls"] == 2 and identity["charged_tokens"] == 26
    assert identity["completed_cases"] == identity["conversation_count"] == 2
    assert identity["partial_sha256"] == digest((f["parent"] / "predictions.partial.jsonl").read_bytes())
    assert identity["protocol_sha256"] == digest(read_json(f["parent"] / "protocol.json"))
    assert [case["id"] for case in f["bundle"]["cases"][len(rows):]] == ["case-2"]
    assert [request["message"] for request in requests] == ["真实问法0", "真实问法1"]


def test_real_v1_prefix_is_only_two_completed_cases_eight_calls_and_no_replay():
    def require_files(kind, paths):
        missing = sum(not path.is_file() for path in paths)
        if missing:
            pytest.skip(f"private checkpoint inputs unavailable: {kind} ({missing} files)")

    parent = Path("data/reports/user-query-source-reviewed-20261015-v1/run-v1")
    require_files("archived partial, protocol and initial context",
                  [parent / name for name in ("predictions.partial.jsonl", "protocol.json", "before.json")])
    gold = Path("data/gold/user-query-source-reviewed-20261015-v1")
    require_files("v1 Gold metadata", [gold / name for name in ("manifest.json", "freeze.json")])
    require_files("frozen v1 Gold files", [gold / name for name in read_json(gold / "freeze.json")["files"]])
    protocol = read_json(parent / "protocol.json")
    runtime = Path(protocol["runtime_root"])
    require_files("runtime and evaluator seals",
                  [runtime / name for name in ("runtime-seal.json", "evaluator-seal.json")])
    require_files("sealed evaluator files",
                  [runtime / "evaluator" / name for name in read_json(runtime / "evaluator-seal.json")["files"]])
    from evals.agent_gold.user_query_source_20261015 import validate_dataset
    bundle = validate_dataset(gold)
    seal = read_json(runtime / "runtime-seal.json")
    rows, seen, requests, checkpoint = load_checkpoint(parent, bundle, seal,
        base_url=protocol["base_url"], max_calls=160, max_tokens=5000000)
    assert [row["id"] for row in rows] == [case["id"] for case in bundle["cases"][:2]]
    assert len(seen) == checkpoint["charged_calls"] == 8
    assert checkpoint["charged_tokens"] == 76968
    assert checkpoint["conversation_count"] == len(requests) == 2
    assert len(bundle["cases"][len(rows):]) == 22


@pytest.mark.parametrize("change,error", [
    ("gold", "RESUME_GOLD_MISMATCH"), ("runtime", "RESUME_RUNTIME_MISMATCH"),
    ("url", "RESUME_RUNTIME_MISMATCH"), ("budget", "RESUME_BUDGET_CHANGED"),
    ("evaluator", "RESUME_EVALUATOR_CHANGED"), ("actor", "RESUME_ACTOR_MISMATCH"),
])
def test_checkpoint_rejects_changed_parent_identities(checkpoint_fixture, change, error):
    f = checkpoint_fixture
    if change in {"gold", "runtime", "url", "budget"}:
        p = read_json(f["parent"] / "protocol.json")
        p[{"gold": "gold_sha256", "runtime": "runtime_sha256", "url": "base_url", "budget": "max_calls"}[change]] = "changed"
        write_json(f["parent"] / "protocol.json", p)
    elif change == "evaluator":
        p = read_json(f["parent"] / "protocol.json")
        (Path(p["runtime_root"]) / "evaluator" / "fixture.py").write_bytes(b"changed")
    else:
        p = read_json(f["parent"] / "before.json"); p["user_id"] = "eval-other"
        write_json(f["parent"] / "before.json", p)
    with pytest.raises(GoldValidationError, match=error): f["load"]()


@pytest.mark.parametrize("change,error", [
    ("order", "RESUME_CASE_PREFIX_MISMATCH"), ("duplicate_case", "RESUME_CASE_PREFIX_MISMATCH"),
    ("row_actor", "RESUME_UNFINISHED_CASE"), ("failed", "RESUME_UNFINISHED_CASE"),
    ("turns", "RESUME_UNFINISHED_CASE"), ("running", "RESUME_UNFINISHED_TURN"),
    ("duplicate_call", "RESUME_DUPLICATE_MODEL_CALL"), ("flat_calls", "RESUME_TELEMETRY_MISMATCH"),
    ("same_conversation", "RESUME_CASE_CONVERSATION_REUSED"),
])
def test_only_complete_nonduplicate_prefix_can_be_resumed(checkpoint_fixture, change, error):
    f = checkpoint_fixture; rows = deepcopy(f["rows"])
    if change == "order": rows.reverse()
    elif change == "duplicate_case": rows[1]["id"] = rows[0]["id"]
    elif change == "row_actor": rows[0]["actor"] = "eval-other"
    elif change == "failed": rows[0]["failed"] = True
    elif change == "turns": rows[0]["turns"] = []
    elif change == "running": rows[0]["turns"][0]["run_status"] = "RUNNING"
    elif change == "duplicate_call":
        rows[1]["turns"][0]["model_calls"][0]["id"] = rows[0]["model_calls"][0]["id"]
    elif change == "flat_calls": rows[0]["model_calls"] = []
    else: rows[1]["turns"][0]["result"]["meta"]["conversation_id"] = rows[0]["turns"][0]["result"]["meta"]["conversation_id"]
    f["save"](rows)
    with pytest.raises(GoldValidationError, match=error): f["load"]()


@pytest.mark.parametrize("unavailable,expected_calls", [(False, 2), (True, 5)])
def test_unknown_usage_retains_full_original_turn_upper_bound(checkpoint_fixture, unavailable, expected_calls):
    f = checkpoint_fixture; rows = deepcopy(f["rows"])
    rows[0]["turns"][0]["model_calls"][0]["output_tokens"] = None
    rows[0]["turns"][0]["telemetry_unavailable"] = unavailable
    f["save"](rows)
    checkpoint = f["load"]()[3]
    assert checkpoint["charged_tokens"] == 96000 + 13
    assert checkpoint["charged_calls"] == expected_calls


@pytest.mark.parametrize("status,calls_changed,error", [
    ("RUNNING", False, "RESUME_PERSISTED_TURN_NOT_SUCCEEDED"),
    (None, False, "RESUME_PERSISTED_TURN_NOT_SUCCEEDED"),
    ("FAILED", False, "RESUME_PERSISTED_TURN_NOT_SUCCEEDED"),
    ("SUCCEEDED", True, "RESUME_PERSISTED_TELEMETRY_CHANGED"),
    ("SUCCEEDED", False, None),
])
def test_live_checkpoint_verification_is_get_only_and_requires_exact_success(checkpoint_fixture, status, calls_changed, error):
    requests = checkpoint_fixture["load"]()[2]
    by_id = {request["request_id"]: request for request in requests}
    observed = []
    def handle(req):
        assert req.method == "GET"
        observed.append(str(req.url))
        if req.url.path == "/api/evaluation/context":
            saved = by_id[req.url.params["request_id"]]
            calls = [] if calls_changed else saved["model_calls"]
            return httpx.Response(200, json={"run_status": status, "model_calls": calls})
        if req.url.path.startswith("/api/query/receipts/"):
            saved = by_id[req.url.path.rsplit("/", 1)[-1]]
            return httpx.Response(200, json={"data": {**saved, "status": "SUCCEEDED"}})
        saved = next(r for r in requests if r["conversation_id"] == req.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json={"data": {"turns": [{**saved, "status": "SUCCEEDED"}]}})
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handle)) as client:
        if error:
            with pytest.raises(GoldValidationError, match=error): verify_checkpoint_requests(client, requests)
        else:
            verify_checkpoint_requests(client, requests)
            assert len(observed) == 6


@pytest.mark.parametrize("field,value", [
    ("input_tokens", -1), ("output_tokens", -1), ("input_tokens", True),
    ("output_tokens", "3"), ("input_tokens", 10.5),
])
def test_invalid_usage_cannot_reduce_restored_spend(checkpoint_fixture, field, value):
    f = checkpoint_fixture; rows = deepcopy(f["rows"])
    rows[0]["turns"][0]["model_calls"][0][field] = value
    f["save"](rows)
    with pytest.raises(GoldValidationError, match="RESUME_TOKEN_USAGE_INVALID"): f["load"]()


@pytest.mark.parametrize("field,value", [("query_max_tokens", 0), ("query_max_tokens", -1),
    ("query_max_tokens", None), ("query_max_model_calls", True)])
def test_fallback_caps_must_be_known_positive_integers(checkpoint_fixture, field, value):
    f = checkpoint_fixture
    before = read_json(f["parent"] / "before.json"); before[field] = value
    write_json(f["parent"] / "before.json", before)
    with pytest.raises(GoldValidationError): f["load"]()


@pytest.mark.parametrize("change,error", [
    ("request_id", "RESUME_PERSISTED_IDENTITY_CHANGED"),
    ("run_id", "RESUME_PERSISTED_IDENTITY_CHANGED"),
    ("conversation_id", "RESUME_PERSISTED_IDENTITY_CHANGED"),
    ("message", "RESUME_PERSISTED_MESSAGE_CHANGED"),
])
def test_a_different_successful_request_cannot_repair_the_prefix(checkpoint_fixture, change, error):
    requests = checkpoint_fixture["load"]()[2]
    first = requests[0]
    def handle(req):
        assert req.method == "GET"
        if req.url.path == "/api/evaluation/context":
            return httpx.Response(200, json={"run_status": "SUCCEEDED", "model_calls": first["model_calls"]})
        if req.url.path.startswith("/api/query/receipts/"):
            receipt = {**first, "status": "SUCCEEDED"}
            if change != "message": receipt[change] = "other-successful-identity"
            return httpx.Response(200, json={"data": receipt})
        return httpx.Response(200, json={"data": {"turns": [{**first, "status": "SUCCEEDED", "message": "不同的真实问法"}]}})
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GoldValidationError, match=error): verify_checkpoint_requests(client, requests)
