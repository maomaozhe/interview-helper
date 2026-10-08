"""Freeze a small regression diagnostic for the complete Fast + Pi fallback path."""
from __future__ import annotations

import argparse
from pathlib import Path

from eval.common import digest, read_json, read_jsonl, write_json, write_jsonl
from eval.validate_gold import load_dataset, validate_gold


# Covers task dimensions, ordering, an unsupported entity, stats and missing
# paging state. These are already-seen regressions, not a new generalization set.
SELECTED = (
    "routing-alg-list", "routing-eng-list", "routing-sql-list",
    "routing-verbal-list", "routing-alg-important", "routing-alg-gap",
    "routing-tencent-alg", "routing-redis-ten", "routing-special-0",
    "routing-missing-scope-1",
)


def build(parent: Path, output: Path):
    source = validate_gold(parent, "routing", split="test", review_policy="delegated_agent")
    original = {row["id"]: row for row in read_jsonl(parent / "routing.jsonl")}
    rows = [{**original[key], "split": "dev"} for key in SELECTED]
    output.mkdir(parents=True, exist_ok=False)
    write_jsonl(output / "routing.jsonl", rows)
    manifest = read_json(parent / "manifest.json")
    for key in ("extraction", "task_labels", "dedup", "retrieval", "sql"):
        manifest.pop(key, None)
    manifest.update(version=output.name, routing="routing.jsonl", parent_dataset_sha256=digest(source),
                    parent_routing_sha256=digest((parent / "routing.jsonl").read_bytes()),
                    purpose="Paired end-to-end fallback diagnostic; not a release or unseen traffic score",
                    selection=list(SELECTED))
    write_json(output / "manifest.json", manifest)
    write_json(output / "freeze.json", {
        "dataset_sha256": digest(load_dataset(output)),
        "file_hashes": {p.name: digest(p.read_bytes()) for p in sorted(output.iterdir()) if p.is_file()},
    })
    print({"dataset": str(output), "samples": len(rows), "split": "dev", "independent": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.parent, args.output)
