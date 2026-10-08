"""Propose evidence-bound alignment; unresolved matches require explicit choices.

This never creates question labels. Gold is already sealed before observations
are compared. Only identical quote text (ignoring numbering/whitespace) aligns
mechanically; other matches are authored explicitly in the decision file.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from eval.common import digest, read_json, read_jsonl, write_jsonl
from eval.validate_gold import require, validate_gold
from evals.agent_gold.build import stamp


def bare(text):
    return re.sub(r"\s+", "", re.sub(r"^[\s\d.、）)（(•\-*]+", "", text)).rstrip("。；;")


def propose(dataset, predictions, output):
    manifest = validate_gold(dataset, "extraction", review_policy="delegated_agent")
    observed = {p["id"]: p for p in read_jsonl(predictions)}
    alignments, todo = [], []
    for gold in manifest["extraction"]:
        pred = observed[gold["id"]].get("result") or {"questions": [], "sessions": [], "followups": []}
        pairs, used = [], set()
        for g in gold["questions"]:
            matches = [p for p in pred["questions"] if bare(g["raw_question"]) == bare(p["raw_question"])
                       and any(a["start_char"] < b["end_char"] and b["start_char"] < a["end_char"]
                               for a in g["source_spans"] for b in p["source_spans"])]
            if len(matches) == 1 and matches[0]["id"] not in used:
                p = matches[0]; used.add(p["id"])
                pairs.append({"gold_id": g["id"], "prediction_id": p["id"], "basis": "Identical original question text and overlapping evidence, ignoring numbering/whitespace."})
            else:
                candidates = [p for p in pred["questions"] if any(a["start_char"] < b["end_char"] and b["start_char"] < a["end_char"]
                              for a in g["source_spans"] for b in p["source_spans"])]
                todo.append({"sample_id": gold["id"], "gold_id": g["id"], "gold": g["raw_question"],
                             "candidates": [{"id": p["id"], "raw": p["raw_question"], "normalized": p.get("normalized_question")} for p in candidates]})
        row = {"id": gold["id"], "prediction_sha256": digest(pred), "human_verified": False,
               "agent_verified": False, "review": {}, "pairs": pairs, "sessions": []}
        if len(gold["sessions"]) == len(pred["sessions"]) == 1:
            row["sessions"] = [{"gold_id": gold["sessions"][0]["id"], "prediction_id": pred["sessions"][0]["id"]}]
        else:
            pqs = {p["id"]: p for p in pred["questions"]}; gqs = {g["id"]: g for g in gold["questions"]}
            mapping = {}
            for pair in pairs:
                gs, ps = gqs[pair["gold_id"]]["session_id"], pqs[pair["prediction_id"]]["session_id"]
                require(mapping.setdefault(gs, ps) == ps, "unaligned session boundaries require review")
            row["sessions"] = [{"gold_id": gs, "prediction_id": ps} for gs, ps in mapping.items()]
        alignments.append(row)
    write_jsonl(output / "alignment.proposals.jsonl", alignments)
    write_jsonl(output / "alignment.review.jsonl", todo)
    print({"identical_source_matches": sum(len(r["pairs"]) for r in alignments), "to_review": len(todo)})


def finalize(proposals, decisions, output):
    rows = read_jsonl(proposals)
    choices = read_json(decisions)
    todo = read_jsonl(proposals.parent / "alignment.review.jsonl")
    require(set(choices) == {r["gold_id"] for r in todo}, "every unresolved alignment needs an explicit decision, including unmatched")
    by_id = {r["id"]: r for r in rows}
    for item in todo:
        decision = choices[item["gold_id"]]
        require(isinstance(decision.get("basis"), str) and decision["basis"].strip(), "alignment choice basis missing")
        if decision.get("prediction_id"):
            require(decision["prediction_id"] in {c["id"] for c in item["candidates"]}, "choice is outside same-source evidence candidates")
            by_id[item["sample_id"]]["pairs"].append({"gold_id": item["gold_id"], **decision})
    for row in rows:
        pairs = row["pairs"]
        require(len({p["gold_id"] for p in pairs}) == len(pairs) and len({p["prediction_id"] for p in pairs}) == len(pairs), "alignment choices must be one-to-one")
        row.update(agent_verified=True, review=stamp("审核同一原文片段和提问意图的一对一对应；不同问题、缺失关键约束及标题误抽取保持未匹配。"),
                   decisions_sha256=digest(decisions.read_bytes()))
    write_jsonl(output, rows)
    print({"aligned": sum(len(r["pairs"]) for r in rows), "documents": len(rows)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("propose", "finalize"))
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--proposals", type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "propose":
        propose(args.dataset, args.predictions, args.output)
    else:
        finalize(args.proposals, args.decisions, args.output)


if __name__ == "__main__":
    main()
