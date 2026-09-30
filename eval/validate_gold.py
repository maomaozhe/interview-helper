"""Reject incomplete or leaked evaluation gold instead of inventing metrics."""

import argparse
import json
from pathlib import Path


MINIMUM_TEST_SIZE = {"extraction": 30, "dedup": 150, "retrieval": 50, "routing": 50}


class GoldValidationError(ValueError):
    pass


def validate_gold(dataset: str | Path) -> dict:
    path = Path(dataset) / "manifest.json"
    with path.open(encoding="utf-8") as source:
        manifest = json.load(source)
    for section in MINIMUM_TEST_SIZE:
        for item in manifest.get(section, []):
            if not item.get("human_verified"):
                raise GoldValidationError(f"{section}: every label needs human verification")
    source_split: dict[str, str] = {}
    for item in manifest.get("extraction", []):
        source_hash = item.get("source_hash")
        split = item.get("split")
        if not source_hash or split not in {"dev", "test"}:
            raise GoldValidationError("extraction item missing source_hash or split")
        prior = source_split.setdefault(source_hash, split)
        if prior != split:
            raise GoldValidationError(f"dev/test source leakage: {source_hash}")
    for section, minimum in MINIMUM_TEST_SIZE.items():
        test_items = [item for item in manifest.get(section, []) if item.get("split") == "test"]
        if len(test_items) < minimum:
            raise GoldValidationError(f"{section} needs at least {minimum} verified test items")
    if manifest.get("status") != "frozen":
        raise GoldValidationError("gold manifest must be frozen")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    args = parser.parse_args()
    try:
        manifest = validate_gold(args.dataset)
    except GoldValidationError as error:
        print(f"Gold set incomplete: {error}")
        return 1
    print(f"Gold set valid: {manifest['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
