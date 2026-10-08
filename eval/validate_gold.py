"""Validate independent, versioned datasets; drafts never qualify for release."""
from __future__ import annotations

import argparse
import re
from datetime import date, datetime
from pathlib import Path

from eval.common import SECTIONS, digest, inside, read_json, read_jsonl

MINIMUM_TEST_SIZE = {"extraction": 30, "dedup": 150, "retrieval": 50, "routing": 50,
                     "task_labels": 200, "sql": 20}
OPS = {"eq", "ne", "set_eq", "contains", "subset", "length", "unique", "approx", "lte", "gte", "exists", "not_empty"}
FORMS = {"VERBAL", "CODE", "SQL", "UNKNOWN"}
FOCUSES = {"ALGORITHM", "ENGINEERING", "MIXED", "NONE", "UNKNOWN"}


class GoldValidationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise GoldValidationError(message)


def reviewed(review):
    require(isinstance(review, dict) and all(isinstance(review.get(k), str) and review[k].strip()
            for k in ("reviewer", "reviewed_at", "guide_version")), "human review provenance missing")
    try:
        stamp = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
        require(stamp.tzinfo is not None, "reviewed_at needs a timezone")
    except ValueError as error:
        raise GoldValidationError("invalid reviewed_at") from error


def load_dataset(dataset: Path | str) -> dict:
    root = Path(dataset)
    manifest = read_json(root / "manifest.json")
    require(isinstance(manifest, dict) and isinstance(manifest.get("version"), str), "dataset version missing")
    for section in SECTIONS:
        rows = manifest.get(section, [])
        if isinstance(rows, str):
            rows = read_jsonl(inside(root, rows))
        require(isinstance(rows, list) and all(isinstance(r, dict) for r in rows), f"{section}: expected an array or JSONL file")
        manifest[section] = rows
    return manifest


def assertions_valid(assertions):
    require(isinstance(assertions, list) and bool(assertions), "result assertions required")
    ids = set()
    for assertion in assertions:
        require(isinstance(assertion, dict), "assertion must be an object")
        key = assertion.get("id")
        require(isinstance(key, str) and key and key not in ids, "assertion IDs must be unique")
        ids.add(key)
        require(assertion.get("op") in OPS and isinstance(assertion.get("path"), str), "invalid assertion operation/path")
        if assertion["op"] not in {"unique", "exists", "not_empty"}:
            require("value" in assertion, "assertion expected value missing")
        if assertion["op"] in {"set_eq", "subset"}:
            require(isinstance(assertion["value"], list), "set assertions need an expected list")
        if assertion["op"] == "length":
            require(type(assertion["value"]) is int and assertion["value"] >= 0, "length needs a nonnegative integer")
        require(assertion.get("dimension", "result") in {"result", "process", "efficiency", "risk"}, "invalid rubric dimension")


