"""Real HTTP acceptance of short-answer and NEXT historical requeries."""
import argparse
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


def evaluate(client, result, persist, prior_report):
    """Keep every receipt and check preconditions before binding a historical run."""
    def check(name, condition, details):
        result["checks"].append({"name": name, "passed": bool(condition), "details": details})
        persist()
        if not condition:
            raise AssertionError(name)

    def query(case, **body):
        body["request_id"] = "historical-requery-" + str(uuid4())
        started = time.perf_counter()
        response = client.post("/api/query", json=body)
        saved = {"case": case, "request": body, "status": response.status_code,
                 "elapsed_ms": round((time.perf_counter()-started)*1000), "response": response.json()}
        result["turns"].append(saved)
        persist()
        print(case, response.status_code, saved["elapsed_ms"], flush=True)
        response.raise_for_status()
        return saved["response"]

    result["health"] = client.get("/api/health").json()
    # Previous attempts change this conversation too. Explicitly establish a
    # different topic each time so this remains an actual topic-reset test.
    case = next(c for c in prior_report["cases"] if c["id"] == "context-and-topic-reset")
    turn = case["turns"][1]
    meta = turn["response"]["meta"]
    history_response = client.get("/api/conversations/" + meta["conversation_id"])
    history_response.raise_for_status()
    history = history_response.json()["data"]
    changed = query("prepare-business-topic", message="秒杀业务系统设计题",
        conversation_id=meta["conversation_id"], expected_version=history["version"])
    changed_spec = changed["meta"]["planning"]["spec"]
    check("business-topic-established", changed_spec["action"] == "SEARCH" and
        "秒杀" in (changed_spec.get("relevance_query") or changed_spec.get("search_query") or ""), changed_spec)
    short = query("short-followup-after-topic-change", message=turn["request"]["message"],
        requery_of_run_id=meta["run_id"], conversation_id=meta["conversation_id"],
        expected_version=changed["meta"]["conversation_version"])
    spec = short["meta"]["planning"]["spec"]
    check("short-followup-restores-agent-goal", spec["action"] == "SEARCH" and
        "agent" in (spec.get("relevance_query") or "").casefold(), spec)
    check("short-followup-has-fresh-bound-run", short["meta"].get("requery_of_run_id") == meta["run_id"] and
        short["meta"]["run_id"] != meta["run_id"], short["meta"])

    # Page size is an explicit UI condition, not an overall Top N instruction.
    first = query("filtered-list-first", message="列出 Redis 高频题", filters={"company": "字节"}, page_size=2)
    first_meta = first["meta"]
    first_spec = first_meta["planning"]["spec"]
    first_ids = [row["canonical_question_id"] for row in first["data"]]
    check("first-page-is-unbounded-paged-list", first_spec["action"] == "LIST" and
        first_spec.get("top_n") is None and first_spec["page_size"] == 2 and len(first_ids) == 2 and
        first_meta["pagination"]["offset"] == 0 and bool(first_meta["pagination"].get("next_cursor")),
        {"spec": first_spec, "pagination": first_meta.get("pagination"), "ids": first_ids})
    check("first-page-preserves-explicit-company", first_meta["applied_filters"]["company"] == "字节",
          first_meta["applied_filters"])
    second = query("filtered-list-next", message="下一页", conversation_id=first_meta["conversation_id"],
        expected_version=first_meta["conversation_version"])
    second_meta = second["meta"]
    second_ids = [row["canonical_question_id"] for row in second["data"]]
    check("source-is-real-next-page", second_meta["planning"]["spec"]["action"] == "NEXT" and
        bool(second_ids) and not set(second_ids).intersection(first_ids) and
        second_meta["pagination"]["offset"] == 2 and second_meta["applied_filters"]["company"] == "字节",
        {"spec": second_meta["planning"]["spec"], "pagination": second_meta.get("pagination"),
         "ids": second_ids, "first_ids": first_ids})

    fresh = query("historical-next-refreshes-first", message="下一页", requery_of_run_id=second_meta["run_id"],
        conversation_id=second_meta["conversation_id"], expected_version=second_meta["conversation_version"])
    fresh_meta = fresh["meta"]
    fresh_ids = [row["canonical_question_id"] for row in fresh["data"]]
    check("historical-next-restarts-original-first-page", fresh_meta["planning"]["spec"]["action"] == "LIST" and
        fresh_meta["applied_filters"]["company"] == "字节" and fresh_meta["pagination"]["offset"] == 0 and
        fresh_ids == first_ids and fresh_meta["run_id"] != second_meta["run_id"] and
        fresh_meta.get("requery_of_run_id") == second_meta["run_id"],
        {"spec": fresh_meta["planning"]["spec"], "pagination": fresh_meta.get("pagination"),
         "ids": fresh_ids, "expected_first_ids": first_ids, "source_run_id": second_meta["run_id"],
         "new_run_id": fresh_meta["run_id"]})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--prior-report", type=Path,
                        default=Path("data/reports/conversation-quality-20261008/after-v9-r6.json"))
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refuse to overwrite real acceptance receipts")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = {"protocol_version": "historical_requery_v2", "status": "RUNNING", "passed": False,
              "started_at": datetime.now(timezone.utc).isoformat(), "turns": [], "checks": [], "human_verified": False}
    def persist():
        atomic_write_json(args.output, result)
    try:
        atomic_write_json(args.output, result, exclusive=True)
    except FileExistsError:
        raise SystemExit("Refuse to overwrite real acceptance receipts") from None
    try:
        prior = json.loads(args.prior_report.read_text(encoding="utf-8"))
        with httpx.Client(base_url=args.url, timeout=100, trust_env=False) as client:
            evaluate(client, result, persist, prior)
        result["passed"] = True
        result["status"] = "SUCCEEDED"
    except Exception as failure:
        result["status"] = "FAILED"
        result["failure"] = {"type": type(failure).__name__, "message": str(failure),
            "last_case": result["turns"][-1]["case"] if result["turns"] else None}
        raise
    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        persist()


if __name__ == "__main__":
    main()
