"""Run frozen-gold metrics from explicit predictions without synthesizing labels."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from eval.metrics import classification_metrics, retrieval_metrics, routing_metrics
from eval.validate_gold import validate_gold


def _load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def run_section(dataset: Path, section: str, predictions: Path, output_root: Path) -> Path:
    manifest = validate_gold(dataset)
    if section not in {"extraction", "dedup", "retrieval", "routing"}:
        raise ValueError("unknown evaluation section")
    gold = {item["id"]: item for item in manifest[section] if item["split"] == "test"}
    rows = _load_jsonl(predictions)
    seen = {row["id"] for row in rows}
    if len(seen) != len(rows) or seen != gold.keys():
        raise ValueError("predictions must cover each frozen test ID exactly once")
    joined = [(gold[row["id"]], row) for row in rows]
    if section == "dedup":
        metrics = classification_metrics([item[0]["label"] for item in joined],
                                         [item[1]["label"] for item in joined], positive="SAME")
    elif section == "retrieval":
        metrics = retrieval_metrics([{
            "relevant": gold_item["relevance"],
            "ranked": prediction.get("ranked", []),
            "failed": prediction.get("failed", False),
        } for gold_item, prediction in joined])
    elif section == "routing":
        metrics = routing_metrics([{
            "required_tools": gold_item["required_tools"],
            "used_tools": prediction.get("used_tools", []),
            "allow_write": gold_item.get("allow_write", False),
            "task_success": prediction.get("task_success", False),
        } for gold_item, prediction in joined])
    else:
        for _, prediction in joined:
            if not prediction.get("alignment_reviewed"):
                raise ValueError("extraction predictions need human-reviewed alignment")
            if not all(key in prediction for key in ("tp", "fp", "fn")):
                raise ValueError("extraction alignment counts missing")
        tp = sum(prediction["tp"] for _, prediction in joined)
        fp = sum(prediction["fp"] for _, prediction in joined)
        fn = sum(prediction["fn"] for _, prediction in joined)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = (2 * precision * recall / (precision + recall)
              if precision is not None and recall is not None and precision + recall else None)
        metrics = {"tp": tp, "fp": fp, "fn": fn,
                   "precision": precision, "recall": recall, "f1": f1}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + section
    output = output_root / section / "results" / run_id
    output.mkdir(parents=True, exist_ok=False)
    raw_predictions = predictions.read_bytes()
    (output / "predictions.jsonl").write_bytes(raw_predictions)
    (output / "manifest.json").write_text(json.dumps({
        "dataset_version": manifest["version"], "section": section,
        "dataset_sha256": hashlib.sha256((dataset / "manifest.json").read_bytes()).hexdigest(),
        "predictions_sha256": hashlib.sha256(raw_predictions).hexdigest(),
        "test_count": len(gold), "run_id": run_id,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "per_sample.csv").open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["id", "failed", "gold", "prediction"])
        writer.writeheader()
        for gold_item, prediction in joined:
            writer.writerow({"id": gold_item["id"], "failed": prediction.get("failed", False),
                             "gold": json.dumps(gold_item, ensure_ascii=False),
                             "prediction": json.dumps(prediction, ensure_ascii=False)})
    (output / "bad_cases.md").write_text("# Bad cases\n\n待逐样本复核。\n", encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--section", choices=["extraction", "dedup", "retrieval", "routing"], required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path("eval"))
    args = parser.parse_args()
    print(run_section(args.dataset, args.section, args.predictions, args.output_root))


if __name__ == "__main__":
    main()
