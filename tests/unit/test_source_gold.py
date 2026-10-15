"""Real-source evidence and incomplete relevance cannot be forged into release gold."""
from copy import deepcopy

import pytest

from eval.common import digest, read_json, write_json, write_jsonl
from eval.release import check_bundle
from eval.run import run_section
from eval.source_gold import release_eligibility_issues, validate_source_contract
from eval.validate_gold import GoldValidationError, validate_gold


def source(root, text, occurrence):
    raw = ("# Test fixture\r\n" + text + "\r\n").encode("utf-8-sig")
    sha = digest(raw)
    path = "sources/" + sha + ".md"
    (root / "sources").mkdir(exist_ok=True)
    (root / path).write_bytes(raw)
    start = len("# Test fixture\n")
    return {"id": occurrence, "occurrence_id": occurrence, "text": text, "raw_question": text,
            "source_hash": sha, "source_path": path,
            "source_spans": [{"start_char": start, "end_char": start + len(text),
                              "start_line": 2, "end_line": 2, "quote": text}],
            "review": {"reviewer": "test agent", "reviewer_kind": "agent",
                       "reviewed_at": "2026-10-14T00:00:00+08:00",
                       "guide_version": "annotation_v2", "basis": "test fixture source review"}}


def pair_manifest(root):
    left = source(root, "Redis 的 RDB 与 AOF 有哪些区别？", "left")
    right = source(root, "Redis 为什么快？", "right")
    return {"source_evidence_contract": "raw_source_spans_v1", "extraction": [], "task_labels": [],
            "dedup": [{"id": "pair", "split": "dev", "left": left, "right": right}], "retrieval": []}


def test_source_evidence_normalizes_bom_crlf_and_checks_both_pair_sides(tmp_path):
    manifest = pair_manifest(tmp_path)
    validate_source_contract(tmp_path, manifest, ["dedup"], split="dev", verify_sources=True)
    (tmp_path / manifest["dedup"][0]["right"]["source_path"]).write_bytes(b"substituted")
    with pytest.raises(GoldValidationError, match="snapshot hash mismatch"):
        validate_source_contract(tmp_path, manifest, ["dedup"], split="dev", verify_sources=True)


@pytest.mark.parametrize("mutation,message", [
    ("quote", "raw question differs"), ("offset", "original span mismatch"),
    ("line", "original line mismatch"), ("path", "escapes its directory"),
    ("missing", "spans missing"),
])
def test_source_contract_rejects_fabricated_or_misattributed_evidence(tmp_path, mutation, message):
    manifest = pair_manifest(tmp_path)
    side = manifest["dedup"][0]["right"]
    if mutation == "quote": side["source_spans"][0]["quote"] = "not in source"
    if mutation == "offset": side["source_spans"][0]["start_char"] += 1
    if mutation == "line": side["source_spans"][0]["start_line"] = 1
    if mutation == "path": side["source_path"] = "../private.md"
    if mutation == "missing": side["source_spans"] = []
    with pytest.raises(ValueError, match=message):
        validate_source_contract(tmp_path, manifest, ["dedup"], split="dev", verify_sources=True)


def test_source_leakage_detected_even_when_pair_group_ids_differ(tmp_path):
    manifest = pair_manifest(tmp_path)
    other = deepcopy(manifest["dedup"][0])
    other.update(id="other", split="test", group_id="different-group")
    manifest["dedup"].append(other)
    with pytest.raises(GoldValidationError, match="source evidence leakage"):
        validate_source_contract(tmp_path, manifest, ["dedup"], split="dev", verify_sources=True)


def retrieval_manifest(root, *, count=50):
    evidence = source(root, "Redis 的 RDB 与 AOF 有哪些区别？", "occurrence")
    review = evidence["review"]
    rows = [{"id": f"query-{i}", "group_id": f"query-family-{i}", "split": "test",
             "human_verified": False, "agent_verified": True, "review": review,
             "query": f"test query {i}", "sample_kind": "positive", "relevance": {"gold": 2},
             "relevance_reviews": {"gold": {"grade": 2, "basis": "reviewed original test fixture"}}}
            for i in range(count)]
    return {"version": "source-test-fixture", "kind": "corpus", "status": "frozen",
            "source_evidence_contract": "raw_source_spans_v1", "retrieval_evidence_scope": "anchor_only",
            "review_authorization": {"policy": "delegated_agent", "thread_id": "test-thread",
                                     "user_instruction": "test-only authorization"},
            "snapshot": {"corpus_revision": 1, "indexed_revision": 1,
                         "task_annotation_revision": 1, "as_of": "2026-10-14"},
            "physical_indices": ["test-index"], "retrieval": rows, "id_namespace": "gold",
            "relevance_evidence": {"gold": {"source_evidence": [evidence],
                                           "canonical_variants_review": [evidence]}},
            "canonical_mapping": {"corpus_revision": 1, "review": review,
                                  "items": [{"gold_question_id": "gold", "canonical_question_id": "canonical",
                                             "equivalence_group": "equivalent", "occurrence_ids": ["occurrence"]}]}}


