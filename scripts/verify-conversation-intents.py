"""Replay conversation intent regressions through the authenticated public API.

Credentials stay in the supplied ignored access file. Every invocation writes a
new report; failed attempts and prior releases are never overwritten. The same
async message and SSE endpoints are used by the web client.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--access-file", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--label", default="replay")
    parser.add_argument("--extended", action="store_true")
    args = parser.parse_args()
    access = json.loads(args.access_file.read_text(encoding="utf-8"))
    args.output_directory.mkdir(parents=True, exist_ok=True)
    output = args.output_directory / (
        f"public-{args.label}-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.json")
    proof = {"url": access["url"], "label": args.label,
             "entrypoint": ("public_conversation_messages_and_sse" if access["url"].startswith("https://")
                            else "isolated_staging_conversation_messages_and_sse"),
             "cases": [], "failures": []}

    def save():
        nonlocal output
        temporary = output.with_suffix(f".{uuid4().hex[:8]}.tmp")
        temporary.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
        # Windows scanners may briefly hold a report that is being replaced.
        # Preserve the completed evidence and retry before choosing a new path.
        for attempt in range(6):
            try:
                temporary.replace(output)
                return
            except PermissionError:
                if attempt < 5:
                    time.sleep(0.1 * (2 ** attempt))
        previous = output
        output = output.with_name(f"{output.stem}-save-{uuid4().hex[:8]}.json")
        proof.setdefault("storage_fallbacks", []).append({
            "locked_path": str(previous), "continued_path": str(output)})
        temporary.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(output)

    with httpx.Client(base_url=access["url"], auth=(access["username"], access["password"]),
                      trust_env=False, timeout=140) as client:
        proof["health_before"] = client.get("/api/health").raise_for_status().json()["data"]
        conversation = client.post("/api/conversations").raise_for_status().json()["data"]
        version = conversation["version"]
        proof["conversation_id"] = conversation["conversation_id"]
        save()

        def ask(message, expected_intent, *, count=None, topic=None, company=None,
                unlimited=False, continuation=False, answer_kind=None):
            nonlocal version
            case = {"message": message, "expected_intent": expected_intent, "checks": {}, "events": []}
            proof["cases"].append(case)
            started = time.monotonic()
            accepted = client.post(f"/api/conversations/{conversation['conversation_id']}/messages", json={
                "message": message, "request_id": "conversation-intent-check-" + str(uuid4()),
                "expected_version": version}).raise_for_status().json()["data"]
            case["accepted"] = accepted
            save()
            with client.stream("GET", f"/api/runs/{accepted['run_id']}/events") as stream:
                stream.raise_for_status()
                case["sse_content_type"] = stream.headers.get("content-type", "")
                for line in stream.iter_lines():
                    if line:
                        case["events"].append(line)
            run = client.get(f"/api/runs/{accepted['run_id']}").raise_for_status().json()["data"]
            case["run"] = run
            case["elapsed_seconds"] = round(time.monotonic() - started, 2)
            result = run.get("result") or {}
            if result:
                version = result["conversation_version"]
            else:
                version = client.get(f"/api/conversations/{conversation['conversation_id']}").raise_for_status().json()["data"]["version"]
            spec = result.get("planning", {}).get("spec", {})
            meta = result.get("facts", {}).get("meta", {})
            checks = case["checks"]
            checks["succeeded"] = run["status"] == "SUCCEEDED"
            checks["intent"] = result.get("intent") == expected_intent
            checks["provider_pi"] = result.get("planning", {}).get("provider") == "pi"
            checks["sse"] = case["sse_content_type"].startswith("text/event-stream")
            if count is not None:
                checks["count"] = meta.get("counts", {}).get("canonical_questions") == count
            if topic is not None:
                checks["topic"] = meta.get("applied_filters", {}).get("topic_l1") == topic
            if company is not None:
                checks["company"] = meta.get("applied_filters", {}).get("company") == company
            if unlimited:
                checks["no_implicit_top_n"] = spec.get("top_n") is None
            if continuation:
                checks["can_continue"] = bool(meta.get("pagination", {}).get("next_cursor"))
            if answer_kind:
                checks["answer_kind"] = meta.get("answer_kind") == answer_kind
            if not all(checks.values()):
                proof["failures"].append({"case": len(proof["cases"]),
                                          "checks": [key for key, value in checks.items() if not value]})
            save()
            print(json.dumps({"case": len(proof["cases"]), "message": message,
                              "intent": result.get("intent"), "answer": result.get("answer"),
                              "top_n": spec.get("top_n"), "checks": checks,
                              "seconds": case["elapsed_seconds"]}, ensure_ascii=False), flush=True)
            return result

        ask("Redis 高频问题有哪些？", "LIST", topic="Redis", unlimited=True, continuation=True)
        ask("一共有多少道题", "COUNT", topic="Redis", count=152)
        ask("我说的是redis的高频率问题啊，这个会话不是才说过", "COUNT", topic="Redis", count=152)
        if args.extended:
            ask("那整个题库一共有多少道题？", "COUNT", count=2452)
            ask("我刚才是问 Redis 的数量，不是整个题库", "COUNT", topic="Redis", count=152)
            ask("其中腾讯呢？", "COUNT", topic="Redis", company="腾讯", count=16)
            ask("现在换个话题，列出 Java 高频问题", "LIST", topic="Java", unlimited=True, continuation=True)
            java_count = ask("这些一共有多少题？", "COUNT", topic="Java")
            if java_count.get("facts", {}).get("meta", {}).get("counts", {}).get("canonical_questions", 0) <= 20:
                proof["failures"].append({"case": len(proof["cases"]), "checks": ["java_count_exceeds_page"]})
            ask("下一页", "NEXT", topic="Java")
            ask("解释一下 Redis 为什么快，三个主要原因，200字以内", "ANSWER", answer_kind="EXPLAIN")
            ask("RDB 和 AOF 有什么区别，怎么选？300字以内", "ANSWER", answer_kind="COMPARE")
            ask("刚才的 AOF 重写为什么能缩小文件？200字以内", "ANSWER", answer_kind="EXPLAIN")
        proof["health_after"] = client.get("/api/health").raise_for_status().json()["data"]
        proof["passed"] = not proof["failures"]
        save()
    print(json.dumps({"report": str(output), "passed": proof["passed"],
                      "cases": len(proof["cases"]), "failures": proof["failures"]}), flush=True)
    return 0 if proof["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
