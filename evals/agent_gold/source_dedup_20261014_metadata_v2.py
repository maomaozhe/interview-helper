"""Preserve v1; correct snapshot hash semantics in metadata-only v2."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone

from eval.common import digest, write_json
from eval.validate_gold import load_dataset, validate_gold
from evals.agent_gold.source_dedup_20261014 import OUTPUT, ROOT, SNAPSHOT


def main():
    target = ROOT / "data/gold/source-reviewed-dedup-20261014-v2"
    if target.exists():
        raise ValueError("v2 already exists; frozen versions cannot be overwritten")
    parent = validate_gold(OUTPUT, "dedup", review_policy="delegated_agent")
    files = [p for p in OUTPUT.rglob("*") if p.is_file() and p.name not in {"manifest.json", "freeze.json"}]
    for source in files:
        copied = target / source.relative_to(OUTPUT)
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, copied)
    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    stamp = datetime.now(timezone(timedelta(hours=8))).isoformat()
    manifest.update(version=target.name, frozen_at=stamp,
                    supersedes=OUTPUT.name, parent_dataset_sha256=digest(parent),
                    parent_freeze_sha256=digest((OUTPUT / "freeze.json").read_bytes()),
                    facts_sha256=digest(json.loads((SNAPSHOT / "facts.json").read_text(encoding="utf-8"))),
                    facts_file_sha256=digest((SNAPSHOT / "facts.json").read_bytes()),
                    metadata_recipe_sha256=digest(__import__("pathlib").Path(__file__).read_bytes()),
                    metadata_revision={"kind": "snapshot_hash_field_semantics_only", "reviewed_at": stamp,
                                       "basis": "facts_sha256 is canonical parsed-JSON digest used by eval; raw file bytes belong in facts_file_sha256.",
                                       "labels_changed": False, "sources_changed": False,
                                       "labels_first_sealed_before_canonical_audit": True})
    write_json(target / "manifest.json", manifest)
    write_json(target / "freeze.json", {
        "dataset_sha256": digest(load_dataset(target)), "frozen_at": stamp,
        "file_hashes": {p.relative_to(target).as_posix(): digest(p.read_bytes()) for p in sorted(target.rglob("*")) if p.is_file() and p.name != "freeze.json"},
    })
    new = validate_gold(target, "dedup", review_policy="delegated_agent")
    if digest((OUTPUT / "dedup.jsonl").read_bytes()) != digest((target / "dedup.jsonl").read_bytes()):
        raise ValueError("Labels changed in metadata-only revision")
    print(json.dumps({"dataset": target.as_posix(), "pairs": len(new["dedup"]), "labels_changed": False,
                      "facts_sha256": manifest["facts_sha256"], "facts_file_sha256": manifest["facts_file_sha256"]}))


if __name__ == "__main__":
    main()