@pytest.mark.parametrize("mutation,message", [
    ("variant", "variants review incomplete"), ("grade", "grade/basis mismatch"),
    ("source", "source evidence list missing"),
])
def test_retrieval_requires_source_review_of_every_canonical_variant_and_grade(tmp_path, mutation, message):
    manifest = retrieval_manifest(tmp_path)
    if mutation == "variant": manifest["canonical_mapping"]["items"][0]["occurrence_ids"].append("missing")
    if mutation == "grade": manifest["retrieval"][0]["relevance_reviews"]["gold"]["grade"] = 0
    if mutation == "source": manifest["relevance_evidence"]["gold"]["source_evidence"] = []
    write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(GoldValidationError, match=message):
        validate_gold(tmp_path, "retrieval", review_policy="delegated_agent")


def test_a_real_neighbor_quote_cannot_borrow_a_reviewed_occurrence_identity(tmp_path):
    manifest = retrieval_manifest(tmp_path)
    first, second = "Redis 的 RDB 与 AOF 有哪些区别？", "Redis 为什么快？"
    variant = source(tmp_path, first + "\n" + second, "occurrence")
    start = variant["source_spans"][0]["start_char"]
    variant.update(raw_question=first, source_spans=[{"start_char": start, "end_char": start + len(first),
                                                     "quote": first, "start_line": 2, "end_line": 2}])
    neighbor = deepcopy(variant)
    neighbor.update(raw_question=second, source_spans=[{"start_char": start + len(first) + 1,
                                                        "end_char": start + len(first) + 1 + len(second),
                                                        "quote": second, "start_line": 3, "end_line": 3}])
    manifest["relevance_evidence"]["gold"] = {"source_evidence": [neighbor],
                                             "canonical_variants_review": [variant]}
    write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(GoldValidationError, match="no overlap"):
        validate_gold(tmp_path, "retrieval", review_policy="delegated_agent")


def test_anchor_only_cannot_certify_absence_of_all_corpus_positives(tmp_path):
    manifest = retrieval_manifest(tmp_path)
    manifest["retrieval"][0].update(sample_kind="negative", relevance={"gold": 0},
                                   relevance_reviews={"gold": {"grade": 0, "basis": "no positive anchor found"}})
    write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(GoldValidationError, match="cannot certify a negative query"):
        validate_gold(tmp_path, "retrieval", review_policy="delegated_agent")


def test_perfect_anchor_hits_remain_ineligible_and_cannot_become_corpus_recall(tmp_path):
    gold = tmp_path / "gold"
    gold.mkdir()
    manifest = retrieval_manifest(gold)
    # Even an attempted override cannot turn an anchor set into a release test.
    manifest["release_eligible"] = True
    write_json(gold / "manifest.json", manifest)
    predictions = tmp_path / "predictions.jsonl"
    write_jsonl(predictions, [{"id": row["id"], "snapshot": manifest["snapshot"],
                              "ranked": ["canonical"], "failed": False}
                             for row in manifest["retrieval"]])
    output = run_section(gold, "retrieval", predictions, tmp_path / "reports", review_policy="delegated_agent")
    metrics = read_json(output / "metrics.json")
    assert metrics["pipelines"]["HYBRID"]["anchor_recall_at_10"] == 1
    assert "recall_at_10" not in metrics["pipelines"]["HYBRID"]
    assert "precision_at_10" not in metrics["pipelines"]["HYBRID"]
    assert read_json(output / "gates.json")["status"] == "NOT_ELIGIBLE"
    bundle = {"modules": {"retrieval": {"dataset": "gold", "report": output.relative_to(tmp_path).as_posix()}}}
    result = check_bundle(bundle, tmp_path)
    assert result["modules"]["retrieval"]["status"] == "NOT_ELIGIBLE"
    assert result["status"] == "BLOCKED"
    assert release_eligibility_issues(manifest, "retrieval")
