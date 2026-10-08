"""Version two invalid round references; retain the original predictions and score."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from eval.common import digest, read_json, read_jsonl, write_json, write_jsonl
from eval.reference_contract import query_reference_issues
from eval.run import run_section
from eval.validate_gold import load_dataset, require, validate_gold
from evals.project_quality.build_gold import FACTS_HASH
from evals.project_quality.build_query_holdout import listing


def correct():
    root = Path("data/reports/quality-refinement-20261006")
    parent = Path("data/gold/query-generalization-reserved-agent-20261006-v3")
    output = Path("data/gold/query-generalization-reserved-agent-20261006-v5")
    require(not output.exists(), "GOLD_VERSION_ALREADY_EXISTS")
    original = validate_gold(parent, "routing", review_policy="delegated_agent")
    facts = read_json(Path("data/reports/resume-quality-snapshot-20261005-v1/facts.json"))
    require(digest(facts) == FACTS_HASH, "SNAPSHOT_CHANGED")
    fixes = {
        ("query-reserve-backend-db-round", 0): ("二面", "SECOND"),
        ("query-reserve-state-2", 0): ("一面", "FIRST"),
    }
    before = read_jsonl(parent / "routing.jsonl")
    require({(issue["id"], issue["turn"]) for issue in query_reference_issues(before)} == set(fixes),
            "UNEXPECTED_REFERENCE_DEFECTS")
    cases, changes = deepcopy(before), []
    stamp = datetime.now(timezone.utc).isoformat()
    for case in cases:
        for index, turn in enumerate(case["turns"]):
            key = (case["id"], index)
            if key not in fixes:
                continue
            old, new = fixes[key]
            require(turn["expected_plan"]["filters.round"] == old, "REFERENCE_CHANGED")
            turn["expected_plan"]["filters.round"] = new
            scope = {name.removeprefix("filters."): value for name, value in turn["expected_plan"].items()
                     if name.startswith("filters.")}
            recomputed = listing(facts, turn["message"], scope, turn["expected_plan"]["top_n"],
                                 turn["expected_plan"]["page_size"])
            turn["assertions"] = recomputed["assertions"]
            changes.append({"id": case["id"], "turn": index, "field": "filters.round", "before": old, "after": new,
                            "basis": "Original explicit round request and the pre-existing FilterSpec enum contract; IDs/counts recomputed from frozen facts, without prediction input.",
                            "oracle_rows": len(recomputed["assertions"][0]["value"])})
            case["review"]["reference_correction"] = {"reviewer": "Codex", "reviewer_kind": "agent", "reviewed_at": stamp,
                "parent_version": parent.name, "basis": changes[-1]["basis"]}
    require(len(cases) == len(before) == 50 and sum(len(c["turns"]) for c in cases) == 62, "SAMPLE_COUNT_CHANGED")
    require(not query_reference_issues(cases), "INVALID_CORRECTED_REFERENCE")
    # Reconstruct the parent byte-level content except the two authorized references and their oracle assertions/review notes.
    restored = deepcopy(cases)
    for case in restored:
        old_case = next(c for c in before if c["id"] == case["id"])
        case["review"] = old_case["review"]
        for index, turn in enumerate(case["turns"]):
            if (case["id"], index) in fixes:
                turn["expected_plan"]["filters.round"] = fixes[(case["id"], index)][0]
                turn["assertions"] = old_case["turns"][index]["assertions"]
    require(restored == before, "UNAUTHORIZED_GOLD_CHANGE")
    manifest = read_json(parent / "manifest.json")
    manifest.update(version=output.name, frozen_at=stamp, reference_contract="typed_query_plan_v1",
                    parent_version=parent.name, parent_dataset_sha256=digest(original),
                    review_recipe_sha256=digest(Path(__file__).read_bytes()),
                    review_lifecycle="Post-first-inference correction of two invalid typed round references. The original 46/50 score and predictions remain immutable; this rescore is not a new independent model test.",
                    limitations="All fifty scenarios and sixty-two turns retained. Agent-reviewed references, not human gold. Original SQL IDs/counts for two turns were invalid due to display-label enum values and are independently recomputed from frozen facts.")
    output.mkdir()
    write_jsonl(output / "routing.jsonl", cases)
    write_json(output / "reference-corrections.json", {"parent_dataset_sha256": digest(original), "changes": changes,
        "scenarios_retained": 50, "turns_retained": 62, "samples_deleted": 0, "new_model_calls": 0,
        "human_verified": False, "reviewer_kind": "agent", "facts_sha256": FACTS_HASH})
    write_json(output / "manifest.json", manifest)
    write_json(output / "freeze.json", {"dataset_sha256": digest(load_dataset(output)),
        "file_hashes": {p.name: digest(p.read_bytes()) for p in output.iterdir() if p.is_file() and p.name != "freeze.json"}})
    validate_gold(output, "routing", review_policy="delegated_agent")
    prediction = root / "query-reserved.v7.jsonl"
    original_prediction_hash = digest(prediction.read_bytes())
    report = run_section(output, "routing", prediction, root / "query-reserved-reference-corrected",
                         review_policy="delegated_agent", threshold_version="quality_gate_v3")
    require(digest(prediction.read_bytes()) == original_prediction_hash, "PREDICTIONS_CHANGED")
    registry = read_json(root / "reports.json")
    require("query-reference-correction" not in registry, "RESCORE_ALREADY_REGISTERED")
    registry["query-reference-correction"] = {"post_review_rescore": report.as_posix()}
    write_json(root / "reports.json", registry)
    print({"dataset": output.as_posix(), "report": report.as_posix(),
           "task_success": read_json(report / "metrics.json")["task_success_rate"], "new_model_calls": 0})


if __name__ == "__main__":
    correct()
