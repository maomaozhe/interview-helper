"""Read-only persisted-telemetry reconciliation; never rescore or replay queries."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import httpx

from eval.common import digest, inside, read_json, read_jsonl, write_json
from eval.snapshot import assert_snapshot
from eval.validate_gold import require
from evals.agent_gold.user_query_source_20261015 import validate_dataset


REQUEST_ID = re.compile(r"^eval-([0-9a-f]{32})-([0-9a-f]{12})-(\d+)$")
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "SKIPPED", "SKIPPED_BUDGET", "INTERRUPTED"}


def load_inputs(dataset, source_run):
    bundle = validate_dataset(dataset)
    protocol, receipt = read_json(source_run / "protocol.json"), read_json(source_run / "collection.json")
    require(protocol["gold_sha256"] == receipt["gold_sha256"] == digest(bundle), "AUDIT_GOLD_CHANGED")
    require(digest((source_run / "predictions.jsonl").read_bytes()) == receipt["predictions_sha256"], "AUDIT_PREDICTIONS_CHANGED")
    require(digest(protocol) == receipt["protocol_sha256"], "AUDIT_PROTOCOL_CHANGED")
    require(all(protocol[k] == receipt[k] for k in ("runtime_sha256", "evaluator_sha256")), "AUDIT_COLLECTION_IDENTITY_CHANGED")
    root = Path(protocol["runtime_root"])
    runtime, evaluator = read_json(root / "runtime-seal.json"), read_json(root / "evaluator-seal.json")
    require(digest(runtime["files"]) == runtime["runtime_sha256"] == protocol["runtime_sha256"], "AUDIT_RUNTIME_CHANGED")
    require(digest(evaluator["files"]) == evaluator["sha256"] == protocol["evaluator_sha256"], "AUDIT_EVALUATOR_CHANGED")
    for seal, folder in ((runtime, "runtime"), (evaluator, "evaluator")):
        for name, expected in seal["files"].items():
            require(digest(inside(root / folder, name).read_bytes()) == expected, "AUDIT_SEALED_FILE_CHANGED")
    before, after = read_json(source_run / "before.json"), read_json(source_run / "after.json")
    for context in (before, after):
        require(context["user_id"] == runtime["actor"], "AUDIT_ACTOR_CHANGED")
        assert_snapshot(bundle["manifest"]["snapshot"], context["snapshot"])
    require(after["state_count"] == after["event_count"] == 0, "AUDIT_REVIEW_WRITES_PRESENT")
    caps = {k: before[k] for k in ("query_max_model_calls", "query_max_tokens")}
    require(all(type(v) is int and v > 0 for v in caps.values()), "AUDIT_QUERY_CAPS_INVALID")
    rows = read_jsonl(source_run / "predictions.jsonl")
    require(len(rows) == len(bundle["cases"]) == bundle["manifest"]["case_count"]
            and [r["id"] for r in rows] == [c["id"] for c in bundle["cases"]], "AUDIT_FULL_SUITE_REQUIRED")
    require(sum(len(r["turns"]) for r in rows) == bundle["manifest"]["turn_count"], "AUDIT_FULL_TURNS_REQUIRED")
    for case, row in zip(bundle["cases"], rows):
        require(len(case["turns"]) == len(row["turns"]), "AUDIT_FULL_TURNS_REQUIRED")
        require(row["actor"] == runtime["actor"], "AUDIT_ACTOR_CHANGED")
        assert_snapshot(bundle["manifest"]["snapshot"], row["snapshot"])
        require(row["model_calls"] == [c for turn in row["turns"] for c in turn["model_calls"]], "AUDIT_SAVED_TELEMETRY_INCONSISTENT")
    return bundle, protocol, receipt, rows, runtime["actor"], caps


def request_plan(bundle, rows, protocol):
    boundary = (protocol.get("resume") or {}).get("completed_cases", 0)
    require(type(boundary) is int and 0 <= boundary < len(rows), "AUDIT_BAD_PHASE_BOUNDARY")
    phases, turns = {}, []
    for index, (case, row) in enumerate(zip(bundle["cases"], rows)):
        phase = "checkpoint" if index < boundary else "current"
        info = phases.setdefault(phase, {"prefixes": set(), "proof_request_ids": []})
        for number, (gold, saved) in enumerate(zip(case["turns"], row["turns"])):
            meta = saved.get("result", {}).get("meta", {})
            entry = {"case_id": case["id"], "turn_index": number, "phase": phase,
                "original_failed": saved.get("failed", False), "original_status": saved.get("run_status"),
                "original_error_code": saved.get("error_code"), "saved_calls": saved["model_calls"],
                "request_id": meta.get("request_id"), "request_id_origin": "saved_meta"}
            if saved.get("error_code") == "PREVIOUS_TURN_FAILED":
                require(not entry["saved_calls"] and not entry["request_id"], "AUDIT_SKIPPED_TURN_HAS_CALLS")
                entry.update(disposition="NOT_SENT_PREVIOUS_TURN_FAILED", request_id_origin="not_sent")
            elif entry["request_id"]:
                match = REQUEST_ID.fullmatch(entry["request_id"])
                require(match and match[2] == digest(case["id"])[:12]
                        and (int(match[3]) == number or gold.get("replay_previous")), "AUDIT_REQUEST_ID_MISMATCH")
                info["prefixes"].add(match[1])
                if not saved.get("failed") and saved.get("run_status") == "SUCCEEDED":
                    info["proof_request_ids"].append(entry["request_id"])
            turns.append(entry)
    for phase, info in phases.items():
        require(len(info["prefixes"]) <= 1, "AUDIT_MULTIPLE_PHASE_PREFIXES")
        pending = [t for t in turns if t["phase"] == phase and not t.get("disposition") and not t["request_id"]]
        if pending:
            require(len(info["prefixes"]) == 1 and info["proof_request_ids"], "AUDIT_PHASE_PREFIX_UNPROVEN")
            prefix = next(iter(info["prefixes"]))
            for entry in pending:
                entry["request_id"] = "eval-" + prefix + "-" + digest(entry["case_id"])[:12] + "-" + str(entry["turn_index"])
                entry["request_id_origin"] = "generator_from_phase_success_meta"
        info["prefixes"] = sorted(info["prefixes"])
    return turns, phases


def call_tokens(call):
    incoming, outgoing = call.get("input_tokens"), call.get("output_tokens")
    if call.get("operation_type") == "EMBEDDING" and outgoing is None:
        outgoing = 0
    return incoming + outgoing if (type(incoming) is int and incoming >= 0
        and type(outgoing) is int and outgoing >= 0) else None


def inspect_requests(client, turns, phases, actor, snapshot, caps):
    grouped, skipped = {}, []
    for turn in turns:
        if turn.get("disposition"):
            skipped.append({**turn, "provider_zero_calls_verified": False})
            continue
        grouped.setdefault(turn["request_id"], []).append(turn)
    requests, all_calls, owners = [], {}, {}
    for request_id, originals in grouped.items():
        persisted, status, lookup_error = [], None, None
        try:
            response = client.get("/api/evaluation/context", params={"request_id": request_id})
            response.raise_for_status()
            current = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as error:
            lookup_error = type(error).__name__
        else:
            require(current.get("user_id") == actor and current.get("evaluation_host") is True, "AUDIT_LIVE_ACTOR_CHANGED")
            assert_snapshot(snapshot, current["snapshot"])
            require(all(current.get(k) == v for k, v in caps.items()), "AUDIT_LIVE_CAPS_CHANGED")
            status, persisted = current.get("run_status"), current.get("model_calls", [])
            require(isinstance(persisted, list), "AUDIT_LIVE_CALLS_INVALID")
        by_id = {call["id"]: call for call in persisted}
        require(len(by_id) == len(persisted), "AUDIT_DUPLICATE_PERSISTED_CALL")
        saved, unverified = {}, []
        for original in originals:
            for call in original["saved_calls"]:
                require(call["id"] not in saved or saved[call["id"]] == call, "AUDIT_SAVED_CALL_CONFLICT")
                saved[call["id"]] = call
        for identity, call in saved.items():
            if identity not in by_id:
                require(status not in TERMINAL, "AUDIT_SAVED_CALL_NOT_PERSISTED")
                unverified.append(identity)
            else:
                require(by_id[identity] == call, "AUDIT_SAVED_CALL_CHANGED")
        for identity, call in by_id.items():
            require(identity not in owners or owners[identity] == request_id, "AUDIT_CALL_REUSED_ACROSS_REQUESTS")
            owners[identity], all_calls[identity] = request_id, call
        usage_complete = status in TERMINAL and all(call_tokens(call) is not None for call in persisted)
        known = sum(call_tokens(call) or 0 for call in persisted)
        requests.append({"request_id": request_id, "original_turns": originals, "persisted_status": status,
            "lookup_error": lookup_error, "persisted_calls": persisted, "unverified_saved_call_ids": unverified,
            "extra_persisted_call_ids": sorted(set(by_id) - set(saved)),
            "late_completed": status == "SUCCEEDED" and any(t["original_failed"] for t in originals),
            "known_usage_tokens": known, "usage_complete": usage_complete,
            "unknown_usage_reserved_tokens": 0 if usage_complete else caps["query_max_tokens"],
            "conservative_calls": len(persisted) if status in TERMINAL else caps["query_max_model_calls"]})
    for phase, info in phases.items():
        if any(t["phase"] == phase and t["request_id_origin"] == "generator_from_phase_success_meta" for t in turns):
            require(any(r["request_id"] in info["proof_request_ids"]
                and r["persisted_status"] == "SUCCEEDED" for r in requests), "AUDIT_PHASE_SUCCESS_NOT_PERSISTED")
    summary = {"requests_checked": len(requests), "skipped_unsent_turns": len(skipped),
        "unique_persisted_paid_call_ids": len(all_calls),
        "complete_known_call_tokens": sum(call_tokens(c) or 0 for c in all_calls.values()),
        "unknown_usage_call_ids": sorted(identity for identity, c in all_calls.items() if call_tokens(c) is None),
        "unknown_usage_requests": sum(not r["usage_complete"] for r in requests),
        "unknown_usage_reserved_tokens": sum(r["unknown_usage_reserved_tokens"] for r in requests),
        "conservative_request_token_charge": sum(r["known_usage_tokens"] if r["usage_complete"] else
                                                  r["unknown_usage_reserved_tokens"] for r in requests),
        "conservative_request_call_charge": sum(r["conservative_calls"] for r in requests),
        "extra_persisted_call_ids": sorted({i for r in requests for i in r["extra_persisted_call_ids"]}),
        "unverified_saved_call_ids": sorted({i for r in requests for i in r["unverified_saved_call_ids"]}),
        "saved_call_identity_verified": not any(r["unverified_saved_call_ids"] for r in requests),
        "late_completed_requests": sum(r["late_completed"] for r in requests),
        "persisted_status_counts": dict(Counter(r["persisted_status"] or "UNKNOWN" for r in requests)),
        "new_model_calls": 0, "billing_certified": False}
    return {"summary": summary, "requests": requests, "skipped_turns": skipped, "phases": phases}


def audit(dataset, source_run, output, *, client=None):
    dataset, source_run, output = Path(dataset), Path(source_run), Path(output)
    require(not output.exists(), "TELEMETRY_AUDIT_ALREADY_EXISTS")
    bundle, protocol, receipt, rows, actor, caps = load_inputs(dataset, source_run)
    turns, phases = request_plan(bundle, rows, protocol)
    def inspect(c): return inspect_requests(c, turns, phases, actor, bundle["manifest"]["snapshot"], caps)
    if client is None:
        with httpx.Client(base_url=protocol["base_url"], trust_env=False, timeout=30) as connection:
            report = inspect(connection)
    else:
        report = inspect(client)
    report.update(schema="user_query_telemetry_audit_v1", audited_at=datetime.now(timezone.utc).isoformat(),
        original_budget=receipt["budget"], bindings={"source_run": str(source_run), "gold_sha256": digest(bundle),
            "predictions_sha256": receipt["predictions_sha256"], "protocol_sha256": digest(protocol),
            "receipt_sha256": digest(receipt), "audit_code_sha256": digest(Path(__file__).read_bytes())},
        note="GET-only telemetry audit, not a provider bill or a new model score. Original predictions, failures, checks and budget are unchanged. Unsent skipped turns are not verified zero-call provider requests.")
    output.mkdir(parents=True)
    write_json(output / "telemetry-audit.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("data/gold/user-query-source-reviewed-20261015-v1"))
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.dataset, args.source_run, args.output)["summary"], ensure_ascii=False, indent=2))
