"""Build and check a small, source-bound service pilot without model calls.

Facts and independent semantic judgments are frozen before collection. This is
seen-source development evidence, never an unseen test or release certificate.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eval.common import SECTIONS, digest, inside, read_json, read_jsonl, write_json
from eval.scoring import check, score_query
from eval.validate_gold import require, validate_gold


ANSWER_RUBRIC = {
    "count_rows": "COUNT(*) counts all three rows, including the row with NULL: 3.",
    "count_values": "COUNT(x) ignores NULL and counts both repeated 1 values: 2.",
    "count_distinct": "COUNT(DISTINCT x) ignores NULL and collapses duplicate 1 values: 1.",
    "no_false_sources": "The explanation does not claim these facts were proved by interview corpus sources.",
}
NEGATIVE_COMPANY = "星际菠萝研究院"
SEARCH_MESSAGE = "找 Redis 缓存穿透、击穿、雪崩的处理场景相关面试题，先给我5道。"


def active_facts(facts):
    """Apply occurrence eligibility directly to the exported relational facts."""
    documents = {d["build_id"]: d for d in facts["documents"]}
    require(len(documents) == len(facts["documents"]), "PILOT_DUPLICATE_BUILD")
    require(len({d["id"] for d in documents.values()}) == len(documents), "PILOT_MULTIPLE_ACTIVE_BUILDS")
    interviews = {i["id"]: i for i in facts["interviews"]}
    require(len(interviews) == len(facts["interviews"]), "PILOT_DUPLICATE_INTERVIEW")
    canonical_ids = {c["id"] for c in facts["canonicals"]}
    require(len(canonical_ids) == len(facts["canonicals"]), "PILOT_DUPLICATE_CANONICAL")
    require(len({q["id"] for q in facts["questions"]}) == len(facts["questions"]), "PILOT_DUPLICATE_OCCURRENCE")
    rows = []
    for question in facts["questions"]:
        interview = interviews[question["interview_id"]]
        document = documents[interview["build_id"]]
        require(question["canonical_question_id"] in canonical_ids, "PILOT_UNKNOWN_CANONICAL")
        if document["decision"] == "INCLUDED" and interview["analytics_eligible"] is True:
            rows.append({**question, "company": interview["company_normalized"],
                         "round": interview["round"] or "UNKNOWN", "document_id": document["id"],
                         "revision_id": document["revision_id"], "source_hash": document["source_hash"]})
    return rows


def company_oracle(rows, company):
    selected = [q for q in rows if q["company"] == company]
    frequencies = Counter(q["canonical_question_id"] for q in selected)
    ordered = sorted(frequencies, key=lambda key: (-frequencies[key], key))
    rounds = Counter(q["round"] for q in selected)
    round_order = sorted(rounds, key=lambda key: (-rounds[key], key))
    return {"ids": ordered, "frequencies": [frequencies[key] for key in ordered],
            "counts": {"canonical_questions": len(frequencies), "occurrences": len(selected),
                       "interviews": len({q["interview_id"] for q in selected}),
                       "source_documents": len({q["document_id"] for q in selected})},
            "round_keys": round_order, "round_counts": [rounds[key] for key in round_order]}


def _rule(name, path, value, op="eq"):
    return {"id": name, "path": path, "op": op, "value": value}


def _turn(message, action, *, company=None, assertions=(), **plan):
    expected = {"action": action, **plan}
    if company is not None:
        expected["filters.company"] = company
    return {"message": message, "expected_plan": expected, "forbidden_tools": ["record_review"],
            "allow_write": False, "assertions": [
                _rule("terminal", "run_status", "SUCCEEDED"),
                _rule("no_review_writes", "result.write_event_delta", 0), *assertions]}


def pilot_cases(oracles):
    byte, tencent, absent = (oracles[key] for key in ("字节", "腾讯", NEGATIVE_COMPANY))

    def listing(message, *, offset=0, action="LIST"):
        ids, frequencies = byte["ids"][offset:offset + 5], byte["frequencies"][offset:offset + 5]
        return _turn(message, action, company="字节", sort="frequency", page_size=5, assertions=[
            _rule("canonical_order", "result.rows.*.canonical_question_id", ids),
            _rule("occurrence_frequencies", "result.rows.*.occurrence_count", frequencies),
            _rule("persisted_page", "result.turn_state.current_page_ids", ids)])

    def count(message, company, oracle):
        return _turn(message, "COUNT", company=company, top_n=None, assertions=[
            *[_rule(key, "result.meta.counts." + key, value) for key, value in oracle["counts"].items()],
            _rule("persisted_count_scope", "result.turn_state.last_count.filters.company", company)])

    definitions = [
        ("p01-list", [listing("列出字节的面试题，按出现次数从高到低排序，先给我5道。")]),
        ("p02-negative", [count(f"当前题库中公司为“{NEGATIVE_COMPANY}”的题目有多少道、出现过多少次？请精确匹配公司，不要替换成其他公司。", NEGATIVE_COMPANY, absent)]),
        ("p03-search-reference", [
            _turn(SEARCH_MESSAGE, "SEARCH", page_size=5, assertions=[
                {"id": "returned_questions", "path": "result.rows", "op": "not_empty"},
                _rule("verified_relevance", "result.meta.relevance_status", "VERIFIED")]),
            _turn("刚才第1题的原始出处是什么？请展示原始提问和来源。", "DETAILS", assertions=[
                _rule("one_reference", "result.rows", 1, "length")])]),
        ("p04-stats", [_turn("按面试轮次统计字节题目的出现次数，列出所有轮次。", "STATS", company="字节", group_by="round", assertions=[
            _rule("round_order", "result.rows.*.key", byte["round_keys"]),
            _rule("round_occurrences", "result.rows.*.occurrence_count", byte["round_counts"])])]),
        ("p05-answer", [_turn("不用查题库，请直接解释 SQL COUNT(*)、COUNT(x)、COUNT(DISTINCT x) 的区别。假设 x 三行依次是 1、1、NULL，各自结果是多少？不要编造题库来源。", "ANSWER", answer_basis="GENERAL_KNOWLEDGE", assertions=[
            _rule("no_corpus_references", "result.meta.evidence_question_ids", []),
            _rule("no_corpus_rows", "result.rows", [], "eq")])]),
        ("p06-pagination", [listing("列出字节的面试题，按出现次数从高到低排序，每页5道。"),
                             listing("下一页。", offset=5, action="NEXT")]),
        ("p07-clarification", [_turn("给我看看那类题。", "CLARIFY", assertions=[
            _rule("no_guessed_results", "result.rows", []),
            {"id": "asks_clarification", "path": "result.plan.clarification", "op": "not_empty"}]),
            listing("我指的是字节的面试题，按出现次数从高到低给我5道。")]),
        ("p08-correction", [count("统计字节题库的去重题目总数和出现总次数。", "字节", byte),
                             count("公司改成腾讯，其余保持不变。", "腾讯", tencent)]),
    ]
    return [{"id": key, "group_id": "service-pilot-" + key, "split": "dev", "human_verified": False,
             "agent_verified": True, "tags": ["service_pilot", "seen_source", "not_release_eligible"], "turns": turns}
            for key, turns in definitions]


def build_pilot(facts_path, semantic_review_path, output, *, authorization):
    """Create new private gold; refuse stale reviews and mutable source evidence."""
    facts_path, output = Path(facts_path), Path(output)
    facts, semantic = read_json(facts_path), read_json(semantic_review_path)
    require(not output.exists(), "PILOT_OUTPUT_EXISTS")
    require(authorization.get("thread_id") and authorization.get("user_instruction"), "PILOT_AUTHORIZATION_MISSING")
    require(semantic.get("facts_sha256") == digest(facts), "PILOT_REVIEW_FACTS_MISMATCH")
    require(semantic.get("query") == SEARCH_MESSAGE and semantic.get("reviewer") and semantic.get("reviewed_at"),
            "PILOT_SEMANTIC_REVIEW_MISSING")
    rows = active_facts(facts)
    by_id = {q["id"]: q for q in rows}
    labels = semantic.get("labels", [])
    require(len({label["occurrence_id"] for label in labels}) == len(labels), "PILOT_DUPLICATE_REVIEW")
    require(all(label["occurrence_id"] in by_id and label.get("role") in {"POSITIVE", "NEGATIVE", "BOUNDARY"}
                and label.get("reason") for label in labels), "PILOT_REVIEW_INVALID")
    require(sum(label["role"] == "POSITIVE" for label in labels) >= 3
            and any(label["role"] == "NEGATIVE" for label in labels), "PILOT_SEARCH_ANCHORS_INCOMPLETE")
    for document in facts["documents"]:
        source = inside(facts_path.parent, document["source_path"])
        require(digest(source.read_bytes()) == document["source_hash"], "PILOT_SOURCE_HASH_MISMATCH")
    documents = {d["revision_id"]: d for d in facts["documents"]}
    from interview_intelligence.ingestion.snapshot import decode_source
    source_texts = {}
    for row in rows:
        revision = row["revision_id"]
        if revision not in source_texts:
            source_texts[revision] = decode_source(inside(facts_path.parent, documents[revision]["source_path"]).read_bytes())
        text = source_texts[revision]
        require(bool(row["source_spans"]), "PILOT_SOURCE_SPAN_MISSING")
        for span in row["source_spans"]:
            start, end = span.get("start_char"), span.get("end_char")
            require(type(start) is int and type(end) is int and 0 <= start < end <= len(text)
                    and text[start:end] == span.get("quote"), "PILOT_SOURCE_SPAN_MISMATCH")
    oracles = {company: company_oracle(rows, company) for company in ("字节", "腾讯", NEGATIVE_COMPANY)}
    require(len(oracles["字节"]["ids"]) >= 10 and oracles["腾讯"]["ids"], "PILOT_COMPANY_COVERAGE_INCOMPLETE")
    require(oracles[NEGATIVE_COMPANY]["counts"]["occurrences"] == 0, "PILOT_NEGATIVE_COMPANY_EXISTS")
    cases = pilot_cases(oracles)
    now = datetime.now(timezone.utc).isoformat()
    review = {"reviewer_kind": "agent", "reviewer": semantic["reviewer"], "reviewed_at": now,
              "guide_version": "service_pilot_v1", "basis": "Independent relational oracle and precollection source review"}
    for case in cases:
        case["review"] = review
    oracle = {"schema": "service_pilot_oracle_v1", "facts_sha256": digest(facts), "snapshot": facts["snapshot"],
              "occurrences": rows, "company_oracles": oracles, "semantic_review": semantic,
              "answer_rubric": ANSWER_RUBRIC, "review_policy": "delegated_agent", "human_verified": False,
              "exposure": "seen_source_development", "release_eligible": False}
    manifest = {"version": output.name, "status": "frozen", "kind": "corpus", "snapshot": facts["snapshot"],
                "physical_indices": facts.get("physical_indices", []), "id_namespace": "canonical",
                "review_authorization": {"policy": "delegated_agent", **authorization},
                "exposure": "seen_source_development", "release_eligible": False,
                **{section: cases if section == "routing" else [] for section in SECTIONS}}
    output.mkdir(parents=True)
    write_json(output / "pilot-oracle.json", oracle)
    write_json(output / "manifest.json", manifest)
    write_json(output / "freeze.json", {"dataset_sha256": digest(manifest), "created_at": now,
        "file_hashes": {"pilot-oracle.json": digest((output / "pilot-oracle.json").read_bytes())}})
    validate_gold(output, "routing", split="dev", review_policy="delegated_agent")
    return {"dataset": str(output.resolve()), "cases": len(cases), "turns": sum(len(c["turns"]) for c in cases),
            "first_stage_cases": 3, "first_stage_turns": 4, "facts_sha256": digest(facts),
            "gold_sha256": digest(manifest), "oracle_sha256": digest(oracle)}


def _sources(row):
    sources = row.get("sources", [])
    if not sources and row.get("source_summary"):
        summary = row["source_summary"]
        sources = [{"revision_id": summary.get("revision_id"), **span} for span in summary.get("spans", [])]
    return sources


def _witness(source, question):
    if source.get("occurrence_id") and source["occurrence_id"] != question["id"]:
        return False
    if source.get("revision_id", source.get("source_revision_id")) != question["revision_id"]:
        return False
    spans = source.get("source_spans", source.get("spans", [source]))
    return bool(spans) and all(any(span.get("quote") == gold["quote"] and
        (all(span.get(k) == gold.get(k) for k in ("start_char", "end_char"))
         if "start_char" in span else source.get("occurrence_id") == question["id"])
        for gold in question["source_spans"]) for span in spans)


def assess_pilot(dataset, predictions, *, answer_review=None, limit=None):
    """Recheck user results; missing independent review stays UNKNOWN."""
    require(limit in {None, 3, 8}, "PILOT_STAGE_MUST_BE_THREE_OR_EIGHT_CASES")
    dataset = Path(dataset)
    manifest = validate_gold(dataset, "routing", split="dev", review_policy="delegated_agent")
    oracle = read_json(dataset / "pilot-oracle.json")
    cases = manifest["routing"][:limit]
    predictions = read_jsonl(predictions) if isinstance(predictions, (str, Path)) else predictions
    require([p.get("id") for p in predictions] == [c["id"] for c in cases], "PILOT_PREDICTION_IDS_MISMATCH")
    require(all(p.get("snapshot") == oracle["snapshot"] for p in predictions), "PILOT_PREDICTION_SNAPSHOT_MISMATCH")
    _, results = score_query(cases, predictions)
    labels = {item["occurrence_id"]: item["role"] for item in oracle["semantic_review"]["labels"]}
    questions = oracle["occurrences"]
    if answer_review is not None:
        require(answer_review.get("predictions_sha256") == digest(predictions)
                and answer_review.get("rubric_sha256") == digest(oracle["answer_rubric"]), "PILOT_ANSWER_REVIEW_IDENTITY_MISMATCH")
        require(answer_review.get("reviewer_kind") == "agent" and answer_review.get("reviewer")
                and answer_review.get("reviewed_at"), "PILOT_ANSWER_REVIEW_PROVENANCE_MISSING")
    for case, prediction, result in zip(cases, predictions, results):
        observed = prediction.get("turns", [])
        for index, (expected, actual) in enumerate(zip(case["turns"], observed)):
            action = expected["expected_plan"]["action"]
            rows = actual.get("result", {}).get("rows", [])
            checks = result["checks"]
            prefix = f"turn{index}."
            if action in {"LIST", "NEXT", "SEARCH", "DETAILS"}:
                company = expected["expected_plan"].get("filters.company")
                possible = [q for q in questions if company is None or q["company"] == company]
                for number, row in enumerate(rows):
                    canonical = row.get("canonical_question_id", row.get("key"))
                    matches = [q for q in possible if q["canonical_question_id"] == canonical]
                    sources = _sources(row)
                    witnesses = [[q for q in matches if _witness(source, q)] for source in sources]
                    checks.append(check(prefix + f"source_{number}", bool(sources) and all(witnesses), dimension="risk"))
                    if action == "SEARCH":
                        roles = [labels.get(q["id"]) for group in witnesses for q in group]
                        status = False if "NEGATIVE" in roles else None if not roles or any(r != "POSITIVE" for r in roles) else True
                        checks.append(check(prefix + f"independent_relevance_{number}", status))
                if action == "SEARCH":
                    returned_ids = [row.get("canonical_question_id") for row in rows]
                    checks.append(check(prefix + "unique_search_questions", len(returned_ids) == len(set(returned_ids))))
                    checks.append(check(prefix + "requested_page_bound", len(rows) <= expected["expected_plan"]["page_size"]))
                    positives = {q["canonical_question_id"] for q in questions if labels.get(q["id"]) == "POSITIVE"}
                    checks.append(check(prefix + "strong_positive_coverage", len({r.get("canonical_question_id") for r in rows} & positives) >= 2))
                if action == "DETAILS" and index:
                    previous = observed[index - 1].get("result", {}).get("rows", [])
                    expected_id = previous[0].get("canonical_question_id") if previous else None
                    checks.append(check(prefix + "first_question_reference", bool(expected_id) and
                        [r.get("canonical_question_id") for r in rows] == [expected_id]))
                    checks.append(check(prefix + "page_reference_preserved", actual.get("result", {}).get("turn_state", {}).get("current_page_ids") ==
                        observed[index - 1].get("result", {}).get("turn_state", {}).get("current_page_ids")))
            if action == "ANSWER":
                answer = actual.get("result", {}).get("plan", {}).get("answer_text", "")
                assessments = (answer_review or {}).get("assessments", {})
                for key in ANSWER_RUBRIC:
                    reviewed = assessments.get(key)
                    if reviewed is None:
                        passed = None
                    else:
                        require(type(reviewed.get("passed")) is bool and reviewed.get("evidence_quote")
                                and reviewed["evidence_quote"] in answer and reviewed.get("reason"), "PILOT_ANSWER_REVIEW_EVIDENCE_INVALID")
                        passed = reviewed["passed"]
                    checks.append(check(prefix + key, passed, reason="Independent answer review required" if passed is None else None))
            if index:
                prior_meta = observed[index - 1].get("result", {}).get("meta", {})
                meta = actual.get("result", {}).get("meta", {})
                checks.append(check(prefix + "same_conversation", bool(meta.get("conversation_id")) and meta.get("conversation_id") == prior_meta.get("conversation_id")))
                checks.append(check(prefix + "version_advanced", type(meta.get("conversation_version")) is int and
                    type(prior_meta.get("conversation_version")) is int and meta["conversation_version"] == prior_meta["conversation_version"] + 1))
        result["status"] = "FAIL" if prediction.get("failed") or any(c["status"] == "FAIL" for c in result["checks"]) else "UNKNOWN" if any(c["status"] == "UNKNOWN" for c in result["checks"]) else "PASS"
        result.pop("observation", None)
    return {"schema": "service_pilot_assessment_v1", "gold_sha256": digest(manifest), "predictions_sha256": digest(predictions),
            "cases": results, "counts": dict(Counter(r["status"] for r in results)), "complete": len(cases) == 8,
            "release_eligible": False, "review_policy": "delegated_agent", "human_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    for flag in ("facts", "semantic-review", "authorization", "output"):
        build.add_argument("--" + flag, type=Path, required=True)
    assess = commands.add_parser("assess")
    for flag in ("dataset", "predictions", "output"):
        assess.add_argument("--" + flag, type=Path, required=True)
    assess.add_argument("--answer-review", type=Path)
    assess.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.command == "build":
        print(build_pilot(args.facts, args.semantic_review, args.output, authorization=read_json(args.authorization)))
    else:
        write_json(args.output, assess_pilot(args.dataset, args.predictions, limit=args.limit,
            answer_review=read_json(args.answer_review) if args.answer_review else None))


if __name__ == "__main__":
    main()
