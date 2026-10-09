"""Smoke-test the authenticated HTTPS site, sources, search and Pi/SSE path."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import time
from uuid import uuid4

import httpx


def main(access_file, output, reuse_latest_query=False):
    access = json.loads(access_file.read_text(encoding="utf-8"))
    report = {"url": access["url"], "checks": {}}
    started = time.monotonic()
    with httpx.Client(base_url=access["url"], trust_env=False, timeout=120) as anonymous:
        response = anonymous.get("/")
        assert response.status_code == 401
        report["checks"]["anonymous_access_requires_login"] = True
    with httpx.Client(base_url=access["url"], auth=(access["username"], access["password"]),
                      trust_env=False, timeout=120) as client:
        def get(path, **kwargs):
            response = client.get(path, **kwargs)
            response.raise_for_status()
            return response.json()

        def post(path, body=None):
            response = client.post(path, json=body)
            response.raise_for_status()
            return response.json()

        page = client.get("/")
        page.raise_for_status()
        assert "面经" in page.text and "assets/" in page.text
        expected_file = access_file.parent / "file-manifest.json"
        expected = json.loads(expected_file.read_text()) if expected_file.exists() else {}
        assets = re.findall(r'(?:href|src)="(assets/[^\"]+)"', page.text)
        assert len(assets) >= 5
        for asset in assets:
            response = client.get(asset)
            response.raise_for_status()
            path = "app/interview_intelligence/web/" + asset.split("?", 1)[0]
            if path in expected:
                assert hashlib.sha256(response.content).hexdigest() == expected[path]
        report["checks"]["static_assets"] = len(assets)
        report["checks"]["authenticated_home"] = True
        assert client.get("/internal/agent/runs/blocked/events").status_code == 404
        report["checks"]["internal_routes_blocked"] = True
        health = get("/api/health")["data"]
        assert health["database"] == health["index"] == "ready" and health["model"] == "configured"
        report["health"] = health

        listing = get("/api/questions/list", params={"coding_focus": "ALGORITHM", "top_n": 40, "page_size": 40})
        assert len(listing["data"]) == 40
        assert len({item["canonical_question_id"] for item in listing["data"]}) == 40
        report["checks"]["algorithm_top40"] = True
        question_id = listing["data"][0]["canonical_question_id"]
        detail = get(f"/api/questions/{question_id}")
        source = detail["data"]["sources"][0]
        revision = get(source["source_api_url"])["data"]
        assert revision["raw_file_hash"] == source["raw_file_hash"] and revision["markdown"].strip()
        report["checks"]["question_detail_and_original_source"] = True

        for pipeline in ("BM25", "HYBRID"):
            search_start = time.monotonic()
            search = get("/api/questions/search", params={"query": "Redis 缓存击穿", "pipeline": pipeline, "top_k": 3})
            assert search["meta"]["executed_pipeline"] == pipeline and search["data"]
            report["checks"][pipeline.lower()] = {"results": len(search["data"]), "elapsed_seconds": round(time.monotonic() - search_start, 3)}

        message = "列出 Redis 频率最高的前5道题"
        if reuse_latest_query:
            history = get("/api/conversations", params={"q": message, "limit": 1})["data"]["items"]
            assert history
            turns = get(f"/api/conversations/{history[0]['conversation_id']}")["data"]["turns"]
            accepted = next(turn for turn in reversed(turns) if turn["message"] == message)
            request_id = accepted["request_id"]
        else:
            conversation = post("/api/conversations")["data"]
            request_id = str(uuid4())
            accepted = post(f"/api/conversations/{conversation['conversation_id']}/messages", {
                "message": message, "request_id": request_id,
                "expected_version": conversation["version"], "page_size": 5})["data"]
        event_types = []
        first_event_ms = None
        stream_start = time.monotonic()
        with client.stream("GET", f"/api/runs/{accepted['run_id']}/events") as stream:
            stream.raise_for_status()
            assert stream.headers["content-type"].startswith("text/event-stream")
            for line in stream.iter_lines():
                if line.startswith("event: "):
                    if first_event_ms is None:
                        first_event_ms = round((time.monotonic() - stream_start) * 1000, 2)
                    event_types.append(line[7:])
        run = get(f"/api/runs/{accepted['run_id']}")["data"]
        assert run["status"] == "SUCCEEDED", {"status": run["status"], "error": run.get("error")}
        result = run["result"]
        assert result["planning"]["provider"] == "pi"
        assert result["planning"]["spec"]["action"] == "LIST"
        assert len(result["facts"]["data"]) == 5
        replay = get(f"/api/query/receipts/{request_id}")["data"]
        assert replay["run_id"] == accepted["run_id"] and replay["status"] == "SUCCEEDED"
        report["checks"]["natural_language_pi_and_sse"] = {"status": run["status"], "results": len(result["facts"]["data"]),
            "event_types": event_types, "first_event_ms": first_event_ms,
            "elapsed_seconds": round(time.monotonic() - stream_start, 3), "receipt_replay": True,
            "reused_existing_run": reuse_latest_query, "original_timings": result["facts"]["meta"]["timings"]}
        get("/api/conversations", params={"limit": 5})
        report["checks"]["conversation_history"] = True
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--access-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-latest-query", action="store_true")
    args = parser.parse_args()
    main(args.access_file, args.output, args.reuse_latest_query)
