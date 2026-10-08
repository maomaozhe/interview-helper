"""Paired development reranker audit on a frozen, complete Top50 candidate pool.

Run inside the API container to use its existing Linux model gate and settings.
Capture uses saved query plans, not a new model rewrite. Compare reuses exactly
the same candidate texts and IDs. This is a regression audit, not human gold.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


CASE_IDS = ["agent-design-feedback", "harness-feedback", "business-specific", "production-specific"]


class RecordingReranker:
    def __init__(self, delegate):
        self.delegate = delegate
        self.version = delegate.version
        self.calls = []

    def rerank(self, query, candidates):
        self.calls.append({"query": query, "candidates": candidates})
        result = self.delegate.rerank(query, candidates)
        self.calls[-1]["all_accepted"] = result
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["capture", "compare"], required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--source", type=Path, default=Path("/app/data/reports/conversation-quality-20261008/after-v9-r6.json"))
    parser.add_argument("--output", type=Path, default=Path("/app/data/reports/retrieval-evidence-20261008"))
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate-module", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / f"{args.label}.json"
    if target.exists():
        raise SystemExit("Refuse to overwrite real model receipts")

    # The API factory wires the same Linux shared lock, interval, token telemetry,
    # provider timeout and serial budget as the running application.
    from interview_intelligence.api import app
    from interview_intelligence.contracts import FilterSpec
    from interview_intelligence.search.service import eligible_canonical_ids
    service = app.state.query_service
    retriever = service.retriever
    delegate = retriever.reranker
    if args.candidate_module:
        spec = importlib.util.spec_from_file_location("candidate_reranker", args.candidate_module)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        delegate = module.LLMReranker(model=delegate.model, client=delegate.client, budget=delegate.budget,
            call_gate=delegate.call_gate, on_call=delegate.on_call, timeout_seconds=delegate.timeout_seconds,
            stream=delegate.stream, reasoning=delegate.reasoning)
    calls, original_callback = [], delegate.on_call
    def record_call(entry):
        calls.append(entry)
        if original_callback:
            original_callback(entry)
    delegate.on_call = record_call
    recorder = RecordingReranker(delegate)
    retriever.reranker = recorder
    source = json.loads((args.baseline if args.mode == "compare" else args.source).read_text(encoding="utf-8"))
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "version": recorder.version,
              "mode": args.mode, "human_verified": False, "cases": [],
              "limitations": "Seen development regression; frozen pool recall only, not global recall or independent human gold."}
    for case in source["cases"]:
        if case["id"] not in CASE_IDS:
            continue
        started = time.perf_counter()
        call_start = len(calls)
        saved = {"id": case["id"]}
        try:
            if args.mode == "capture":
                turn = case["turns"][0]
                spec = turn["response"]["meta"]["planning"]["spec"]
                saved["spec"] = spec
                with service.database.session() as session:
                    ids = eligible_canonical_ids(session, FilterSpec.model_validate(spec["filters"]))
                saved["eligible_count"] = len(ids)
                retrieval = retriever.retrieve(spec["search_query"], ids, "HYBRID_RERANK", spec["page_size"],
                                               relevance_query=spec["relevance_query"])
                saved["retrieval_meta"] = retrieval["meta"]
                saved.update(recorder.calls[-1])
                saved["returned"] = retrieval["data"]
            else:
                saved.update({key: case[key] for key in ["spec", "query", "candidates", "eligible_count", "pool_sha256"]})
                saved["all_accepted"] = recorder.rerank(case["query"], case["candidates"])
                saved["returned"] = saved["all_accepted"][:case["spec"]["page_size"]]
                saved["rerank_audit"] = getattr(saved["all_accepted"], "audit", None)
            saved["pool_sha256"] = hashlib.sha256(json.dumps(saved["candidates"], sort_keys=True,
                                                    ensure_ascii=False).encode()).hexdigest()
        except Exception as error:
            saved["error"] = type(error).__name__ + ": " + str(error)[:200]
        saved["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        saved["model_calls"] = calls[call_start:]
        result["cases"].append(saved)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(saved["id"], saved.get("error", len(saved.get("returned", []))), saved["elapsed_ms"], flush=True)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
