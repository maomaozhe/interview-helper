"""Replay the production batched classifier without publishing labels or leaking gold."""
from __future__ import annotations

import argparse
import os
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from pydantic import ValidationError

from eval.budget import EvaluationBudget
from eval.collect import error_code, phases
from eval.common import digest, inside, provenance, write_json, write_jsonl
from eval.snapshot import assert_snapshot, current_snapshot
from eval.validate_gold import require, validate_gold
from interview_intelligence.agent.task_annotation import resilient_model_labels
from interview_intelligence.agent.task_context import VERSION, compile_task_context
from interview_intelligence.ingestion.snapshot import decode_source
from interview_intelligence.config import load_settings
from interview_intelligence.domain.models import create_database
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.resources import resource_path


def collect(dataset, output, *, prompt_version, batch_size, split, max_calls, max_tokens, source_context=False):
    partial = output.with_suffix(".partial.jsonl")
    require(not output.exists() and not partial.exists(), "PREDICTION_OUTPUT_ALREADY_EXISTS")
    require(1 <= batch_size <= 100, "invalid annotation batch bounds")
    manifest = validate_gold(dataset, "task_labels", split=split, review_policy="delegated_agent")
    golds = [g for g in manifest["task_labels"] if g["split"] == split]
    require(len({g["occurrence_id"] for g in golds}) == len(golds), "DUPLICATE_OCCURRENCE")
    settings = load_settings()
    require(os.name != "nt" and settings.model_lock_path.resolve() == Path("/app/runtime/model-call.lock"),
            "SHARED_LINUX_MODEL_GATE_REQUIRED")
    database = create_database(settings.database_url, create_tables=False)
    snapshot = manifest["snapshot"]
    observe = lambda: current_snapshot(database, settings, as_of=snapshot["as_of"])
    assert_snapshot(snapshot, observe())
    gate = ModelCallGate(settings.model_lock_path, minimum_interval_seconds=settings.model_min_interval_seconds)
    budget = EvaluationBudget(max_calls, max_tokens)
    prompt = resource_path(f"prompts/{prompt_version}.md").read_text(encoding="utf-8")
    started, source, rows, recoveries = time.perf_counter(), provenance(), [], []
    for offset in range(0, len(golds), batch_size):
        assert_snapshot(snapshot, observe())
        selected = golds[offset:offset + batch_size]
        batch = [SimpleNamespace(id=g["occurrence_id"], raw_question=g["raw_question"],
                                 source_spans=g["source_spans"]) for g in selected]
        # Expected classes/review text never enter the production request.
        payload = [{"occurrence_id": g["occurrence_id"], "raw_question": g["raw_question"],
                    "context_before": g.get("context_before"), "context_after": g.get("context_after"),
                    "source_quotes": [s["quote"] for s in g["source_spans"]]} for g in selected]
        if source_context:
            for gold, item in zip(selected, payload):
                raw = inside(dataset, gold["source_path"]).read_bytes()
                require(digest(raw) == gold["source_hash"], "ANNOTATION_SOURCE_HASH_MISMATCH")
                item.update(compile_task_context(decode_source(raw), gold["source_spans"]))
        calls, failure, detail, labels = [], None, None, {}
        class AuditSession:
            def begin(self): return nullcontext()
            def add(self, entry):
                calls.append({key: getattr(entry, key, None) for key in (
                    "operation_type", "model", "model_revision", "prompt_version", "input_tokens", "output_tokens",
                    "latency_ms", "queue_ms", "interval_ms", "provider_ms", "status", "retry_count", "error_code")})
        audit = SimpleNamespace(session=lambda: nullcontext(AuditSession()))
        batch_started = time.perf_counter()
        try:
            result = resilient_model_labels(audit, settings, gate, budget, prompt, payload, batch, prompt_version,
                on_recovery=lambda entry: recoveries.append({"batch_offset": offset, **entry}))
            labels = {item.occurrence_id: item.model_dump(mode="json") for item in result.items}
        except Exception as error:
            failure = error_code(error)
            detail = (error.errors(include_url=False, include_context=False, include_input=False)
                      if isinstance(error, ValidationError) else failure)
        assert_snapshot(snapshot, observe())
        elapsed = (time.perf_counter() - batch_started) * 1000
        batch_id = f"batch-{offset // batch_size}"
        for index, gold in enumerate(selected):
            label = labels.get(gold["occurrence_id"], {})
            if label and label["confidence"] < .7:
                label.update(response_form="UNKNOWN", coding_focus="UNKNOWN")
            attributed_calls = calls if index == 0 else []
            rows.append({"id": gold["id"], "snapshot": snapshot, "failed": failure is not None,
                         "error_code": failure, "error_detail": detail, "result": label, "elapsed_ms": elapsed,
                         "model_calls": attributed_calls, "timings": phases(attributed_calls),
                         "batch_id": batch_id, "batch_items": len(selected),
                         "adapter": "production_provider_batched_no_publish"})
        write_jsonl(partial, rows)
        print({"completed": len(rows), "failed": sum(r["failed"] for r in rows),
               "calls": budget.used_calls}, flush=True)
    assert_snapshot(snapshot, observe())
    write_jsonl(output, rows)
    write_json(output.with_suffix(".collection.json"), {
        "adapter": "production_provider_batched_no_publish", "section": "task_labels", "split": split,
        "review_policy": "delegated_agent", "snapshot": snapshot, "samples": len(rows),
        "failed": sum(r["failed"] for r in rows), "prompt_version": prompt_version, "recoveries": recoveries,
        "execution_policy": "two schema attempts, then one level of ten-item sub-batches; atomic batch result",
        "source_context": VERSION if source_context else "gold_context_only",
        "prompt_sha256": digest(prompt.encode()), "batch_size": batch_size,
        "job_elapsed_ms": (time.perf_counter() - started) * 1000, "budget": budget.summary(),
        "predictions_sha256": digest(output.read_bytes()), "provenance": source,
        "completed_provenance": provenance(), "published_label_count": 0,
        "latency_scope": "Each row carries the full shared batch delay; model telemetry attributed once per batch",
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt-version", choices=("task_annotation_v4", "task_annotation_v5", "task_annotation_v6", "task_annotation_v7"), required=True)
    parser.add_argument("--source-context", action="store_true")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--split", choices=("dev", "test"), default="test")
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--max-tokens", type=int, required=True)
    args = parser.parse_args()
    collect(args.dataset, args.output, prompt_version=args.prompt_version, batch_size=args.batch_size,
            split=args.split, max_calls=args.max_calls, max_tokens=args.max_tokens, source_context=args.source_context)
