"""Run a sealed candidate phase; keep seen regression and first-run reserve separate."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import httpx

from eval.common import digest, read_json, write_json
from eval.run import run_section
from eval.snapshot import assert_snapshot
from eval.validate_gold import require
from evals.project_quality.collect_labels import collect


def run(root: Path, phase: str):
    protocol = read_json(root / "protocol.v2.json")
    for prompt in ("query_agent_v7", "task_annotation_v7", "extract_question_v5"):
        name = f"prompts/{prompt}.md"
        require(digest(Path(name).read_bytes()) == protocol["source_sha256"][name], "SEALED_PROMPT_CHANGED")
    registry_path = root / "reports.json"
    registry = read_json(registry_path) if registry_path.exists() else {}
    require(phase not in registry, "PHASE_ALREADY_RUN")
    reports = {}
    if phase in {"labels", "challenge"}:
        datasets = (
            ("regression-211", "data/gold/evaluation-agent-reviewed-20261005-v5", "task_annotation_v7"),
            ("seen-source-200", "data/gold/task-generalization-agent-20261006-v2", "task_annotation_v7"),
        ) if phase == "labels" else tuple(
            (f"engineering-challenge-200-{version}", "data/gold/task-engineering-challenge-agent-20261006-v1", version)
            for version in ("task_annotation_v6", "task_annotation_v7"))
        for scope, dataset, version in datasets:
            prediction = root / f"labels.{version}.{scope}.jsonl"
            collect(Path(dataset), prediction, prompt_version=version, batch_size=40,
                    split="test", max_calls=48, max_tokens=2000000, source_context=True)
            report = run_section(Path(dataset), "task_labels", prediction, root / scope,
                                 review_policy="delegated_agent", threshold_version="quality_gate_v3")
            reports[scope] = str(report)
    else:
        dataset = ("data/gold/query-generalization-agent-20261006-v1" if phase == "query-regression"
                   else "data/gold/query-generalization-reserved-agent-20261006-v3")
        with httpx.Client(trust_env=False, timeout=20) as client:
            before = client.get("http://127.0.0.1:8000/api/evaluation/context").json()
        assert_snapshot(protocol["snapshot"], before["snapshot"])
        require(before["query_prompt_version"] == "query_agent_v7" and before["as_of_frozen"],
                "EVALUATION_CANDIDATE_OR_DATE_MISMATCH")
        require(before["state_count"] == before["event_count"] == before["conversation_count"] == 0,
                "FRESH_EVALUATION_ACTOR_REQUIRED")
        if phase == "query-reserved":
            require("query-regression" in registry, "RUN_SEEN_REGRESSION_BEFORE_RESERVE")
        write_json(root / f"{phase}.before.json", before)
        prediction = root / f"{phase}.v7.jsonl"
        subprocess.run([sys.executable, "-m", "eval.collect", "--dataset", dataset, "--section", "routing",
                        "--split", "test", "--review-policy", "delegated_agent", "--adapter", "http",
                        "--base-url", "http://127.0.0.1:8000", "--output", str(prediction),
                        "--max-calls", "240", "--max-tokens", "8000000"], check=True)
        report = run_section(Path(dataset), "routing", prediction, root / phase,
                             review_policy="delegated_agent", threshold_version="quality_gate_v3")
        reports[phase] = str(report)
        with httpx.Client(trust_env=False, timeout=20) as client:
            after = client.get("http://127.0.0.1:8000/api/evaluation/context").json()
        assert_snapshot(protocol["snapshot"], after["snapshot"])
        require(after["state_count"] == after["event_count"] == 0, "UNEXPECTED_ACTOR_WRITES")
        write_json(root / f"{phase}.after.json", after)
    # Other offline sections may finish while this model-backed phase is running.
    # Merge at completion instead of replacing their newly registered reports.
    registry = read_json(registry_path) if registry_path.exists() else {}
    require(phase not in registry, "PHASE_ALREADY_REGISTERED")
    registry[phase] = reports
    write_json(registry_path, registry)
    for scope, report in reports.items():
        print({"scope": scope, "report": report, "gate": read_json(Path(report) / "gates.json")["status"]}, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("labels", "challenge", "query-regression", "query-reserved"))
    parser.add_argument("--root", type=Path, default=Path("data/reports/quality-refinement-20261006"))
    args = parser.parse_args()
    run(args.root, args.phase)
