"""Compare only the same dataset, split, task and snapshot."""
import argparse
from pathlib import Path

from eval.common import digest, read_json, read_jsonl, write_json
from eval.report import _flat
from eval.release import verify_report
from eval.validate_gold import require


def _answer_review_coverage(folder, metadata):
    if metadata.get("answer_reviews_sha256") is None:
        return []
    reviews = read_json(folder / "answer_reviews.json")["reviews"]
    return sorted([{"case_id": row["case_id"], "turn_index": row["turn_index"],
                    "reviewer_kind": row["reviewer_kind"],
                    "assessed_criteria": sorted(key for key, value in row["assessments"].items()
                                                if type(value.get("passed")) is bool)} for row in reviews],
                  key=lambda row: (row["case_id"], row["turn_index"]))


def compare(before: Path, after: Path):
    before, after = Path(before), Path(after)
    left, right = read_json(before / "manifest.json"), read_json(after / "manifest.json")
    for key in ("dataset_sha256", "section", "split", "snapshot"):
        require(left.get(key) == right.get(key), f"incomparable runs: {key} differs")
    verified = []
    for path in (before, after):
        result = verify_report(path, strict=(path / "integrity.json").is_file())
        if result["status"] == "INVALID" and result["errors"] == ["SCORER_VERSION_MISMATCH"]:
            # An older evaluator can be inspected, but its scores cannot be
            # certified by silently running today's evaluator on its behalf.
            result = verify_report(path, strict=False)
        require(result["status"] != "INVALID", f"invalid report {path.name}: {result['errors']}")
        verified.append(result)

    def protocol(metadata, config, folder):
        scoring = metadata.get("scoring", {})
        return {"scorer": scoring.get("scorer"), "protocol": scoring.get("protocol", {}),
                "review_policy": metadata.get("review_policy", "human"),
                "threshold_version": config.get("threshold_version", "quality_gate_v2"),
                "default_pipeline": config.get("default_pipeline", "HYBRID"),
                "source_evidence_contract": metadata.get("source_evidence_contract"),
                "reference_scope": metadata.get("reference_scope"),
                "answer_review_coverage": _answer_review_coverage(folder, metadata)}

    policies = [protocol(meta, result["config"], folder)
                for meta, result, folder in zip((left, right), verified, (before, after))]
    policy_changes = {key: {"before": policies[0][key], "after": policies[1][key]}
                      for key in policies[0] if policies[0][key] != policies[1][key]}
    comparable = not policy_changes and all(v["strict_verified"] for v in verified)
    old, new = _flat(read_json(before / "metrics.json")), _flat(read_json(after / "metrics.json"))
    changes = {}
    for key in sorted(old.keys() | new.keys()):
        old_present, new_present = key in old, key in new
        previous, current = old.get(key), new.get(key)
        if old_present == new_present and previous == current:
            continue
        change = {"before": previous, "after": current, "before_present": old_present, "after_present": new_present}
        if not old_present:
            change["transition"] = "METRIC_ADDED"
        elif not new_present:
            change["transition"] = "METRIC_REMOVED"
        elif previous is None:
            change["transition"] = "NEWLY_MEASURED"
        elif current is None:
            change["transition"] = "NO_LONGER_MEASURED"
        else:
            change["transition"] = "VALUE_CHANGED"
        if type(previous) in {int, float} and type(current) in {int, float}:
            change["delta" if comparable else "numeric_difference"] = current - previous
        changes[key] = change

    rows = [{(r["id"], r.get("pipeline")): r for r in read_jsonl(path / "per_sample.jsonl")}
            for path in (before, after)]
    failures = [{key for key, row in group.items() if row["status"] != "PASS"} for group in rows]
    lost, found = failures[1] - failures[0], failures[0] - failures[1]
    assertions = []
    for identity in sorted(rows[0].keys() | rows[1].keys(), key=repr):
        checks = [{c["id"]: c for c in group.get(identity, {}).get("checks", [])} for group in rows]
        for key in sorted(checks[0].keys() | checks[1].keys()):
            previous, current = checks[0].get(key), checks[1].get(key)
            if digest(previous) != digest(current):
                assertions.append({"id": identity[0], "pipeline": identity[1], "assertion": key,
                                   "before": previous, "after": current})
    return {"compatible": not policy_changes, "quality_comparable": comparable,
            "protocol_changes": policy_changes, "metric_changes": changes,
            "new_failures": sorted(lost, key=repr), "recovered": sorted(found, key=repr),
            "assertion_changes": assertions, "verification": [r["status"] for r in verified],
            "scope": "Paired observations only. Protocol changes, legacy evidence and newly measured values are not quality gains."}


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
