"""Frozen development cases, real HTTP receipts; never report global recall."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

if __package__:
    from .receipt_io import atomic_write_json
else:
    from receipt_io import atomic_write_json

CASES = [
    {"id": "ambiguous-design", "turns": [
        {"message": "场景设计题", "expected": "CLARIFY"},
        {"message": "Agent 应用设计", "expected": "SEARCH"}]},
    {"id": "agent-design-feedback", "turns": [
        {"message": "agent场景设计题", "expected": "SEARCH"}],
     "known_negative_ids": ["6ade363b-c9bd-4986-8038-96e33731cb21", "7e998020-a3cd-47ea-837b-67fd7790ec11", "f1d23e34-3e42-4798-b9cf-1668a7685523"]},
    {"id": "harness-feedback", "turns": [
        {"message": "harness相关问题，返回15篇", "expected": "SEARCH"}],
     "known_positive_ids": ["642cb5b2-ef14-4251-a3ef-4e9aab3bc676", "857e6f16-8f16-4c21-94d5-8572b27e4829", "84f72bb6-2e3a-4c23-aadb-218b9de6250e"]},
    {"id": "business-specific", "turns": [
        {"message": "高并发秒杀业务系统怎么设计，有哪些真实面试题？", "expected": "SEARCH"}]},
    {"id": "production-specific", "turns": [
        {"message": "线上服务内存一直涨，如何定位且不影响业务，有哪些真实问法？", "expected": "SEARCH"}]},
    {"id": "no-sample", "turns": [
        {"message": "拓扑量子纠错码解码器设计的面试题", "expected": "SEARCH"}], "expected_empty": True},
    {"id": "context-and-topic-reset", "turns": [
        {"message": "帮我找 Agent 的上下文记忆设计面试题", "expected": "SEARCH"},
        {"message": "这类场景设计题再找一些", "expected": "SEARCH"},
        {"message": "现在改看秒杀系统设计，不限 AI", "expected": "SEARCH"}]},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["before", "after"], required=True)
    parser.add_argument("--label", default=None)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--case", choices=[case["id"] for case in CASES], action="append",
                        help="Repeat selected frozen cases; receipts explicitly record subset coverage.")
    parser.add_argument("--output", type=Path, default=Path("data/reports/conversation-quality-20261008"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    protocol = {"version": "conversation_quality_v1", "reviewer": "development_agent", "human_verified": False,
                "cases": CASES, "limitations": "Feedback regression and development challenge; incomplete relevance pool; no global recall."}
    raw = json.dumps(protocol, ensure_ascii=False, indent=2)
    path = args.output / "protocol.json"
    if path.exists() and path.read_text(encoding="utf-8") != raw:
        raise SystemExit("Protocol mismatch; use a new output directory")
    if not path.exists():
        try:
            atomic_write_json(path, protocol, exclusive=True)
        except FileExistsError:
            if path.read_text(encoding="utf-8") != raw:
                raise SystemExit("Protocol mismatch; use a new output directory") from None
    target = args.output / f"{args.label or args.stage}.json"
    if target.exists():
        raise SystemExit("Refuse to overwrite real receipts")
    selected = [case for case in CASES if not args.case or case["id"] in args.case]
    result = {"stage": args.stage, "started_at": datetime.now(timezone.utc).isoformat(),
              "selected_case_ids": [case["id"] for case in selected], "full_protocol_run": not bool(args.case),
              "protocol_sha256": hashlib.sha256(raw.encode()).hexdigest(), "cases": []}
    try:
        atomic_write_json(target, result, exclusive=True)
    except FileExistsError:
        raise SystemExit("Refuse to overwrite real receipts") from None
    with httpx.Client(base_url=args.url, timeout=100, trust_env=False) as client:
        result["health"] = client.get("/api/health").json()
        for case in selected:
            saved = {"id": case["id"], "turns": []}
            result["cases"].append(saved)
            conversation, version = None, None
            for turn in case["turns"]:
                body = {"message": turn["message"], "request_id": f"dev-eval-{args.stage}-{uuid4()}",
                        "conversation_id": conversation, "expected_version": version}
                started = time.perf_counter()
                try:
                    response = client.post("/api/query", json=body)
                    receipt = {"request": body, "expected": turn["expected"], "status_code": response.status_code,
                               "elapsed_ms": round((time.perf_counter() - started) * 1000), "response": response.json()}
                    if response.is_success:
                        meta = receipt["response"]["meta"]
                        conversation, version = meta["conversation_id"], meta["conversation_version"]
                        receipt["action"] = meta["planning"]["spec"]["action"]
                        receipt["action_pass"] = receipt["action"] == turn["expected"]
                        ids = {r["canonical_question_id"] for r in receipt["response"]["data"]}
                        receipt["known_negative_hits"] = sorted(ids.intersection(case.get("known_negative_ids", [])))
                        receipt["known_positive_hits"] = sorted(ids.intersection(case.get("known_positive_ids", [])))
                except Exception as error:
                    receipt = {"request": body, "expected": turn["expected"], "error": type(error).__name__,
                               "elapsed_ms": round((time.perf_counter() - started) * 1000)}
                saved["turns"].append(receipt)
                atomic_write_json(target, result)
                print(args.stage, case["id"], receipt.get("action", receipt.get("error", receipt.get("status_code"))), receipt["elapsed_ms"], flush=True)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(target, result)


if __name__ == "__main__":
    main()
