"""Score stored observations; dev reports remain ineligible for release."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from eval.common import SECTIONS, digest, provenance, read_jsonl, write_json
from eval.metrics import efficiency
from eval.report import gates, write_report
from eval.scoring import score_dedup, score_extraction, score_labels, score_query, score_retrieval, slices
from eval.validate_gold import require, validate_gold


def run_section(dataset: Path, section: str, predictions: Path, output_root: Path, *,
                split="test", alignments: Path | None = None, default_pipeline="HYBRID", review_policy="human",
                threshold_version="quality_gate_v2") -> Path:
    manifest = validate_gold(dataset, section, split=split, review_policy=review_policy)
    golds = [item for item in manifest[section] if item["split"] == split]
    rows = read_jsonl(predictions)
    seen = {row.get("id") for row in rows}
    require(len(seen) == len(rows) and seen == {g["id"] for g in golds},
            "predictions must cover each selected ID exactly once")
    by_id = {row["id"]: row for row in rows}
    rows = [by_id[g["id"]] for g in golds]
    snapshot = manifest.get("snapshot", {})
    for row in rows:
        require(type(row.get("failed", False)) is bool, "failed must be boolean")
        if snapshot:
            actual = row.get("snapshot", {})
            require(all(actual.get(k) == value for k, value in snapshot.items()), "prediction snapshot missing or changed")
    alignment_map = {}
    if alignments:
        entries = read_jsonl(alignments)
        require(len({r["id"] for r in entries}) == len(entries), "duplicate alignment ID")
        alignment_map = {r["id"]: r for r in entries}
    if section == "dedup":
        metrics, scored = score_dedup(golds, rows)
    elif section == "task_labels":
        metrics, scored = score_labels(golds, rows)
    elif section == "retrieval":
        metrics, scored = score_retrieval(golds, rows, manifest)
    elif section in {"routing", "sql"}:
        metrics, scored = score_query(golds, rows, sql=section == "sql")
    else:
        metrics, scored = score_extraction(golds, rows, dataset, alignment_map,
                                          fixture=manifest.get("kind") == "fixture", review_policy=review_policy)
    metrics["efficiency"] = efficiency(rows)
    metrics["slices"] = slices(scored)
    verified = sum(g["human_verified"] for g in golds)
    eligible = split == "test" and verified == len(golds) and manifest.get("kind") != "fixture"
    agent_verified = sum(g.get("agent_verified") is True for g in golds)
    if review_policy == "delegated_agent":
        eligible = split == "test" and agent_verified == len(golds) and manifest.get("kind") != "fixture"
    gate = gates(section, metrics, eligible=eligible, default_pipeline=default_pipeline,
                 threshold_version=threshold_version)
    gate["review_policy"] = review_policy
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    output = output_root / section / "results" / run_id
    output.mkdir(parents=True, exist_ok=False)
    (output / "predictions.jsonl").write_bytes(predictions.read_bytes())
    if alignments:
        (output / "alignments.jsonl").write_bytes(alignments.read_bytes())
    config = provenance()
    config.update(default_pipeline=default_pipeline, snapshot=snapshot, models=manifest.get("models", {}),
                  threshold_version=threshold_version,
                  annotation_guide=manifest.get("annotation_guide"))
    receipt = predictions.with_suffix(".collection.json")
    if receipt.is_file():
        from eval.common import read_json
        collection = read_json(receipt)
        require(collection.get("predictions_sha256") == digest(predictions.read_bytes()), "collection receipt prediction hash mismatch")
        config["collection"] = collection
        (output / "collection.json").write_bytes(receipt.read_bytes())
    config["observed_models"] = sorted({(c.get("operation_type"), c.get("model"), c.get("model_revision"), c.get("prompt_version"))
        for row in rows for c in row.get("model_calls", [])}, key=repr)
    metadata = {"dataset_version": manifest["version"], "kind": manifest.get("kind", "corpus"),
                "section": section, "split": split, "sample_count": len(golds),
                "test_count": len(golds) if split == "test" else 0, "human_verified_count": verified,
                "agent_verified_count": agent_verified, "review_policy": review_policy,
                "dataset_sha256": digest(manifest), "predictions_sha256": digest(predictions.read_bytes()),
                "alignments_sha256": digest(alignments.read_bytes()) if alignments else None,
                "snapshot": snapshot, "run_id": run_id}
    write_report(output, metadata, config, metrics, gate, scored)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--section", choices=SECTIONS, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--alignments", type=Path)
    parser.add_argument("--split", choices=("dev", "test"), default="test")
    parser.add_argument("--review-policy", choices=("human", "delegated_agent"), default="human")
    parser.add_argument("--default-pipeline", choices=("BM25", "DENSE", "HYBRID", "HYBRID_RERANK"), default="HYBRID")
    parser.add_argument("--threshold-version", choices=("quality_gate_v2", "quality_gate_v3"), default="quality_gate_v2")
    parser.add_argument("--output-root", type=Path, default=Path("eval"))
    args = parser.parse_args()
    try:
        output = run_section(args.dataset, args.section, args.predictions, args.output_root,
                             split=args.split, alignments=args.alignments, default_pipeline=args.default_pipeline,
                             review_policy=args.review_policy, threshold_version=args.threshold_version)
    except (ValueError, KeyError, OSError, TypeError) as error:
        # An invalid run is a diagnostic receipt, never a partial quality score.
        output = args.output_root / args.section / "results" / ("invalid-" + uuid4().hex)
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "gates.json", {"status": "INVALID", "error": str(error)})
        print(f"INVALID: {error}\n{output}")
        return 1
    print(output)
    from eval.common import read_json
    return 1 if read_json(output / "gates.json")["status"] in {"BELOW_GATE", "INVALID", "NOT_RUN"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