def _validate_item(root, section, item, *, formal, labels, sources):
    if item.get("source_hash"):
        require(bool(re.fullmatch(r"[0-9a-f]{64}", item["source_hash"])), "invalid source SHA-256")
    if sources and item.get("source_path"):
        file = inside(root, item["source_path"])
        require(file.is_file() and digest(file.read_bytes()) == item.get("source_hash"), "source snapshot hash mismatch")
    if not labels:
        return
    if section == "extraction":
        require(item.get("sample_kind") in {"positive", "negative"}, "extraction sample_kind missing")
        require(item.get("source_hash") and item.get("source_path"), "extraction source snapshot required")
        require(isinstance(item.get("questions"), list) and isinstance(item.get("sessions"), list), "extraction questions/sessions required")
        sessions = [s["id"] for s in item["sessions"]]
        require(len(sessions) == len(set(sessions)), "duplicate gold session ID")
        question_ids = [q["id"] for q in item["questions"]]
        require(len(question_ids) == len(set(question_ids)), "duplicate gold question ID")
        require(bool(item["questions"]) == (item["sample_kind"] == "positive"), "positive/negative extraction labels inconsistent")
        if formal:
            require(all(isinstance(s.get("metadata"), dict) and all(k in s["metadata"] for k in ("company", "round", "position")) for s in item["sessions"]), "gold session metadata missing")
        for session in item["sessions"]:
            pending = session.get("metadata_to_review", [])
            require(isinstance(pending, list) and len(pending) == len(set(pending))
                    and set(pending) <= {"company", "round", "position", "interview_date", "publish_date"},
                    "invalid pending extraction metadata")
            require(not pending or bool(session.get("review_basis")), "pending metadata needs source review basis")
        for question in item["questions"]:
            require(question.get("session_id") in sessions and question.get("raw_question"), "question session/text missing")
            pending = question.get("attributes_to_review", [])
            require(isinstance(pending, list) and len(pending) == len(set(pending))
                    and set(pending) <= {"topic_l1", "topic_l2", "question_type"}, "invalid pending extraction attributes")
            require(not pending or bool(question.get("review_basis")), "pending attributes need source review basis")
            if formal:
                require(all(k in question for k in ("topic_l1", "topic_l2", "question_type", "source_spans")), "gold question attributes missing")
                require(bool(question["source_spans"]), "gold question evidence missing")
                require(all(isinstance(question[k], str) and question[k] for k in ("topic_l1", "topic_l2", "question_type")), "gold question attributes empty")
                require(question["raw_question"] == "\n".join(s["quote"] for s in question["source_spans"]), "gold raw question differs from evidence")
            if sources:
                from interview_intelligence.ingestion.snapshot import decode_source
                text = decode_source(inside(root, item["source_path"]).read_bytes())
                for span in question.get("source_spans", []):
                    start, end = span.get("start_char"), span.get("end_char")
                    require(type(start) is int and type(end) is int and 0 <= start < end <= len(text)
                            and text[start:end] == span.get("quote"), "gold source span mismatch")
        for link in item.get("followups", []):
            require(link.get("source_id") in question_ids and link.get("target_id") in question_ids, "gold followup ID unknown")
            qmap = {q["id"]: q for q in item["questions"]}
            require(qmap[link["source_id"]]["session_id"] == qmap[link["target_id"]]["session_id"], "gold followup crosses sessions")
            if formal:
                require(bool(link.get("evidence_spans")), "gold followup evidence missing")
                if sources:
                    for span in link["evidence_spans"]:
                        start, end = span.get("start_char"), span.get("end_char")
                        require(type(start) is int and type(end) is int and 0 <= start < end <= len(text) and text[start:end] == span.get("quote"), "gold followup evidence mismatch")
    elif section == "task_labels":
        require(item.get("response_form") in FORMS and item.get("coding_focus") in FOCUSES, "task labels missing/invalid")
        if formal:
            require(item.get("source_hash") and item.get("source_path") and item.get("raw_question") and item.get("source_spans"), "task label source evidence missing")
            if sources:
                from interview_intelligence.ingestion.snapshot import decode_source
                text = decode_source(inside(root, item["source_path"]).read_bytes())
                for span in item["source_spans"]:
                    start, end = span.get("start_char"), span.get("end_char")
                    require(type(start) is int and type(end) is int and 0 <= start < end <= len(text) and text[start:end] == span.get("quote"), "task label source span mismatch")
    elif section == "dedup":
        require(item.get("label") in {"SAME", "RELATED", "DIFFERENT"}, "invalid dedup label")
        require(all(isinstance(item.get(k), dict) and item[k].get("id") and item[k].get("text") for k in ("left", "right")), "dedup question identities/text missing")
        require(item["left"]["id"] != item["right"]["id"], "dedup self pair")
    elif section == "retrieval":
        require(isinstance(item.get("query"), str) and item["query"], "retrieval query missing")
        require(isinstance(item.get("relevance"), dict) and all(type(v) is int and v in {0, 1, 2} for v in item["relevance"].values()), "invalid relevance grades")
        positive = any(v > 0 for v in item["relevance"].values())
        require(item.get("sample_kind") == ("positive" if positive else "negative"), "retrieval positive/negative labels inconsistent")
        require(isinstance(item.get("filters", {}), dict), "retrieval filters invalid")
    elif section in {"routing", "sql"}:
        turns = item.get("turns", [item])
        require(isinstance(turns, list) and bool(turns), "empty conversation")
        for turn in turns:
            assertions_valid(turn.get("assertions"))
            require(isinstance(turn.get("expected_plan", {}), dict), "expected_plan must be an object")
            if section == "routing":
                require(turn.get("message"), "query message missing")
                require(all(isinstance(turn.get(k, []), list) and all(isinstance(v, str) for v in turn.get(k, []))
                            for k in ("required_tools", "forbidden_tools", "tool_order")), "invalid tool constraints")
                alternatives = turn.get("allowed_plans", [])
                require(isinstance(alternatives, list) and all(isinstance(p, list) and all(isinstance(t, str) for t in p) for p in alternatives), "invalid alternative tool plans")
            else:
                require(isinstance(turn.get("request"), dict), "SQL list request missing")


