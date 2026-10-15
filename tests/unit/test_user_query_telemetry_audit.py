"""GET-only reconciliation may expose missing calls without changing old results."""
from copy import deepcopy

import httpx
import pytest

from eval.common import digest, read_json, write_json, write_jsonl
from eval.validate_gold import GoldValidationError
from evals.project_quality import user_query_telemetry_audit as audit


@pytest.fixture
def suite():
    snap = {"corpus_revision": 293, "indexed_revision": 293, "task_annotation_revision": 30, "as_of": "2026-10-05"}
    caps = {"query_max_model_calls": 4, "query_max_tokens": 96000}
    cases = [{"id": "case-" + str(i), "turns": [{"message": "fixture"}] * (2 if i == 1 else 1)} for i in range(3)]
    def rid(i, prefix): return "eval-" + prefix * 32 + "-" + digest(cases[i]["id"])[:12] + "-0"
    def call(identity, incoming=7, outgoing=5):
        return {"id": identity, "operation_type": "QUERY_PLAN", "input_tokens": incoming, "output_tokens": outgoing}
    a, b = call("saved-a"), call("saved-b")
    rows = [
        {"id": cases[0]["id"], "actor": "eval-audit", "snapshot": snap, "model_calls": [a], "turns": [
            {"failed": False, "run_status": "SUCCEEDED", "model_calls": [a], "result": {"meta": {"request_id": rid(0, 'a')}}}]},
        {"id": cases[1]["id"], "actor": "eval-audit", "snapshot": snap, "model_calls": [], "turns": [
            {"failed": True, "run_status": "FAILED", "error_code": "HTTPStatusError", "model_calls": [], "result": {}},
            {"failed": True, "error_code": "PREVIOUS_TURN_FAILED", "model_calls": [], "result": {}}]},
        {"id": cases[2]["id"], "actor": "eval-audit", "snapshot": snap, "model_calls": [b], "turns": [
            {"failed": False, "run_status": "SUCCEEDED", "model_calls": [b], "result": {"meta": {"request_id": rid(2, 'f')}}}]},
    ]
    bundle = {"manifest": {"snapshot": snap, "case_count": 3, "turn_count": 4}, "cases": cases}
    protocol = {"resume": {"completed_cases": 1}}
    turns, phases = audit.request_plan(bundle, rows, protocol)
    live = {rid(0, 'a'): ("SUCCEEDED", [a]), rid(1, 'f'): ("FAILED", [call("extra-paid", 9, 3)]), rid(2, 'f'): ("SUCCEEDED", [b])}
    return {"bundle": bundle, "rows": rows, "protocol": protocol, "turns": turns, "phases": phases,
            "live": live, "snapshot": snap, "caps": caps}


def probe(fixture):
    gets = []
    def handle(request):
        assert request.method == "GET" and request.url.path == "/api/evaluation/context"
        request_id = request.url.params["request_id"]; gets.append(request_id)
        status, calls = fixture["live"][request_id]
        return httpx.Response(200, json={"user_id": "eval-audit", "evaluation_host": True,
            "snapshot": fixture["snapshot"], **fixture["caps"], "run_status": status, "model_calls": calls})
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handle)) as client:
        result = audit.inspect_requests(client, fixture["turns"], fixture["phases"], "eval-audit", fixture["snapshot"], fixture["caps"])
    return result, gets


def test_missing_meta_id_comes_from_its_own_successful_phase_and_unsent_turn_is_not_probed(suite):
    sent = [t for t in suite["turns"] if t.get("request_id")]
    assert sent[0]["request_id"].startswith("eval-" + "a" * 32)
    assert sent[1]["request_id"].startswith("eval-" + "f" * 32)
    assert sent[1]["request_id_origin"] == "generator_from_phase_success_meta"
    report, gets = probe(suite)
    assert len(gets) == 3 and len(set(gets)) == 3
    assert report["skipped_turns"][0]["provider_zero_calls_verified"] is False
    assert report["summary"]["skipped_unsent_turns"] == 1


def test_previously_overwritten_paid_call_is_reported_without_rewriting_failure(suite):
    report, _ = probe(suite)
    assert report["summary"]["unique_persisted_paid_call_ids"] == 3
    assert report["summary"]["complete_known_call_tokens"] == 36
    assert report["summary"]["extra_persisted_call_ids"] == ["extra-paid"]
    failed = next(r for r in report["requests"] if r["extra_persisted_call_ids"])
    assert failed["original_turns"][0]["original_failed"] is True and failed["persisted_status"] == "FAILED"
    suite["live"][failed["request_id"]] = ("SUCCEEDED", failed["persisted_calls"])
    late, _ = probe(suite)
    assert late["summary"]["late_completed_requests"] == 1
    assert suite["rows"][1]["turns"][0]["failed"] is True


@pytest.mark.parametrize("unknown_status", [False, True])
def test_unknown_usage_or_nonterminal_status_reserves_full_96000_not_zero(suite, unknown_status):
    request_id = next(k for k, v in suite["live"].items() if v[0] == "FAILED")
    if unknown_status:
        suite["live"][request_id] = (None, [])
    else:
        call = deepcopy(suite["live"][request_id][1][0]); call["output_tokens"] = None
        suite["live"][request_id] = ("FAILED", [call])
    report, _ = probe(suite)
    assert report["summary"]["unknown_usage_reserved_tokens"] == 96000
    assert report["summary"]["conservative_request_token_charge"] == 96024
    assert report["summary"]["conservative_request_call_charge"] == (6 if unknown_status else 3)


