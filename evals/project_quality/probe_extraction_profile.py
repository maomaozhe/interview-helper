"""A single previously seen development case checks provider profile support."""
import argparse
import os
import time
from pathlib import Path

from eval.budget import EvaluationBudget
from eval.collect import ProviderCollector, error_code
from eval.common import digest, provenance, write_json, write_jsonl
from eval.snapshot import assert_snapshot, current_snapshot
from eval.validate_gold import require, validate_gold
from interview_intelligence.config import load_settings
from interview_intelligence.domain.models import create_database
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.resources import resource_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt-version", choices=("extract_question_v3", "extract_question_v4"), default="extract_question_v3")
    args = parser.parse_args()
    require(not args.output.exists(), "PROBE_ALREADY_EXISTS")
    manifest = validate_gold(args.dataset, "extraction", review_policy="delegated_agent")
    case = next(g for g in manifest["extraction"] if g["id"] == args.sample_id)
    settings = load_settings()
    require(os.name != "nt" and settings.model_lock_path.resolve() == Path("/app/runtime/model-call.lock"),
            "SHARED_LINUX_MODEL_GATE_REQUIRED")
    database = create_database(settings.database_url, create_tables=False)
    observe = lambda: current_snapshot(database, settings, as_of=manifest["snapshot"]["as_of"])
    assert_snapshot(manifest["snapshot"], observe())
    prompt = args.prompt_version
    write_json(args.output.with_suffix(".protocol.json"), {"sample_id": args.sample_id, "split": "dev",
        "eligible_for_release": False, "model": settings.extraction_model, "prompt_version": prompt,
        "thinking_mode": settings.extraction_thinking_mode,
        "prompt_sha256": digest(resource_path(f"prompts/{prompt}.md").read_bytes()),
        "source_sha256": case["source_hash"], "parent_dataset_sha256": digest(manifest)})
    budget, calls = EvaluationBudget(3, 800000), []
    collector = ProviderCollector("extraction", args.dataset, settings,
        ModelCallGate(settings.model_lock_path, minimum_interval_seconds=settings.model_min_interval_seconds),
        budget, extraction_prompt_version=prompt)
    started = time.perf_counter()
    row = {"id": args.sample_id, "snapshot": manifest["snapshot"], "split": "dev", "failed": False}
    try:
        row.update(collector(case, calls))
    except Exception as error:
        row.update(failed=True, error_code=error_code(error))
    row.update(elapsed_ms=(time.perf_counter() - started) * 1000, model_calls=calls)
    assert_snapshot(manifest["snapshot"], observe())
    write_jsonl(args.output, [row])
    write_json(args.output.with_suffix(".collection.json"), {"eligible_for_release": False,
        "predictions_sha256": digest(args.output.read_bytes()), "budget": budget.summary(), "provenance": provenance()})
    print({"sample": args.sample_id, "failed": row["failed"], "calls": budget.used_calls,
           "questions": len(row.get("result", {}).get("questions", [])), "elapsed_ms": row["elapsed_ms"]})
    raise SystemExit(1 if row["failed"] else 0)
