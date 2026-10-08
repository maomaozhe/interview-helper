"""Seal the source-reviewed union of all four observed retrieval Top-10 pools.

This is post-ranking diagnostic adjudication, not unseen test performance.
Every previous relevance judgement is retained unchanged. Unlisted reviewed
queue entries receive an explicit zero; they are never silently unjudged.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eval.common import digest, inside, read_json, read_jsonl, write_json, write_jsonl
from eval.validate_gold import load_dataset, require, validate_gold
from interview_intelligence.ingestion.snapshot import decode_source


QUEUE_SHA256 = "f1ffa6d03787c184a091ddd3c268952a790f0cf9cd64d2729ec9fd9a18102b61"
# Original questions, all variants and ambiguous surrounding source passages
# were read before authoring these additions. Indices address the sealed r293
# canonical catalogue, not ranks or system relevance judgements.
ADDITIONS = {
    "pool-params": ([], [948, 1804, 2206, 2230]),
    "pool-reject": ([], [230, 851, 955, 1154]),
    "pool-queue-concurrency": ([], [218, 1990]),
    "pool-submit": ([], [860, 1165]),
    "pool-tuning": ([2206], [7, 1154, 1932, 1990, 2256, 2344]),
    "pool-caller-runs": ([], [49, 230, 955, 1804, 1990, 2344]),
    "future-executor": ([], [304, 860, 1412, 2207, 2430]),
    "unbounded-queue": ([], [488, 923, 955, 1154, 1679, 2230, 2256]),
    "lru-implementation": ([], [356, 618, 664, 1389, 2019, 2087]),
    "redis-lru": ([], [441, 554, 651, 776, 1389, 1552, 1678, 1923, 2041, 2151]),
    "deadlock-conditions": ([], [1348]),
    "deadlock-prevention": ([], [1272, 1714]),
    "deadlock-code": ([], [840, 1442]),
    "api-idempotence": ([1613], [2, 334, 496, 578, 740, 927, 1189, 1219, 1508]),
    "redis-lock": ([], [145, 740, 1348, 1508, 1646, 2220, 2276]),
    "lock-holder-crash": ([], [776, 816, 839, 1138, 1438, 1646, 2175]),
    "lock-ownership": ([], [816, 1138, 1348, 1374, 2105, 2175]),
    "lock-alternatives": ([], [128, 839, 1296, 1646, 2175]),
    "tcp-syn-rationale": ([], []),
    "tcp-third-data": ([], [568, 1995, 2112, 2361]),
    "tcp-close": ([], [623, 812, 1195, 1369, 1554]),
    "tcp-close-merge": ([1250, 2049], [1195, 2112]),
    "tls-handshake": ([293, 1758, 1925, 2139], [104, 361, 1979, 2318]),
    "bplus-rationale": ([], [2202]),
    "bplus-delete-space": ([], [881, 1256]),
    "bplus-weakness": ([], [684, 755, 827, 879, 881, 1028, 1256, 1408, 1425, 1901, 2169, 2202]),
    "mvcc-versions": ([2356], [157, 388, 969, 1538, 1567]),
    "readview-time": ([], [157, 388, 1538, 1592, 2055]),
    "transaction-isolation": ([1682, 1736, 2178], [388, 2209, 2381]),
    "index-unusable": ([1226], [391, 550, 732, 949, 1007, 1190, 1255, 1352, 1465, 1786, 2386]),
    "force-index": ([], [61, 261, 391, 550, 644, 732, 1352, 1584, 1786]),
    "gc-roots": ([], [109, 952, 1410, 1445, 1801, 1992, 2056, 2147]),
    "g1-pause": ([], [719, 1136]),
    "g1-cms": ([719, 2347], [621, 908, 1103, 1176, 1666, 2056]),
    "cms-phases": ([], [719, 1103, 1445, 2147]),
    "fullgc-investigation": ([], [204, 503, 557, 698, 1410, 1977, 2136, 2153]),
    "gc-reachable": ([109, 961, 2161], [197, 1111, 1585]),
    "volatile-visibility": ([], [363, 877, 1118, 1150, 1244, 1309, 1704]),
    "singleton-safe": ([], [363, 877, 1118, 1150, 1309, 1704, 1983, 2340]),
    "zero-copy": ([], []),
    "consistent-hash": ([], [1204, 1737, 2192]),
    "rate-algorithms": ([150], [8, 744, 913, 1098, 1238, 1830]),
    "rate-dimensions": ([1792], [744, 1238, 1830]),
    "global-rate": ([], [8, 330, 744, 913, 1238, 1792, 1830, 2115, 2275]),
    "circuit-breaker": ([], [70]),
    "kafka-idempotence": ([1649, 1739, 1973], [334, 578, 1110, 1189, 1361, 1685, 1952, 2435]),
    "bloom-principle": ([], [318, 777, 1409, 1681, 1877, 1898]),
    "bloom-size": ([], [318, 376, 509, 1147, 1221, 1747, 2440]),
    "bloom-persistence": ([], [2246]),
    "threadlocal-leak": ([], [205, 574, 1345, 1410, 1992]),
    **{f"heldout-negative-{i}": ([], []) for i in range(5)},
}


def build(snapshot, queue, prior, output):
    require(not output.exists(), "GOLD_VERSION_ALREADY_EXISTS")
    require(digest(queue.read_bytes()) == QUEUE_SHA256, "REVIEWED_QUEUE_CHANGED")
    candidates = read_jsonl(queue)
    require(len(candidates) == 828 and len({r["id"] for r in candidates}) == 828,
            "REVIEWED_POOL_CHANGED")
    previous = validate_gold(prior, "retrieval", review_policy="delegated_agent")
    facts = read_json(snapshot / "facts.json")
    require(digest(facts) == previous["facts_sha256"], "SNAPSHOT_CHANGED")
    catalogue = sorted(facts["canonicals"], key=lambda c: c["id"])
    documents = {d["revision_id"]: d for d in facts["documents"]}
    manifest = copy.deepcopy(read_json(prior / "manifest.json"))
    mapping = manifest["canonical_mapping"]["items"]
    mapped = {m["canonical_question_id"]: m["gold_question_id"] for m in mapping}
    rows = copy.deepcopy(previous["retrieval"])
    by_id = {r["id"]: r for r in rows}
    require(set(ADDITIONS) == {r["query_id"] for r in candidates}, "QUERY_REVIEW_INCOMPLETE")
    reviewed_keys = {(r["query_id"], r["catalog_index"]) for r in candidates}
    require(all((key, i) in reviewed_keys for key, groups in ADDITIONS.items() for group in groups for i in group),
            "DECISION_OUTSIDE_REVIEWED_POOL")
    output.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    review = {"reviewer": "Codex", "reviewer_kind": "agent", "reviewed_at": now,
        "guide_version": "project_quality_agent_v1",
        "basis": "Reviewed all original variants and ambiguous surrounding source text in the four-path Top-10 union."}
    decisions = []
    for candidate in candidates:
        key, i = candidate["query_id"], candidate["catalog_index"]
        canonical_id = candidate["canonical_id"]
        require(catalogue[i]["id"] == canonical_id, "CATALOGUE_ID_CHANGED")
        require(by_id[key]["query"] == candidate["query"], "QUERY_CHANGED")
        direct, related = ADDITIONS[key]
        require(not set(direct) & set(related), "CONFLICTING_RELEVANCE_DECISION")
        grade = 2 if i in direct else 1 if i in related else 0
        evidence = []
        for variant in candidate["source_variants"]:
            for span in variant["source_spans"]:
                document = documents[span["revision_id"]]
                raw = inside(snapshot, document["source_path"]).read_bytes()
                require(digest(raw) == document["source_hash"], "SOURCE_CHANGED")
                text = decode_source(raw)
                require(text[span["start_char"]:span["end_char"]] == span["quote"], "SOURCE_SPAN_CHANGED")
                target = inside(output, document["source_path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    require(digest(target.read_bytes()) == document["source_hash"], "COPIED_SOURCE_CHANGED")
                else:
                    target.write_bytes(raw)
            evidence.append({"occurrence_id": variant["id"], "raw_question": variant["raw_question"],
                "source_spans": variant["source_spans"], "sources": [
                    {"source_hash": documents[s["revision_id"]]["source_hash"],
                     "source_path": documents[s["revision_id"]]["source_path"]} for s in variant["source_spans"]]})
        gold_id = mapped.get(canonical_id)
        if gold_id is None:
            gold_id = "gold-pool-q" + str(i)
            mapped[canonical_id] = gold_id
            mapping.append({"gold_question_id": gold_id, "canonical_question_id": canonical_id,
                "equivalence_group": gold_id, "canonical_text": candidate["canonical_text"],
                "source_evidence": evidence, "review": review})
        require(gold_id not in by_id[key]["relevance"], "EXISTING_JUDGEMENT_CANNOT_BE_OVERWRITTEN")
        by_id[key]["relevance"][gold_id] = grade
        basis = ("Directly asks for the queried object and operation." if grade == 2 else
                 "Explicit broader mechanism or adjacent condition for the queried operation." if grade == 1 else
                 "Different primary object/operation or answer boundary; broad topic overlap alone is insufficient.")
        decisions.append({**candidate, "grade": grade, "gold_question_id": gold_id,
            "human_verified": False, "agent_verified": True,
            "review": {**review, "basis": basis}, "source_evidence": evidence})
    for old, new in zip(previous["retrieval"], rows):
        require(old["id"] == new["id"] and all(new["relevance"][k] == v for k, v in old["relevance"].items()),
                "OLD_RELEVANCE_CHANGED")
        new["pool_review"] = {"queue_sha256": QUEUE_SHA256, "review": review, "selection_depth": 10,
            "lifecycle": "Post-ranking diagnostic; labels added without modifying previous judgements."}
    for section in ("dedup", "routing", "sql"):
        if isinstance(manifest.get(section), str):
            (output / manifest[section]).write_bytes(inside(prior, manifest[section]).read_bytes())
    manifest.update(version=output.name, retrieval="retrieval.jsonl", parent_dataset=prior.name,
        parent_dataset_sha256=digest(previous), reviewed_pool_sha256=QUEUE_SHA256,
        review_recipe_sha256=digest(Path(__file__).read_bytes()), frozen_at=now,
        pool_expansion={"pairs": len(decisions), "queries": len(ADDITIONS), "selection_depth": 10,
            "grades": dict(Counter(r["grade"] for r in decisions)), "review": review,
            "selection": read_json(queue.with_suffix(".selection.json")),
            "limitations": "Post-ranking adjudication on the same snapshot; pooled Top-10 coverage is not corpus-wide completeness or unseen performance. Independent semantic equivalence outside current canonical groups remains limited."})
    manifest["canonical_mapping"]["review"] = review
    write_jsonl(output / "retrieval.jsonl", rows)
    write_jsonl(output / "pool_reviews.jsonl", decisions)
    write_json(output / "manifest.json", manifest)
    write_json(output / "freeze.json", {"dataset_sha256": digest(load_dataset(output)),
        "file_hashes": {p.relative_to(output).as_posix(): digest(p.read_bytes())
                       for p in sorted(output.rglob("*")) if p.is_file() and p.name != "freeze.json"}})
    validate_gold(output, "retrieval", review_policy="delegated_agent")
    print({"dataset": str(output), "added_judgements": len(decisions),
           "grades": manifest["pool_expansion"]["grades"], "source_files": len(list((output / "sources").glob("*.md")))})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("snapshot", "queue", "prior", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    build(args.snapshot, args.queue, args.prior, args.output)