def canonical_mapping(manifest, *, formal=False):
    mapping = manifest.get("canonical_mapping", {})
    if manifest.get("id_namespace") == "canonical":
        require(not formal or manifest.get("kind") == "fixture", "formal relevance must use independent gold IDs")
        return {}
    entries = mapping.get("items", []) if isinstance(mapping, dict) else []
    ids, reverse, groups = {}, {}, {}
    for item in entries:
        gold, canonical, group = item["gold_question_id"], item["canonical_question_id"], item["equivalence_group"]
        require(gold not in ids, "duplicate canonical mapping gold ID")
        require(reverse.setdefault(canonical, group) == group, "different gold equivalence groups merged by canonical mapping")
        require(groups.setdefault(group, canonical) == canonical, "gold equivalence group split by canonical mapping")
        ids[gold] = canonical
    if formal and manifest["retrieval"]:
        require(entries and mapping.get("corpus_revision") == manifest.get("snapshot", {}).get("corpus_revision"), "canonical mapping snapshot missing/mismatch")
        reviewed(mapping.get("review"))
    for item in manifest["retrieval"]:
        require(set(item.get("relevance", {})) <= ids.keys(), "relevance gold ID missing canonical mapping")
    return ids


def validate_gold(dataset: str | Path, section: str | None = None, *, split="test", require_labels=True, verify_sources=True,
                  review_policy="human") -> dict:
    require(split in {"dev", "test"}, "split must be dev or test")
    require(review_policy in {"human", "delegated_agent"}, "unknown review policy")
    manifest = load_dataset(dataset)
    freeze = Path(dataset) / "freeze.json"
    if freeze.exists():
        seal = read_json(freeze)
        require(seal.get("dataset_sha256") == digest(manifest), "frozen dataset content changed")
        for name, expected in seal.get("file_hashes", {}).items():
            file = inside(Path(dataset), name)
            require(file.is_file() and digest(file.read_bytes()) == expected, "frozen label file changed")
    if review_policy == "delegated_agent":
        authorization = manifest.get("review_authorization", {})
        require(authorization.get("policy") == "delegated_agent" and authorization.get("user_instruction")
                and authorization.get("thread_id"), "delegated review authorization provenance missing")
    sections = [section] if section else ["extraction", "dedup", "retrieval", "routing"] + [s for s in ("task_labels", "sql") if manifest[s]]
    require(all(s in SECTIONS for s in sections), "unknown evaluation section")
    reference_contract = manifest.get("reference_contract")
    require(reference_contract in {None, "typed_query_plan_v1"}, "unknown query reference contract")
    split_by_group = {}
    for name in SECTIONS:
        for item in manifest[name]:
            item_split = item.get("split")
            require(item_split in {"dev", "test"}, f"{name}: split missing")
            for group in (item.get("group_id"), item.get("source_hash")):
                if group:
                    require(split_by_group.setdefault(group, item_split) == item_split, f"dev/test source leakage: {group}")
    formal = split == "test"
    for name in sections:
        rows = manifest[name]
        selected = [r for r in rows if r["split"] == split]
        positives = [r for r in selected if name not in {"extraction", "retrieval"} or r.get("sample_kind", "positive") == "positive"]
        if formal:
            require(len(positives) >= MINIMUM_TEST_SIZE[name], f"{name} needs at least {MINIMUM_TEST_SIZE[name]} verified test items")
        else:
            require(bool(selected), f"{name}: no dev samples")
        ids, sources, pairs, questions = set(), set(), set(), {}
        for item in rows:
            key = item.get("id")
            require(isinstance(key, str) and key and key not in ids, f"{name}: duplicate/missing sample ID")
            ids.add(key)
            require(isinstance(item.get("group_id"), str) and item["group_id"], f"{name}: group_id missing")
            require(type(item.get("human_verified")) is bool, f"{name}: human_verified must be boolean")
            if item["human_verified"]:
                reviewed(item.get("review"))
            if formal and item["split"] == "test":
                if review_policy == "human":
                    require(item["human_verified"], f"{name}: every test label needs human verification")
                else:
                    require(item.get("agent_verified") is True and item["human_verified"] is False,
                            f"{name}: delegated labels must identify agent review without claiming human verification")
                    reviewed(item.get("review"))
                    require(item["review"].get("reviewer_kind") == "agent" and bool(item["review"].get("basis")),
                            f"{name}: agent review basis missing")
            if name == "extraction":
                source = item.get("source_hash")
                require(source not in sources, "duplicate extraction source document")
                sources.add(source)
            if name == "dedup" and "left" in item and "right" in item:
                pair = tuple(sorted((item["left"]["id"], item["right"]["id"])))
                require(pair not in pairs, "duplicate/reversed dedup pair")
                pairs.add(pair)
                for question in pair:
                    require(questions.setdefault(question, item["split"]) == item["split"], "dev/test dedup question leakage")
            if item["split"] == split:
                _validate_item(Path(dataset), name, item, formal=formal, labels=require_labels, sources=verify_sources)
        if name in {"routing", "sql"} and reference_contract == "typed_query_plan_v1":
            from eval.reference_contract import query_reference_issues

            issues = query_reference_issues(selected)
            require(not issues, f"invalid typed query references: {issues}")
        if formal and name == "dedup":
            require({r.get("label") for r in selected} == {"SAME", "RELATED", "DIFFERENT"}, "dedup test needs all three classes")
    if "retrieval" in sections and require_labels:
        canonical_mapping(manifest, formal=formal)
        if formal:
            require(len(manifest.get("physical_indices", [])) == 1, "formal retrieval needs one physical index")
    if formal:
        require(manifest.get("status") == "frozen", "gold manifest must be frozen")
        require(manifest.get("kind") != "fixture", "synthetic fixture cannot be a formal test gold")
        snapshot = manifest.get("snapshot", {})
        if any(s in sections for s in ("retrieval", "routing", "sql", "task_labels")):
            require(all(k in snapshot for k in ("corpus_revision", "indexed_revision", "task_annotation_revision", "as_of")), "formal corpus snapshot incomplete")
            require(snapshot["corpus_revision"] == snapshot["indexed_revision"], "formal index not synchronized")
            date.fromisoformat(snapshot["as_of"])
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--section", choices=SECTIONS)
    parser.add_argument("--split", choices=("dev", "test"), default="test")
    parser.add_argument("--review-policy", choices=("human", "delegated_agent"), default="human")
    args = parser.parse_args()
    try:
        manifest = validate_gold(args.dataset, args.section, split=args.split, review_policy=args.review_policy)
    except (ValueError, KeyError, OSError) as error:
        print(f"Gold set incomplete or invalid: {error}")
        return 1
    print(f"Dataset valid: {manifest['version']} ({args.split}; {args.review_policy}; {'formal' if args.split == 'test' else 'NOT_ELIGIBLE'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
