"""Compare only the same dataset, split, task and snapshot."""
import argparse
from pathlib import Path

from eval.common import read_json, read_jsonl, write_json
from eval.report import _flat
from eval.validate_gold import require


def compare(before: Path, after: Path):
    left, right = read_json(before / "manifest.json"), read_json(after / "manifest.json")
    for key in ("dataset_sha256", "section", "split", "snapshot"):
        require(left.get(key) == right.get(key), f"incomparable runs: {key} differs")
    old, new = _flat(read_json(before / "metrics.json")), _flat(read_json(after / "metrics.json"))
    changes = {key: {"before": old[key], "after": value, "delta": value - old[key]}
               for key, value in new.items() if type(value) in {int, float} and type(old.get(key)) in {int, float}
               and value != old[key]}
    def failures(path):
        return {(r["id"], r.get("pipeline")) for r in read_jsonl(path / "per_sample.jsonl") if r["status"] != "PASS"}
    lost, found = failures(after) - failures(before), failures(before) - failures(after)
    return {"compatible": True, "metric_changes": changes, "new_failures": sorted(lost), "recovered": sorted(found)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.before, args.after)
    write_json(args.output, result)
    print(args.output)


if __name__ == "__main__":
    main()
