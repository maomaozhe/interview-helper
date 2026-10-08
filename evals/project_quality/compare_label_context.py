"""Execute the predeclared paired v4/v6 protocol on regression and held-out sources."""
import argparse
from pathlib import Path

from eval.run import run_section
from evals.project_quality.collect_labels import collect


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--regression", type=Path, required=True)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for dataset, scope in ((args.regression, "regression"), (args.holdout, "holdout")):
        for version in ("task_annotation_v4", "task_annotation_v6"):
            prediction = args.output / f"labels.{scope}.{version}.source-context.jsonl"
            print({"protocol": "paired_original_source_context", "scope": scope, "prompt": version}, flush=True)
            collect(dataset, prediction, prompt_version=version, batch_size=40, split="test",
                    max_calls=48, max_tokens=4000000, source_context=True)
            report = run_section(dataset, "task_labels", prediction, args.output / scope / version,
                                 review_policy="delegated_agent", threshold_version="quality_gate_v3")
            print({"report": str(report)}, flush=True)
