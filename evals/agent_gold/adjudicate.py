"""Apply explicit user decisions to a new sealed version, preserving provenance."""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
from shutil import copyfile

from eval.common import digest, inside, read_json, read_jsonl, write_json, write_jsonl
from eval.validate_gold import load_dataset, require, validate_gold
from evals.agent_gold.build import review_row

ALLOWED = {
    "review-cpp-template": {"ALGORITHM", "ENGINEERING", "MIXED", "UNKNOWN"},
    "review-boss-company": {"BOSS直聘", None},
    "review-lru-task": {"ALGORITHM", "PRINCIPLE", "SYSTEM_DESIGN", "OTHER"},
}


def apply_decisions(base: Path, decisions_file: Path | None, output: Path, provenance_file: Path | None = None):
    require(not output.exists(), "gold version already exists; create a new version")
    validate_gold(base, "extraction", review_policy="delegated_agent")
    validate_gold(base, "task_labels", review_policy="delegated_agent")
    seal = read_json(base / "freeze.json")
    decisions = read_json(decisions_file) if decisions_file else []
    require(isinstance(decisions, list) and (bool(decisions) or provenance_file is not None), "explicit user decisions required")
    note = read_json(provenance_file) if provenance_file else None
    if note is not None:
        require(isinstance(note, dict) and isinstance(note.get("manifest_updates"), dict)
                and bool(note["manifest_updates"])
                and set(note["manifest_updates"]) <= {"blind_review", "review_lifecycle", "evaluation_scope"},
                "provenance revision cannot change labels, identity or authorization")
    require(all(isinstance(d, dict) and isinstance(d.get("id"), str) for d in decisions), "invalid decision record")
    require(len({d["id"] for d in decisions}) == len(decisions), "duplicate user decision")
    pending = read_jsonl(base / "needs_user_review.jsonl")
    unresolved = {r["id"]: r for r in pending}
    extraction = read_jsonl(base / "extraction.jsonl")
    labels = read_jsonl(base / "task_labels.jsonl")
    by_id = {r["id"]: r for r in extraction}
    for decision in decisions:
        key = decision["id"]
        require(key in unresolved and key in ALLOWED, "decision is not a pending review")
        require((decision.get("value") is None or isinstance(decision.get("value"), str))
                and decision.get("value") in ALLOWED[key], "invalid user adjudication value")
        require(decision.get("reviewer_kind") == "human" and decision.get("evidence") == "direct_user_reply"
                and all(isinstance(decision.get(k), str) and decision[k].strip()
                        for k in ("answer", "reviewed_at", "question_item_id")), "direct user reply provenance missing")
        item = unresolved.pop(key)
        field_review = deepcopy(decision)
        field_review.update(source_sample_id=item["sample_id"], source_quote=item["quote"])
        if key == "review-cpp-template":
            candidate = item["candidate"]
            require(candidate["id"] not in {r["id"] for r in labels}, "task sample already exists")
            row = {**candidate, **review_row(candidate["id"], candidate["source_hash"],
                ["random_source_sample", "task_labels", "user_field_adjudication"],
                "原文明示C++代码实现；任务焦点采用用户对该歧义项的明确裁决。"),
                "response_form": "CODE", "coding_focus": decision["value"], "reviewed_queue_index": 151,
                "field_reviews": {"coding_focus": field_review}}
            labels.append(row)
        elif key == "review-boss-company":
            session = next(s for s in by_id[item["sample_id"]]["sessions"] if s["id"] == item["session_id"])
            session["metadata"]["company"] = decision["value"]
            session["metadata_to_review"].remove("company")
            session.setdefault("field_reviews", {})["company"] = field_review
            session["review_basis"] += " 公司身份经用户明确确认。"
        else:
            question = next(q for q in by_id[item["sample_id"]]["questions"] if q["id"] == item["question_id"])
            question["question_type"] = decision["value"]
            if decision["value"] != "OTHER":
                question["attributes_to_review"].remove("question_type")
            question.setdefault("field_reviews", {})["question_type"] = field_review
            question["review_basis"] += " 作答方式经用户明确裁决。"

    output.mkdir(parents=True)
    for name in seal["file_hashes"]:
        target = inside(output, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(inside(base, name), target)
    manifest = read_json(base / "manifest.json")
    manifest.update(version=output.name, supersedes=base.name, parent_dataset_sha256=seal["dataset_sha256"])
    if decisions_file:
        manifest["user_decisions_sha256"] = digest(decisions_file.read_bytes())
    if note is not None:
        manifest.update(note["manifest_updates"])
        manifest["provenance_note_sha256"] = digest(provenance_file.read_bytes())
        write_json(output / "provenance_note.json", note)
    manifest.setdefault("human_field_adjudications", []).extend(decisions)
    write_json(output / "manifest.json", manifest)
    write_jsonl(output / "extraction.jsonl", extraction)
    write_jsonl(output / "task_labels.jsonl", labels)
    write_jsonl(output / "needs_user_review.jsonl", list(unresolved.values()))
    if decisions_file:
        history_file = base / "human_review_decisions.json"
        history = read_json(history_file) if history_file.is_file() else []
        write_json(output / "human_review_decisions.json", history + decisions)
    write_json(output / "freeze.json", {"dataset_sha256": digest(load_dataset(output)),
        "file_hashes": {p.relative_to(output).as_posix(): digest(p.read_bytes())
                       for p in sorted(output.rglob("*")) if p.is_file() and p.name != "freeze.json"}})
    validate_gold(output, "extraction", review_policy="delegated_agent")
    validate_gold(output, "task_labels", review_policy="delegated_agent")
    return {"dataset": str(output), "resolved": len(decisions), "pending": len(unresolved),
            "task_labels": len(labels), "human_verified_rows": sum(r["human_verified"] for r in extraction + labels)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--provenance-note", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(apply_decisions(args.base, args.decisions, args.output, args.provenance_note))


if __name__ == "__main__":
    main()
