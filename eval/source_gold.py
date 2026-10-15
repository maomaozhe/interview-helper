"""Optional, stricter provenance for labels reviewed from original interviews.

Historical gold remains replayable. New source-reviewed datasets opt into this
contract; an anchor-only retrieval set can diagnose hits but cannot certify
corpus recall or pass a release gate.
"""
from __future__ import annotations

import re
from pathlib import Path

from eval.common import digest, inside, read_json
from interview_intelligence.ingestion.snapshot import decode_source

SOURCE_CONTRACT = "raw_source_spans_v1"
RETRIEVAL_SCOPES = {"anchor_only", "judged_pool", "corpus_exhaustive"}


def release_eligibility_issues(manifest: dict, section: str) -> list[str]:
    issues = []
    if manifest.get("release_eligible") is False:
        issues.append("dataset explicitly excludes release certification")
    if section == "retrieval" and manifest.get("retrieval_evidence_scope") == "anchor_only":
        issues.append("only source-reviewed anchors; corpus relevance is not exhaustive")
    return issues


def qualify_retrieval_metrics(metrics: dict, manifest: dict, rows: list[dict] | None = None) -> None:
    """Do not present anchor coverage as corpus recall or retrieval precision."""
    scope = manifest.get("retrieval_evidence_scope")
    if scope is None:
        return
    metrics["reference_scope"] = scope
    if scope != "anchor_only":
        return
    names = {"recall_at_5": "anchor_recall_at_5", "recall_at_10": "anchor_recall_at_10",
             "recall_at_10_upper_bound": "anchor_recall_at_10_upper_bound",
             "mrr": "anchor_mrr", "ndcg_at_10": "anchor_ndcg_at_10",
             "precision_at_10": "known_positive_fraction_at_10"}
    values_to_qualify = list(metrics.get("pipelines", {}).values())
    values_to_qualify.extend(row["scores"] for row in rows or [])
    for values in values_to_qualify:
        for old, new in names.items():
            if old in values:
                values[new] = values.pop(old)
    for row in rows or []:
        for check in row["checks"]:
            if check["id"] == "has_relevant_hit":
                check["id"] = "has_source_reviewed_anchor_hit"
    metrics["reference_scope_note"] = (
        "Anchor metrics describe only reviewed original-source positives. "
        "Unjudged returns are not negative labels; corpus recall and precision are not measured."
    )