def test_replayed_request_is_probed_and_counted_once(suite):
    first = suite["rows"][0]["turns"][0]
    suite["bundle"]["cases"][0]["turns"].append({"message": "replay", "replay_previous": True})
    suite["rows"][0]["turns"].append({**deepcopy(first), "model_calls": []})
    suite["turns"], suite["phases"] = audit.request_plan(suite["bundle"], suite["rows"], suite["protocol"])
    report, gets = probe(suite)
    assert len(gets) == report["summary"]["requests_checked"] == 3
    assert report["summary"]["unique_persisted_paid_call_ids"] == 3


@pytest.mark.parametrize("change,error", [("missing", "AUDIT_SAVED_CALL_NOT_PERSISTED"),
    ("changed", "AUDIT_SAVED_CALL_CHANGED"), ("duplicate", "AUDIT_DUPLICATE_PERSISTED_CALL")])
def test_saved_calls_must_match_exact_persisted_evidence(suite, change, error):
    request_id = next(iter(suite["live"]))
    status, calls = deepcopy(suite["live"][request_id])
    if change == "missing": calls = []
    if change == "changed": calls[0]["input_tokens"] += 1
    if change == "duplicate": calls *= 2
    suite["live"][request_id] = (status, calls)
    with pytest.raises(GoldValidationError, match=error): probe(suite)


def test_no_success_evidence_means_no_guessed_phase_prefix(suite):
    suite["rows"][2]["turns"][0]["failed"] = True
    with pytest.raises(GoldValidationError, match="AUDIT_PHASE_PREFIX_UNPROVEN"):
        audit.request_plan(suite["bundle"], suite["rows"], suite["protocol"])


def test_generation_needs_complete_bound_saved_suite_and_refuses_overwrite(suite, tmp_path, monkeypatch):
    root, source = tmp_path / "runtime", tmp_path / "run"
    source.mkdir()
    files = {}
    for folder in ("runtime", "evaluator"):
        path = root / folder / "fixture.py"; path.parent.mkdir(parents=True); path.write_bytes(b"# frozen\n")
        files[folder] = {"fixture.py": digest(path.read_bytes())}
    write_json(root / "runtime-seal.json", {"actor": "eval-audit", "files": files["runtime"], "runtime_sha256": digest(files["runtime"])})
    write_json(root / "evaluator-seal.json", {"files": files["evaluator"], "sha256": digest(files["evaluator"])})
    protocol = {**suite["protocol"], "gold_sha256": digest(suite["bundle"]), "runtime_root": str(root),
        "runtime_sha256": digest(files["runtime"]), "evaluator_sha256": digest(files["evaluator"]), "base_url": "http://fixture"}
    write_json(source / "protocol.json", protocol)
    write_jsonl(source / "predictions.jsonl", suite["rows"])
    receipt = {"gold_sha256": digest(suite["bundle"]), "runtime_sha256": protocol["runtime_sha256"],
        "evaluator_sha256": protocol["evaluator_sha256"], "protocol_sha256": digest(protocol),
        "predictions_sha256": digest((source / "predictions.jsonl").read_bytes()), "budget": {"charged_calls": 2, "charged_tokens": 24}}
    write_json(source / "collection.json", receipt)
    context = {"user_id": "eval-audit", "snapshot": suite["snapshot"], "state_count": 0, "event_count": 0, **suite["caps"]}
    write_json(source / "before.json", context); write_json(source / "after.json", context)
    monkeypatch.setattr(audit, "validate_dataset", lambda _: suite["bundle"])
    loaded = audit.load_inputs(tmp_path / "gold", source)
    assert len(loaded[3]) == 3
    original = {p.name: digest(p.read_bytes()) for p in source.iterdir()}
    def handle(request):
        assert request.method == "GET"
        status, calls = suite["live"][request.url.params["request_id"]]
        return httpx.Response(200, json={**context, "evaluation_host": True, "run_status": status, "model_calls": calls})
    output = tmp_path / "audit"
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handle)) as client:
        report = audit.audit(tmp_path / "gold", source, output, client=client)
    assert report["original_budget"] == receipt["budget"] and report["summary"]["new_model_calls"] == 0
    assert original == {p.name: digest(p.read_bytes()) for p in source.iterdir()}
    with pytest.raises(GoldValidationError, match="TELEMETRY_AUDIT_ALREADY_EXISTS"):
        audit.audit(tmp_path / "gold", source, output)
    write_jsonl(source / "predictions.jsonl", suite["rows"][:-1])
    with pytest.raises(GoldValidationError, match="AUDIT_PREDICTIONS_CHANGED"):
        audit.load_inputs(tmp_path / "gold", source)
    receipt["predictions_sha256"] = digest((source / "predictions.jsonl").read_bytes())
    write_json(source / "collection.json", receipt)
    with pytest.raises(GoldValidationError, match="AUDIT_FULL_SUITE_REQUIRED"):
        audit.load_inputs(tmp_path / "gold", source)
