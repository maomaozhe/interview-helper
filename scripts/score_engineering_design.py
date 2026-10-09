"""Score chat receipts against a frozen source-backed engineering reference.

Unknown results stay unjudged. Reference recall is not exhaustive corpus recall;
independent source review is still required for task precision and new results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def score(rows, reference):
    known = {x["canonical_question_id"]: x for x in reference["items"]}
    primary = {cid for cid, x in known.items() if x["grade"] == 2}
    families = {known[cid]["task_family"] for cid in primary}
    ids = [x["canonical_question_id"] for x in rows]
    hits = set(ids) & primary
    covered = {known[cid]["task_family"] for cid in hits}
    return {
        "returned": len(ids), "duplicates": len(ids) - len(set(ids)),
        "reference_hits": len(hits), "reference_total": len(primary),
        "reference_recall": len(hits) / len(primary) if primary else None,
        "reference_family_hits": len(covered), "reference_family_total": len(families),
        "leaderboard_hit": any(known[cid]["task_family"] == "leaderboard" for cid in hits),
        "primary_ids": [cid for cid in ids if cid in primary],
        "adjacent_ids": [cid for cid in ids if cid in known and known[cid]["grade"] == 1],
        "negative_ids": [cid for cid in ids if cid in known and known[cid]["grade"] == 0],
        "unjudged_ids": [cid for cid in ids if cid not in known],
        "missed_primary_ids": sorted(primary - hits),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw, reference_raw = args.receipt.read_bytes(), args.reference.read_bytes()
    receipt, reference = json.loads(raw), json.loads(reference_raw)
    turns = []
    for case in receipt["cases"]:
        for index, turn in enumerate(case["turns"]):
            run = turn.get("run", {})
            result = run.get("result") or {}
            semantic_evaluation = turn.get("semantic_evaluation", True)
            turns.append({"case": case["id"], "turn": index, "scope": case["scope"],
                          "semantic_evaluation": semantic_evaluation,
                          "run_id": run.get("run_id") or turn.get("accepted", {}).get("run_id"),
                          "status": run.get("status"), "intent": result.get("intent"),
                          "error": turn.get("error"),
                          "metrics": score(result.get("facts", {}).get("data", []), reference)
                          if semantic_evaluation and run.get("status") == "SUCCEEDED" and result.get("intent") == "SEARCH" else None})
    report = {"version": "engineering_reference_score_v1", "human_verified": False,
              "reference_is_exhaustive_corpus_gold": False,
              "receipt_sha256": hashlib.sha256(raw).hexdigest(),
              "reference_sha256": hashlib.sha256(reference_raw).hexdigest(), "turns": turns}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(report, target, ensure_ascii=False, indent=2)
    print(args.output)


if __name__ == "__main__":
    main()
