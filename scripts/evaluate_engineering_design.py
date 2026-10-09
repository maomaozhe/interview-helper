"""Replay engineering design scope regressions through the real chat API.

Each output directory is a new attempt. Credentials remain in an ignored file;
full requests, results and events are saved so semantic judgments can be audited.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx

from receipt_io import atomic_write_json


CASES = [
    {"id": "bare-engineering", "turns": ["工程系统设计题"], "scope": "broad"},
    {"id": "example-is-category", "turns": ["我要的是工程系统设计题，类似设计一个排行榜这种"], "scope": "broad"},
    {"id": "example-coverage", "turns": ["我要的是工程系统设计题，类似设计一个排行榜这种"], "scope": "broad", "page_size": 50},
    {"id": "topic-correction", "turns": ["agent场景相关的系统设计题", "工程系统设计题", "我要的是工程系统设计题，类似设计一个排行榜这种"], "scope": "broad", "skip_semantic_turns": [0]},
    {"id": "leaderboard-only", "turns": ["只要设计排行榜系统的面试题，其他系统不要"], "scope": "narrow"},
    {"id": "design-answer", "turns": ["解释一下如何设计一个排行榜系统"], "scope": "answer", "expected_intent": "ANSWER"},
    {"id": "agent-guard", "turns": ["帮我找 Agent 应用设计的真实面试题"], "scope": "agent"},
    {"id": "code-guard", "turns": ["工程代码题，手写线程安全 LRU 缓存有哪些面试题"], "scope": "code"},
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--access-file", type=Path)
    parser.add_argument("--url")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", action="append", choices=[c["id"] for c in CASES])
    parser.add_argument("--legacy-conversation", type=Path,
                        help="Existing isolated evaluation actor conversation and version")
    parser.add_argument("--legacy-only", action="store_true", help="Run only the supplied fresh legacy fixture")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Use a new output directory; previous attempts are preserved")
    args.output.mkdir(parents=True)
    selected = [case for case in CASES if not args.case or case["id"] in args.case]
    if args.legacy_only:
        if not args.legacy_conversation:
            parser.error("--legacy-only requires --legacy-conversation")
        selected = []
    if args.legacy_conversation:
        legacy = json.loads(args.legacy_conversation.read_text(encoding="utf-8"))
        selected.append({"id": "legacy-state-correction", "scope": "broad",
                         "turns": ["我要的是工程系统设计题，类似设计一个排行榜这种"],
                         "conversation": legacy})
    access = json.loads(args.access_file.read_text(encoding="utf-8")) if args.access_file else {}
    url = args.url or access.get("url")
    if not url:
        parser.error("--url or --access-file required")
    auth = (access["username"], access["password"]) if access else None
    protocol = {"version": "engineering_design_scope_v1", "cases": CASES,
                "reference_is_exhaustive_corpus_gold": False, "human_verified": False}
    protocol_bytes = json.dumps(protocol, ensure_ascii=False, indent=2).encode()
    (args.output / "protocol.json").write_bytes(protocol_bytes)
    report = {"url": url, "started_at": datetime.now(timezone.utc).isoformat(),
              "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(), "cases": []}
    target = args.output / "receipts.json"
    atomic_write_json(target, report, exclusive=True)
    # SSH forwarding can keep a locally-open connection after Uvicorn has closed
    # its idle peer. Open a new connection for each request instead of reusing
    # that socket; retries cover connection establishment only, never POST replay.
    limits = httpx.Limits(max_connections=5, max_keepalive_connections=0)
    transport = httpx.HTTPTransport(retries=2, limits=limits)
    report["transport"] = {"keepalive_connections": 0, "connection_retries": 2}
    with httpx.Client(base_url=url, auth=auth, trust_env=False, timeout=180,
                      headers={"Connection": "close"}, transport=transport) as client:
        report["health_before"] = client.get("/api/health").raise_for_status().json()["data"]
        for case in selected:
            saved = {"id": case["id"], "scope": case["scope"], "turns": []}
            report["cases"].append(saved)
            try:
                conversation = case.get("conversation") or client.post("/api/conversations").raise_for_status().json()["data"]
            except (httpx.HTTPError, ValueError, KeyError) as error:
                saved["error"] = type(error).__name__
                saved["error_stage"] = "create_conversation"
                atomic_write_json(target, report)
                print(json.dumps({"case": case["id"], "error": type(error).__name__,
                                  "stage": "create_conversation"}), flush=True)
                continue
            for index, message in enumerate(case["turns"]):
                body = {"message": message, "request_id": "engineering-eval-" + str(uuid4()),
                        "expected_version": conversation["version"], "page_size": case.get("page_size", 20)}
                turn = {"request": body, "events": [], "semantic_evaluation": index not in case.get("skip_semantic_turns", [])}
                saved["turns"].append(turn)
                started = time.monotonic()
                try:
                    response = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", json=body)
                    turn["accepted_status"] = response.status_code
                    turn["accepted"] = response.raise_for_status().json()["data"]
                    atomic_write_json(target, report)
                    with client.stream("GET", f"/api/runs/{turn['accepted']['run_id']}/events") as stream:
                        stream.raise_for_status()
                        turn["content_type"] = stream.headers.get("content-type")
                        for line in stream.iter_lines():
                            if line:
                                turn["events"].append(line)
                    turn["run"] = client.get(f"/api/runs/{turn['accepted']['run_id']}").raise_for_status().json()["data"]
                    conversation = client.get(f"/api/conversations/{conversation['conversation_id']}").raise_for_status().json()["data"]
                    result = turn["run"].get("result") or {}
                    turn["intent_pass"] = result.get("intent") == case.get("expected_intent", "SEARCH")
                    print(json.dumps({"case": case["id"], "turn": index, "intent": result.get("intent"),
                                      "status": turn["run"]["status"], "returned": len(result.get("facts", {}).get("data", [])),
                                      "seconds": round(time.monotonic() - started, 2)}, ensure_ascii=False), flush=True)
                except (httpx.HTTPError, ValueError, KeyError) as error:
                    turn["error"] = type(error).__name__
                    print(json.dumps({"case": case["id"], "error": type(error).__name__}), flush=True)
                turn["elapsed_seconds"] = round(time.monotonic() - started, 2)
                atomic_write_json(target, report)
                if "error" in turn:
                    break
        report["health_after"] = client.get("/api/health").raise_for_status().json()["data"]
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(target, report)
    print(str(target), flush=True)


if __name__ == "__main__":
    main()
