"""Assemble immutable run references without relaxing or replacing failed gates."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import re

from eval.common import digest, read_json, read_jsonl, write_json
from eval.compare import compare
from eval.reference_contract import query_reference_issues
from eval.release import check_bundle
from eval.validate_gold import load_dataset, require, validate_gold


ROOT = Path("data/reports/quality-refinement-20261006")
DOCS = Path("docs/verification")


def summarize():
    registry = read_json(ROOT / "reports.json")
    previous = read_json(DOCS / "2026-10-06-quality-bundle-v3.json")
    extraction_gold = "data/gold/evaluation-agent-reviewed-20261005-v5"
    task_seen_gold = "data/gold/task-generalization-agent-20261006-v2"
    challenge_gold = "data/gold/task-engineering-challenge-agent-20261006-v1"
    query_seen_gold = "data/gold/query-generalization-agent-20261006-v1"
    query_first_gold = "data/gold/query-generalization-reserved-agent-20261006-v3"
    query_corrected_gold = "data/gold/query-generalization-reserved-agent-20261006-v5"
    query_forward_gold = "data/gold/query-forward-reserved-agent-20261006-v4"
    def entry(dataset, report, section=None):
        return {"dataset": dataset, "report": report, **({"section": section} if section else {})}
    modules = {k: previous["modules"][k] for k in ("dedup", "retrieval", "sql")}
    modules.update(
        extraction=entry(extraction_gold, registry["extraction"]["v5-metadata-v2"]),
        task_labels=entry(challenge_gold, registry["challenge"]["engineering-challenge-200-task_annotation_v7"]),
        routing=entry(query_corrected_gold, registry["query-reference-correction"]["post_review_rescore"]))
    regressions = {
        "task-labels-211": entry(extraction_gold, registry["labels"]["regression-211"], "task_labels"),
        "task-labels-seen-source-200": entry(task_seen_gold, registry["labels"]["seen-source-200"], "task_labels"),
        "query-known-50": entry(query_seen_gold, registry["query-regression"]["query-regression"], "routing"),
        "query-reserved-original-first-score": entry(query_first_gold, registry["query-reserved"]["query-reserved"], "routing"),
    }
    bundle = {"schema": "project_quality_bundle_v1", "as_of": previous["as_of"],
              "threshold_version": "quality_gate_v3", "modules": modules, "required_regressions": regressions,
              "scope": "Offline candidates. The routing primary is a post-first-inference reference correction; its original first score is also required. Existing task regressions remain required. Dedup/retrieval/SQL evidence is carried forward without new calls. Experimental prompts remain disabled by default."}
    write_json(DOCS / "2026-10-06-quality-bundle-v4.json", bundle)
    gate = check_bundle(bundle, Path("."))
    require(all(item["status"] not in {"INVALID", "MISSING"} for item in (*gate["modules"].values(), *gate["required_regressions"].values())),
            "INVALID_BUNDLE_EVIDENCE")
    write_json(DOCS / "2026-10-06-quality-gate-v4.json", gate)
    pairs = {
        "extraction-v4-v5": (previous["modules"]["extraction"]["report"], modules["extraction"]["report"]),
        "task-regression-211-v6-v7": (previous["required_regressions"]["task-labels-211"]["report"], regressions["task-labels-211"]["report"]),
        "task-seen-200-v6-v7": (previous["modules"]["task_labels"]["report"], regressions["task-labels-seen-source-200"]["report"]),
        "task-challenge-200-v6-v7": tuple(registry["challenge"][f"engineering-challenge-200-task_annotation_v{v}"] for v in (6, 7)),
        "query-known-50-v6-v7": (previous["modules"]["routing"]["report"], regressions["query-known-50"]["report"]),
    }
    diffs = {}
    for name, (before, after) in pairs.items():
        result = compare(Path(before), Path(after))
        file = ROOT / f"diff.{name}.json"
        write_json(file, result)
        diffs[name] = {"before": before, "after": after, "diff": file.as_posix(), "sha256": digest(file.read_bytes())}
    # Compare topic accuracy on exactly the same matched Gold questions as well as each run's full matches.
    def attributes(path):
        return {f"{row['id']}:{check['id']}": check["status"] for row in read_jsonl(Path(path) / "per_sample.jsonl")
                for check in row["checks"] if check["id"].startswith("attribute.")}
    old, new = map(attributes, pairs["extraction-v4-v5"])
    common = {}
    for field in ("topic_l1", "topic_l2", "question_type"):
        ids = {key for key in old.keys() & new.keys() if key.endswith("." + field)}
        common[field] = {"samples": len(ids), "before_correct": sum(old[k] == "PASS" for k in ids),
                         "after_correct": sum(new[k] == "PASS" for k in ids)}
    write_json(ROOT / "extraction.common-match-diff.json", common)
    # Audit the challenge independently against both previously observed task sets.
    prior = load_dataset(extraction_gold)["task_labels"] + load_dataset(task_seen_gold)["task_labels"]
    challenge = validate_gold(challenge_gold, "task_labels", review_policy="delegated_agent")["task_labels"]
    facts = read_json(Path("data/reports/resume-quality-snapshot-20261005-v1/facts.json"))
    canonical_by_occurrence = {o["id"]: o["canonical_question_id"] for o in facts["questions"]}
    def values(rows, field):
        if field == "canonical_question_id":
            return {r.get(field) or canonical_by_occurrence[r["occurrence_id"]] for r in rows}
        return {r[field] for r in rows}
    overlap = {field: len(values(prior, field) & values(challenge, field))
               for field in ("occurrence_id", "canonical_question_id", "raw_question")}
    bare = lambda text: re.sub(r"\W", "", re.sub(r"^\s*\d+[.、)）]?", "", text)).casefold()
    overlap["normalized_question_text"] = len({bare(r["raw_question"]) for r in prior}
                                              & {bare(r["raw_question"]) for r in challenge})
    overlapping = []
    for current in challenge:
        for earlier in prior:
            if current["source_hash"] != earlier["source_hash"]:
                continue
            if any(a["start_char"] < b["end_char"] and b["start_char"] < a["end_char"]
                   for a in current["source_spans"] for b in earlier["source_spans"]):
                overlapping.append([current["id"], earlier["id"]])
    require(not any(overlap.values()) and not overlapping, "CHALLENGE_TASK_LEAKAGE")
    challenge_audit = {"prior_samples": len(prior), "challenge_samples": len(challenge), "exact_overlap": overlap,
        "overlapping_source_spans": overlapping, "shared_source_documents": len(values(prior, "source_hash") & values(challenge, "source_hash")),
        "source_disjoint": False, "classes": dict(Counter(r["response_form"] + "/" + r["coding_focus"] for r in challenge)),
        "strict_engineering_positives": sum(r["coding_focus"] == "ENGINEERING" for r in challenge),
        "scope": "Cue-stratified unseen questions in a seen corpus; not unseen sources or random traffic."}
    write_json(ROOT / "engineering-challenge.disjointness.audit.json", challenge_audit)
    forward = validate_gold(query_forward_gold, "routing", review_policy="delegated_agent")
    require(not query_reference_issues(forward["routing"]), "INVALID_FORWARD_REFERENCE")
    forward_ids = {row["id"] for row in forward["routing"]}
    forward_predictions = sum(row.get("id") in forward_ids for file in ROOT.glob("query*.jsonl") for row in read_jsonl(file))
    require(forward_predictions == 0, "FORWARD_RESERVE_ALREADY_USED_IN_THIS_ITERATION")
    forward_audit = {"dataset": query_forward_gold, "dataset_sha256": digest(forward), "scenarios": len(forward["routing"]),
        "turns": sum(len(r["turns"]) for r in forward["routing"]), "typed_reference_issues": [], "model_calls": 0,
        "predictions_in_this_iteration": forward_predictions,
        "scope": "New authored composite scenarios sealed for the next iteration; not natural traffic or semantic-family independence."}
    write_json(ROOT / "query-forward.reference.audit.json", forward_audit)
    reports = {}
    for phase, entries in registry.items():
        for name, path in entries.items():
            folder = Path(path)
            metadata = read_json(folder / "manifest.json")
            reports[f"{phase}/{name}"] = {"report": path, "manifest": metadata,
                "metrics": read_json(folder / "metrics.json"), "gate": read_json(folder / "gates.json")["status"],
                "manifest_file_sha256": digest((folder / "manifest.json").read_bytes()),
                "metrics_file_sha256": digest((folder / "metrics.json").read_bytes())}
    datasets = {name: {"dataset_sha256": digest(load_dataset(name)), "freeze_file_sha256": digest((Path(name) / "freeze.json").read_bytes())}
                for name in (extraction_gold, task_seen_gold, challenge_gold, query_seen_gold, query_first_gold, query_corrected_gold, query_forward_gold)}
    evidence = {"schema": "quality_refinement_evidence_v1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": gate["snapshot"], "threshold_version": "quality_gate_v3", "review_policy": "delegated_agent", "human_verified": False,
        "protocols": {name: {"path": (ROOT / name).as_posix(), "sha256": digest((ROOT / name).read_bytes())} for name in ("protocol.json", "protocol.v2.json")},
        "reports": reports, "datasets": datasets, "regression_diffs": diffs, "extraction_common_matches": common,
        "challenge_audit": challenge_audit, "forward_reserve": forward_audit,
        "reference_correction": {"original_score": .92, "rescore": .96, "new_model_calls": 0, "scenarios_deleted": 0,
            "original_dataset": query_first_gold, "corrected_dataset": query_corrected_gold,
            "prediction_sha256": digest((ROOT / "query-reserved.v7.jsonl").read_bytes()),
            "scope": "Post-inference annotation audit; not model improvement or a fresh independent test."},
        "release": {"bundle": (DOCS / "2026-10-06-quality-bundle-v4.json").as_posix(), "gate": (DOCS / "2026-10-06-quality-gate-v4.json").as_posix(),
            "status": gate["status"], "blockers": gate["blockers"]},
        "current_worktree_hashes": {name: digest(Path(name).read_bytes()) for name in (
            "prompts/extract_question_v5.md", "prompts/task_annotation_v7.md", "prompts/query_agent_v7.md", "eval/reference_contract.py",
            "eval/validate_gold.py", "src/interview_intelligence/ingestion/pipeline.py", "src/interview_intelligence/contracts/__init__.py")}}
    for name in ("deployment.json", "pytest-summary.json"):
        if (ROOT / name).exists():
            evidence[name.removesuffix(".json")] = read_json(ROOT / name)
    write_json(DOCS / "2026-10-06-quality-refinement.evidence.json", evidence)
    print({"gate": gate["status"], "blockers": gate["blockers"], "common_matches": common, "challenge_audit": challenge_audit,
           "reserve": forward_audit})


if __name__ == "__main__":
    summarize()
