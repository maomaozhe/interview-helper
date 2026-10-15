"""Prepare and finalize fresh, arm-specific source-grounded alignments.

Both arms use the unchanged source-reviewed extraction v3 gold. A completed
31-row final prediction file is required; partials are never scored. Exact
source matches are proposals, and nonexact plus session choices are explicit
Agent decisions. Published-output alignments are not inputs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import re

from eval.common import digest, inside, read_json, read_jsonl, write_json, write_jsonl
from eval.validate_gold import require, validate_gold
from interview_intelligence.ingestion.snapshot import decode_source


GOLD = Path("data/gold/source-reviewed-extraction-20261014-v3")
ROOT = Path("data/reports/source-optimization-20261014-v1")


def bare(value):
    """Only ignore list prefixes, whitespace and terminal sentence punctuation."""
    prefix = r"^(?:\s*(?:(?:\d+|[一二三四五六七八九十]+)[.、)）:]\s*|[-*•]\s*))+"
    return re.sub(r"\s+", "", re.sub(prefix, "", value)).rstrip("。；;")


def stamp(basis):
    return {"reviewer": "Codex/source-optimization-alignment-20261014", "reviewer_kind": "agent",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "guide_version": "annotation_v2_delegated_source_review_20261014", "basis": basis}


def observations(dataset, prediction_path):
    require(prediction_path.is_file() and ".partial." not in prediction_path.name,
            "complete final prediction file required")
    manifest = validate_gold(dataset, "extraction", review_policy="delegated_agent")
    rows = read_jsonl(prediction_path)
    require(len(rows) == len(manifest["extraction"]) == 31, "complete 31-source arm required")
    by_id = {row["id"]: row for row in rows}
    require(len(by_id) == len(rows) and set(by_id) == {g["id"] for g in manifest["extraction"]},
            "prediction IDs must cover each frozen source once")
    for row in rows:
        require(all(row.get("snapshot", {}).get(k) == v for k, v in manifest["snapshot"].items()),
                "fresh arm snapshot mismatch")
        require(type(row.get("failed", False)) is bool, "failed must be boolean")
        if not row.get("failed"):
            result = row.get("result", {})
            require("questions" in result and "sessions" in result, "actual extracted questions and sessions required")
            for key in ("questions", "sessions"):
                require(len({item["id"] for item in result[key]}) == len(result[key]), "duplicate prediction identities")
    return manifest, by_id


def source_candidates(gold, prediction, text):
    gspans = gold["source_spans"] + gold.get("corroborating_spans", [])
    for span in gspans:
        require(text[span["start_char"]:span["end_char"]] == span["quote"], "frozen original evidence changed")
    candidates = []
    for question in prediction.get("questions", []):
        pspans = question.get("source_spans", [])
        grounded = bool(pspans) and all(type(span.get("start_char")) is int and type(span.get("end_char")) is int
            and 0 <= span["start_char"] < span["end_char"] <= len(text)
            and text[span["start_char"]:span["end_char"]] == span.get("quote") for span in pspans)
        if grounded and any(a["start_char"] < b["end_char"] and b["start_char"] < a["end_char"]
                            for a in gspans for b in pspans):
            candidates.append(question)
    return candidates


def prepare(dataset, prediction_path, output, arm):
    manifest, observed = observations(dataset, prediction_path)
    require(not (output / "preparation.json").exists(), "this arm review is already prepared; do not overwrite decisions")
    proposals, unresolved, session_rows = [], [], []
    candidate_index = {}
    for gold in manifest["extraction"]:
        item = observed[gold["id"]]
        prediction = item.get("result") or {}
        questions = [] if item.get("failed") else prediction.get("questions", [])
        source = decode_source(inside(dataset, gold["source_path"]).read_bytes())
        pairs, used = [], set()
        for question in gold["questions"]:
            candidates = source_candidates(question, {"questions": questions}, source)
            candidate_index[question["id"]] = [q["id"] for q in candidates]
            exact = [q for q in candidates if bare(question["raw_question"]) == bare(q["raw_question"])]
            if len(exact) == 1 and exact[0]["id"] not in used:
                q = exact[0]; used.add(q["id"])
                pairs.append({"gold_id": question["id"], "prediction_id": q["id"],
                    "basis": "Identical source task ignoring numbering/whitespace; primary or pre-frozen same-event corroborating evidence overlaps.",
                    "proposal_kind": "exact_source_match"})
            else:
                unresolved.append({"sample_id": gold["id"], "gold_id": question["id"],
                    "gold_session_id": question["session_id"], "gold": question["raw_question"],
                    "gold_spans": question["source_spans"], "corroborating_spans": question.get("corroborating_spans", []),
                    "prediction_failed": item.get("failed", False), "prediction_error": item.get("error_code"),
                    "candidates": [{"id": q["id"], "session_id": q["session_id"], "raw": q["raw_question"],
                                    "normalized": q.get("normalized_question"), "source_spans": q.get("source_spans")} for q in candidates]})
        pqs = {q["id"]: q for q in questions}; gqs = {q["id"]: q for q in gold["questions"]}
        votes = {}
        for pair in pairs:
            key = gqs[pair["gold_id"]]["session_id"]
            votes.setdefault(key, Counter())[pqs[pair["prediction_id"]]["session_id"]] += 1
        session_rows.append({"id": gold["id"], "original_path": gold["original_path"], "failed": item.get("failed", False),
            "gold_sessions": gold["sessions"], "prediction_sessions": prediction.get("sessions", []),
            "exact_match_session_votes": {sid: dict(counts) for sid, counts in votes.items()},
            "prediction_question_locations": [{"id": q["id"], "session_id": q["session_id"],
                "raw": q["raw_question"], "lines": [[s.get("start_line"), s.get("end_line")] for s in q.get("source_spans", [])]} for q in questions]})
        proposals.append({"id": gold["id"], "prediction_sha256": digest(prediction), "human_verified": False,
                          "agent_verified": False, "review": {}, "pairs": pairs, "sessions": []})
    template = {"arm": arm, "predictions_sha256": digest(prediction_path.read_bytes()),
                "exact_proposals_reviewed": False, "question_decisions": {
                    row["gold_id"]: {"prediction_id": None, "basis": ""} for row in unresolved},
                "session_decisions": {row["id"]: {s["id"]: {"prediction_id": None, "basis": ""}
                    for s in row["gold_sessions"]} for row in session_rows}}
    write_jsonl(output / "alignment.proposals.jsonl", proposals)
    write_jsonl(output / "question.review.jsonl", unresolved)
    write_jsonl(output / "session.review.jsonl", session_rows)
    write_json(output / "candidate-index.json", candidate_index)
    write_json(output / "decisions.template.json", template)
    receipt = {"arm": arm, "dataset": str(dataset), "dataset_sha256": digest(manifest),
        "gold_freeze_sha256": digest((dataset / "freeze.json").read_bytes()), "predictions": str(prediction_path),
        "predictions_sha256": digest(prediction_path.read_bytes()), "samples": len(proposals),
        "failed_samples": sum(bool(p.get("failed")) for p in observed.values()),
        "exact_proposals": sum(len(p["pairs"]) for p in proposals), "nonexact_to_review": len(unresolved),
        "prepared_at": datetime.now(timezone.utc).isoformat(), "finalized": False}
    write_json(output / "preparation.json", receipt)
    print(receipt)


def finalize(dataset, prediction_path, output, decisions_path, arm):
    manifest, observed = observations(dataset, prediction_path)
    receipt = read_json(output / "preparation.json")
    require(receipt["arm"] == arm and receipt["dataset_sha256"] == digest(manifest)
            and receipt["predictions_sha256"] == digest(prediction_path.read_bytes()), "prepared input changed")
    decisions = read_json(decisions_path)
    require(decisions.get("arm") == arm and decisions.get("predictions_sha256") == receipt["predictions_sha256"]
            and decisions.get("exact_proposals_reviewed") is True, "explicit review receipt is missing/mismatched")
    proposals = read_jsonl(output / "alignment.proposals.jsonl")
    unresolved = read_jsonl(output / "question.review.jsonl")
    candidates = read_json(output / "candidate-index.json")
    choices = decisions.get("question_decisions", {})
    require({row["gold_id"] for row in unresolved} <= choices.keys() <= candidates.keys(),
            "decide every unresolved task; only existing gold may override exact proposals")
    sessions = decisions.get("session_decisions", {})
    require(set(sessions) == {g["id"] for g in manifest["extraction"]}, "review sessions for each source")
    require(not (output / "alignments.jsonl").exists(), "final alignment already exists; create a new review version")
    gold_map = {g["id"]: g for g in manifest["extraction"]}
    for row in proposals:
        gold = gold_map[row["id"]]; prediction = observed[row["id"]].get("result") or {}
        valid_psids = {s["id"] for s in prediction.get("sessions", [])}
        sid_choices = sessions[row["id"]]
        require(set(sid_choices) == {s["id"] for s in gold["sessions"]}, "every gold session needs a reviewed decision")
        mapping = {}
        for gid, choice in sid_choices.items():
            require(isinstance(choice.get("basis"), str) and choice["basis"].strip(), "session decision basis required")
            pid = choice.get("prediction_id")
            if pid:
                require(pid in valid_psids, "session decision references unknown prediction session")
                mapping[gid] = pid
        require(len(set(mapping.values())) == len(mapping), "cannot merge independent gold sessions by alignment")
        pairs = {p["gold_id"]: p for p in row["pairs"]}
        for q in gold["questions"]:
            gid = q["id"]
            if gid not in choices:
                continue
            choice = choices[gid]
            require(isinstance(choice.get("basis"), str) and choice["basis"].strip(), "question decision basis required")
            pid = choice.get("prediction_id")
            pairs.pop(gid, None)
            if pid:
                require(pid in candidates[gid], "decision is outside valid same-source evidence candidates")
                pairs[gid] = {"gold_id": gid, "prediction_id": pid, "basis": choice["basis"], "proposal_kind": "explicit_agent_decision"}
        pairs = list(pairs.values())
        require(len({p["prediction_id"] for p in pairs}) == len(pairs), "one prediction can match at most one gold task")
        pqs = {q["id"]: q for q in prediction.get("questions", [])}; gqs = {q["id"]: q for q in gold["questions"]}
        for pair in pairs:
            require(mapping.get(gqs[pair["gold_id"]]["session_id"]) == pqs[pair["prediction_id"]]["session_id"],
                    "question pair crosses reviewed session boundaries; explicitly leave it unmatched")
        row.update(pairs=pairs, sessions=[{"gold_id": g, "prediction_id": p, "basis": sid_choices[g]["basis"]} for g, p in mapping.items()],
                   human_verified=False, agent_verified=True,
                   review=stamp("Reviewed this arm's exact source proposals, every unresolved task and original-session boundaries. Merged/split/incomplete tasks stay unmatched; no gold changes after predictions."),
                   decisions_sha256=digest(decisions_path.read_bytes()))
    write_jsonl(output / "alignments.jsonl", proposals)
    write_json(output / "finalization.json", {**receipt, "finalized": True,
        "aligned": sum(len(row["pairs"]) for row in proposals),
        "mapped_sessions": sum(len(row["sessions"]) for row in proposals),
        "alignments_sha256": digest((output / "alignments.jsonl").read_bytes()),
        "decisions_sha256": digest(decisions_path.read_bytes()), "review": stamp("Arm-specific review; original frozen gold unchanged.")})
    print(read_json(output / "finalization.json"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "finalize"))
    parser.add_argument("--arm", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--dataset", type=Path, default=GOLD)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--decisions", type=Path)
    args = parser.parse_args()
    prediction = args.root / f"extraction.{args.arm}.jsonl"
    output = args.root / "review" / args.arm
    if args.action == "prepare":prepare(args.dataset, prediction, output, args.arm)
    else:finalize(args.dataset, prediction, output, args.decisions or output / "decisions.json", args.arm)


if __name__ == "__main__":main()