def validate_source_contract(root: Path, manifest: dict, sections: list[str], *,
                             split: str, verify_sources: bool) -> None:
    from eval.validate_gold import require, reviewed

    contract = manifest.get("source_evidence_contract")
    require(contract in {None, SOURCE_CONTRACT}, "unknown source evidence contract")
    if "release_eligible" in manifest:
        require(type(manifest["release_eligible"]) is bool, "release_eligible must be boolean")
    scope = manifest.get("retrieval_evidence_scope")
    require(scope is None or scope in RETRIEVAL_SCOPES, "unknown retrieval evidence scope")
    if contract is None:
        return

    cache: dict[str, str] = {}
    source_splits: dict[str, str] = {}

    def evidence(value: dict, item_split: str, *, with_review=False) -> None:
        require(isinstance(value, dict), "source evidence must be an object")
        sha, path = value.get("source_hash"), value.get("source_path")
        require(isinstance(sha, str) and bool(re.fullmatch(r"[0-9a-f]{64}", sha)),
                "source evidence SHA-256 missing/invalid")
        require(isinstance(path, str) and bool(path), "source evidence path missing")
        file = inside(root, path)
        require(source_splits.setdefault(sha, item_split) == item_split,
                f"dev/test source evidence leakage: {sha}")
        spans = value.get("source_spans")
        require(isinstance(spans, list) and bool(spans), "source evidence spans missing")
        for span in spans:
            require(isinstance(span, dict), "source evidence span must be an object")
            start, end, quote = span.get("start_char"), span.get("end_char"), span.get("quote")
            require(type(start) is int and type(end) is int and 0 <= start < end
                    and isinstance(quote, str) and bool(quote), "source evidence span invalid")
        require(value.get("raw_question") == "\n".join(s["quote"] for s in spans),
                "source evidence raw question differs from quoted spans")
        if with_review:
            reviewed(value.get("review"))
            require(bool(value["review"].get("basis")), "source evidence review basis missing")
        if not verify_sources:
            return
        if sha not in cache:
            require(file.is_file() and digest(file.read_bytes()) == sha,
                    "source evidence snapshot hash mismatch")
            cache[sha] = decode_source(file.read_bytes())
        else:
            # The same hash cannot conceal a missing or substituted second path.
            require(file.is_file() and digest(file.read_bytes()) == sha,
                    "source evidence snapshot hash mismatch")
        text = cache[sha]
        for span in spans:
            start, end = span["start_char"], span["end_char"]
            require(end <= len(text) and text[start:end] == span["quote"],
                    "source evidence original span mismatch")
            for field, offset in (("start_line", start), ("end_line", end - 1)):
                if field in span:
                    require(type(span[field]) is int and span[field] == text.count("\n", 0, offset) + 1,
                            "source evidence original line mismatch")

    # Check source grouping across all splits, including both sides of a pair.
    for section in ("extraction", "task_labels", "dedup"):
        if section not in sections:
            continue
        for item in manifest[section]:
            if section == "dedup":
                for side in ("left", "right"):
                    evidence(item.get(side), item["split"])
                    require(item[side].get("text") == item[side].get("raw_question"),
                            "dedup input text differs from reviewed raw source question")
            elif section == "task_labels":
                evidence(item, item["split"])
            else:
                for question in item.get("questions", []):
                    evidence({**question, "source_hash": item.get("source_hash"),
                              "source_path": item.get("source_path")}, item["split"])
                    corroborating = question.get("corroborating_spans", [])
                    if corroborating:
                        evidence({"source_hash": item.get("source_hash"), "source_path": item.get("source_path"),
                                  "raw_question": "\n".join(s.get("quote", "") for s in corroborating),
                                  "source_spans": corroborating}, item["split"])

    if "retrieval" not in sections:
        return
    require(scope is not None, "source-reviewed retrieval scope missing")
    references = manifest.get("relevance_evidence")
    require(isinstance(references, dict) and bool(references), "retrieval original-source evidence missing")
    mapping = {r["gold_question_id"]: r for r in manifest.get("canonical_mapping", {}).get("items", [])}
    facts = None
    if manifest.get("source_facts_path"):
        facts = read_json(inside(root, manifest["source_facts_path"]))
        require(digest(facts) == manifest.get("facts_sha256"), "retrieval source facts hash mismatch")
        require(facts.get("snapshot") == manifest.get("snapshot"), "retrieval source facts snapshot mismatch")
        fact_questions = {q["id"]: q for q in facts["questions"]}
        documents = {d["revision_id"]: d for d in facts["documents"]}

    def span_identity(spans):
        return [(s["start_char"], s["end_char"], s["quote"]) for s in spans]

    for item in manifest["retrieval"]:
        if scope == "anchor_only":
            require(any(grade > 0 for grade in item["relevance"].values()),
                    "anchor-only references cannot certify a negative query")
        reviews = item.get("relevance_reviews")
        require(isinstance(reviews, dict) and set(reviews) == set(item["relevance"]),
                "retrieval query relevance review coverage mismatch")
        for gold_id, grade in item["relevance"].items():
            review = reviews[gold_id]
            require(isinstance(review, dict) and type(review.get("grade")) is int
                    and review["grade"] == grade and bool(review.get("basis")),
                    "retrieval query relevance review grade/basis mismatch")
            reference = references.get(gold_id)
            require(isinstance(reference, dict), "retrieval gold ID has no original-source evidence")
            originals = reference.get("source_evidence")
            variants = reference.get("canonical_variants_review")
            require(isinstance(originals, list) and bool(originals), "retrieval source evidence list missing")
            require(isinstance(variants, list) and bool(variants), "retrieval canonical variants review missing")
            for original in originals + variants:
                evidence(original, item["split"], with_review=True)
            declared = mapping.get(gold_id, {}).get("occurrence_ids")
            require(isinstance(declared, list) and bool(declared) and len(declared) == len(set(declared)),
                    "retrieval canonical occurrence identities missing/duplicate")
            actual = [v.get("occurrence_id") for v in variants]
            require(all(isinstance(v, str) and v for v in actual) and len(actual) == len(set(actual))
                    and set(actual) == set(declared), "retrieval canonical variants review incomplete")
            require({v.get("occurrence_id") for v in originals} <= set(declared),
                    "retrieval anchor source does not belong to reviewed canonical")
            by_occurrence = {v["occurrence_id"]: v for v in variants}
            for original in originals:
                variant = by_occurrence[original["occurrence_id"]]
                require(all(original[k] == variant[k] for k in ("source_hash", "source_path")),
                        "retrieval anchor source differs from reviewed occurrence")
                require(any(max(a["start_char"], b["start_char"]) < min(a["end_char"], b["end_char"])
                            for a in original["source_spans"] for b in variant["source_spans"]),
                        "retrieval anchor has no overlap with reviewed occurrence")
            if facts is not None:
                canonical = mapping[gold_id]["canonical_question_id"]
                expected = {q["id"] for q in facts["questions"] if q["canonical_question_id"] == canonical}
                require(set(declared) == expected, "retrieval variants differ from complete source facts occurrence set")
                for variant in variants:
                    question = fact_questions[variant["occurrence_id"]]
                    spans = variant.get("occurrence_source_spans", variant["source_spans"])
                    require(span_identity(spans) == span_identity(question["source_spans"]),
                            "retrieval occurrence source spans differ from frozen facts")
                    require(any(max(a["start_char"], b["start_char"]) < min(a["end_char"], b["end_char"])
                                for a in variant["source_spans"] for b in spans),
                            "retrieval variant review has no overlap with frozen occurrence source spans")
                    require(all(documents[s["revision_id"]]["source_hash"] == variant["source_hash"]
                                for s in question["source_spans"]),
                            "retrieval occurrence source revision differs from frozen facts")
