"""Sealed, budgeted provider comparisons over original-source gold; no publishing.

Source replay evaluates archived real interviews, so it does not require the live
database to remain on their historical revision. Both arms share one sealed code
package, original input set, model configuration, and the existing Linux gate.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


DEFINITIONS = {
    "task_labels": {"dataset": "data/gold/source-reviewed-extraction-20261014-v3",
                    "baseline": "task_annotation_v7", "candidate": "task_annotation_v8",
                    "max_calls": 48, "max_tokens": 2000000, "batch_size": 40},
    "extraction": {"dataset": "data/gold/source-reviewed-extraction-20261014-v3",
                   "baseline": "extract_question_v5", "candidate": "extract_question_v6",
                   "max_calls": 96, "max_tokens": 4000000, "batch_size": 1},
    "dedup": {"dataset": "data/gold/source-reviewed-dedup-20261014-v2",
              "baseline": "dedup_judge_v1", "candidate": "dedup_judge_v2",
              "max_calls": 620, "max_tokens": 4000000, "batch_size": 1},
}


def selected_samples(manifest, section, definition):
    from eval.validate_gold import require
    rows = [row for row in manifest[section] if row["split"] == "test"]
    selected = definition.get("sample_ids")
    if selected is not None:
        require(isinstance(selected, list) and selected and len(set(selected)) == len(selected),
                "EXPLICIT_UNIQUE_SOURCE_SAMPLE_IDS_REQUIRED")
        require(set(selected) <= {row["id"] for row in rows}, "SELECTED_SOURCE_SAMPLE_MISSING")
        rows = [row for row in rows if row["id"] in selected]
    return rows


def prepare(root: Path, section: str):
    from eval.common import digest, write_json
    from eval.validate_gold import require, validate_gold
    root.mkdir(parents=True, exist_ok=True)
    path = root / (section + ".protocol.json")
    runtime = root / ("runtime-" + section)
    require(not path.exists() and not runtime.exists(), "SOURCE_PHASE_ALREADY_SEALED")
    definition = DEFINITIONS[section]
    dataset = Path(definition["dataset"]).resolve()
    gold = validate_gold(dataset, section, review_policy="delegated_agent")
    for arm in ("baseline", "candidate"):
        require((Path("prompts") / (definition[arm] + ".md")).is_file(), "CANDIDATE_PROMPT_NOT_READY")
    runtime.mkdir()
    files = {}
    for folder in ("src", "eval", "evals", "prompts", "config"):
        for source in sorted(Path(folder).rglob("*")):
            if not source.is_file() or source.suffix not in {".py", ".md", ".json", ".yaml"}:
                continue
            if {"__pycache__", "reports", "results"}.intersection(source.parts):
                continue
            target = runtime / source
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            files[source.as_posix()] = digest(source.read_bytes())
    protocol = {"schema": "source_provider_iteration_v1", "section": section,
                "created_at": datetime.now(timezone.utc).isoformat(), "snapshot": gold["snapshot"],
                "dataset": definition["dataset"], "dataset_sha256": digest(gold),
                "sample_count": len(selected_samples(gold, section, definition)), "definition": definition,
                "runtime": runtime.name, "runtime_sha256": digest(files), "runtime_files": files,
                "prompt_sha256": {arm: digest((runtime / "prompts" / (definition[arm] + ".md")).read_bytes())
                                  for arm in ("baseline", "candidate")},
                "source_context": "task_context_v1", "thinking_mode": "disabled",
                "dedup_guard": definition.get("dedup_guard", {"baseline": False, "candidate": True}),
                "dedup_source_context": definition.get("dedup_source_context", {"baseline": False, "candidate": False}),
                "scope": definition.get("scope", "Seen original-source diagnostic comparison, not unseen generalization."),
                "production_writes": 0}
    write_json(path, protocol)
    print(json.dumps({"sealed": section, "samples": protocol["sample_count"], "runtime": str(runtime)}, ensure_ascii=False))


def collect(root: Path, section: str, arm: str):
    # Bind imports to the immutable package before importing any project module.
    protocol_path = root / (section + ".protocol.json")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    runtime = (root / protocol["runtime"]).resolve()
    sys.path[:0] = [str(runtime / "src"), str(runtime)]
    from eval.budget import EvaluationBudget
    from eval.collect import ProviderCollector, capped_client, error_code, phases
    from eval.common import digest, inside, read_json, write_json, write_jsonl
    from eval.validate_gold import require, validate_gold
    from interview_intelligence.agent.task_annotation import resilient_model_labels
    from interview_intelligence.agent.task_context import VERSION, compile_task_context
    from interview_intelligence.config import load_settings
    from interview_intelligence.ingestion.snapshot import decode_source
    from interview_intelligence.providers.gate import ModelCallGate

    require(os.name != "nt", "SHARED_LINUX_GATE_REQUIRED")
    for name, expected in protocol["runtime_files"].items():
        require(digest((runtime / name).read_bytes()) == expected, "SEALED_RUNTIME_CHANGED")
    require(VERSION == protocol["source_context"], "SEALED_CONTEXT_CHANGED")
    definition = protocol["definition"]
    dataset = Path(protocol["dataset"]).resolve()
    manifest = validate_gold(dataset, section, review_policy="delegated_agent")
    require(digest(manifest) == protocol["dataset_sha256"], "SOURCE_GOLD_CHANGED")
    golds = selected_samples(manifest, section, definition)
    settings = load_settings()
    require(Path(settings.model_lock_path).resolve() == Path("/app/runtime/model-call.lock"), "SHARED_LINUX_GATE_REQUIRED")
    require(settings.model_api_key and settings.model_base_url, "MODEL_CONFIGURATION_INCOMPLETE")
    settings = settings.model_copy(update={"extraction_thinking_mode": "disabled"})
    gate = ModelCallGate(settings.model_lock_path, minimum_interval_seconds=settings.model_min_interval_seconds)
    budget = EvaluationBudget(definition["max_calls"], definition["max_tokens"])
    version = definition[arm]
    prompt = (runtime / "prompts" / (version + ".md")).read_text(encoding="utf-8")
    output = root / (section + "." + arm + ".jsonl")
    partial = output.with_suffix(".partial.jsonl")
    require(not output.exists() and not partial.exists(), "PREDICTION_OUTPUT_ALREADY_EXISTS")
    provider = None
    if section != "task_labels":
        provider = ProviderCollector(section, dataset, settings, gate, budget,
            extraction_prompt_version=version if section == "extraction" else None,
            dedup_prompt_version=version if section == "dedup" else None,
            dedup_verify_equivalence=protocol["dedup_guard"][arm] if section == "dedup" else None,
            dedup_source_context=protocol.get("dedup_source_context", {}).get(arm, False) if section == "dedup" else False)
        provider.client = capped_client(settings)
    started, rows, recoveries, halt = time.perf_counter(), [], [], None
    batch_size = definition["batch_size"]
    for offset in range(0, len(golds), batch_size):
        selected = golds[offset:offset + batch_size]
        calls, observation = [], {}
        failure = halt
        batch_started = time.perf_counter()
        if not halt:
            try:
                if section == "task_labels":
                    batch = [SimpleNamespace(id=g["id"], raw_question=g["raw_question"], source_spans=g["source_spans"])
                             for g in selected]
                    payload = []
                    for gold in selected:
                        raw = inside(dataset, gold["source_path"]).read_bytes()
                        require(digest(raw) == gold["source_hash"], "SOURCE_BYTES_CHANGED")
                        context = compile_task_context(decode_source(raw), gold["source_spans"])
                        payload.append({"occurrence_id": gold["id"], "raw_question": gold["raw_question"],
                                        "source_quotes": [s["quote"] for s in gold["source_spans"]], **context})
                    class AuditSession:
                        def begin(self): return nullcontext()
                        def add(self, entry):
                            calls.append({k: getattr(entry, k, None) for k in (
                                "operation_type", "model", "model_revision", "prompt_version", "input_tokens",
                                "output_tokens", "latency_ms", "queue_ms", "interval_ms", "provider_ms",
                                "status", "retry_count", "error_code")})
                    audit = SimpleNamespace(session=lambda: nullcontext(AuditSession()))
                    labels = resilient_model_labels(audit, settings, gate, budget, prompt, payload, batch, version,
                        on_recovery=lambda value: recoveries.append({"offset": offset, **value}))
                    observation = {r.occurrence_id: r.model_dump(mode="json") for r in labels.items}
                else:
                    observation = provider(selected[0], calls)
            except Exception as error:
                failure = error_code(error)
                status = getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
                if failure == "EVALUATION_BUDGET_EXCEEDED" or type(error).__name__ in {
                        "AuthenticationError", "PermissionDeniedError", "NotFoundError"} or status in {401, 403, 404}:
                    halt = failure
        elapsed = (time.perf_counter() - batch_started) * 1000
        for index, gold in enumerate(selected):
            if section == "task_labels":
                result = observation.get(gold["id"], {})
                if result and result["confidence"] < .7:
                    result.update(response_form="UNKNOWN", coding_focus="UNKNOWN")
                row = {"result": result, "model_calls": calls if index == 0 else [],
                       "timings": phases(calls if index == 0 else []), "batch_id": f"batch-{offset // batch_size}",
                       "batch_items": len(selected)}
            else:
                row = {**observation, "model_calls": calls}
            rows.append({**row, "id": gold["id"], "snapshot": manifest["snapshot"], "failed": failure is not None,
                         "error_code": failure, "elapsed_ms": elapsed, "adapter": "sealed_original_source_provider"})
        write_jsonl(partial, rows)
        print(json.dumps({"section": section, "arm": arm, "completed": len(rows), "total": len(golds),
                          "failed": sum(r["failed"] for r in rows), "calls": budget.used_calls,
                          "charged_tokens": budget.used_tokens}, ensure_ascii=False), flush=True)
    write_jsonl(output, rows)
    receipt = {"adapter": "sealed_original_source_provider", "section": section, "arm": arm,
               "prompt_version": version, "prompt_sha256": protocol["prompt_sha256"][arm],
               "runtime_sha256": protocol["runtime_sha256"], "protocol_sha256": digest(protocol),
               "dataset_sha256": digest(manifest), "snapshot": manifest["snapshot"], "split": "test",
               "review_policy": "delegated_agent", "samples": len(rows), "failed": sum(r["failed"] for r in rows),
               "predictions_sha256": digest(output.read_bytes()), "model_base_url_sha256": digest(settings.model_base_url),
               "configured_models": {"extraction": settings.extraction_model, "judge": settings.judge_model,
                                     "task_labels": settings.query_model or settings.judge_model},
               "budget": budget.summary(), "recoveries": recoveries, "halted_for": halt,
               "elapsed_ms": (time.perf_counter() - started) * 1000, "production_writes": 0,
               "scope": protocol["scope"]}
    write_json(output.with_suffix(".collection.json"), receipt)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "collect"))
    parser.add_argument("--root", type=Path, default=Path("data/reports/source-optimization-20261014-v1"))
    parser.add_argument("--section", choices=tuple(DEFINITIONS), required=True)
    parser.add_argument("--arm", choices=("baseline", "candidate"), default="baseline")
    args = parser.parse_args()
    if args.mode == "prepare": prepare(args.root, args.section)
    else:
        # Execute the sealed entry point too, not only its imported providers.
        # Arguments remain an argv array, including Unicode or spaced paths.
        protocol = json.loads((args.root / (args.section + ".protocol.json")).read_text(encoding="utf-8"))
        entry = (args.root / protocol["runtime"] / "evals/project_quality/source_iteration.py").resolve()
        if Path(__file__).resolve() != entry:
            os.execv(sys.executable, [sys.executable, str(entry), *sys.argv[1:]])
        collect(args.root, args.section, args.arm)


if __name__ == "__main__":
    main()
