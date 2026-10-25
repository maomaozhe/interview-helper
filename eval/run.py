"""Score stored observations; dev reports remain ineligible for release."""
from __future__ import annotations

import argparse
import inspect
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from eval.common import SECTIONS, digest, inside, provenance, read_json, read_jsonl, write_json
from eval.metrics import efficiency
from eval.report import gates, write_report
from eval.scoring import score_dedup, score_extraction, score_labels, score_query, score_retrieval, slices
from eval.source_gold import qualify_retrieval_metrics, release_eligibility_issues
from eval.validate_gold import require, validate_gold


REPORT_SCHEMA = "evaluation_report_integrity_v1"
SCORING_PROTOCOL = "evaluation_scoring_v2"


def scorer_identity() -> dict:
    """Identity of scoring and validation, independent of the evaluated candidate."""
    root = Path(__file__).resolve().parent
    files = ("scoring.py", "metrics.py", "report.py", "validate_gold.py", "source_gold.py", "reference_contract.py",
             "query_results.py", "answer_quality.py", "answer_reviews.py")
    hashes = {name: digest((root / name).read_text(encoding="utf-8-sig").encode()) for name in files}
    # Include orchestration and the source decoder used by evidence validation,
    # without fingerprinting the whole API, collector, suite or candidate tree.
    from interview_intelligence.ingestion.snapshot import decode_source
    hashes["score_inputs"] = digest(inspect.getsource(score_inputs).encode())
    hashes["decode_source"] = digest(inspect.getsource(decode_source).encode())
    from eval.pilot import active_facts
    hashes["active_facts"] = digest(inspect.getsource(active_facts).encode())
    from eval import common
    for name in ("digest", "read_json", "read_jsonl", "inside", "lookup", "finite_number"):
        hashes[f"common.{name}"] = digest(inspect.getsource(getattr(common, name)).encode())
    hashes["common.constants"] = digest({"SECTIONS": common.SECTIONS, "PIPELINES": common.PIPELINES})
    return {"protocol": SCORING_PROTOCOL, "sha256": digest({"protocol": SCORING_PROTOCOL, "files": hashes}),
            "files": hashes}


def scoring_version() -> str:
    return scorer_identity()["sha256"]


def score_inputs(dataset: Path, section: str, predictions: Path, *,
                 split="test", alignments: Path | None = None, default_pipeline="HYBRID", review_policy="human",
                 threshold_version="quality_gate_v2", answer_reviews: Path | None = None) -> dict:
    """Pure offline derivation shared by report creation and strict verification."""
    manifest = validate_gold(dataset, section, split=split, review_policy=review_policy)
    golds = [item for item in manifest[section] if item["split"] == split]
    rows = read_jsonl(predictions)
    seen = {row.get("id") for row in rows}
    require(len(seen) == len(rows) and seen == {g["id"] for g in golds},
            "predictions must cover each selected ID exactly once")
    by_id = {row["id"]: row for row in rows}
    rows = [by_id[g["id"]] for g in golds]
    require(answer_reviews is None or section == "routing", "ANSWER_REVIEWS_REQUIRE_ROUTING")
    snapshot = manifest.get("snapshot", {})
    for row in rows:
        require(type(row.get("failed", False)) is bool, "failed must be boolean")
        if snapshot:
            actual = row.get("snapshot", {})
            require(all(actual.get(k) == value for k, value in snapshot.items()), "prediction snapshot missing or changed")
    alignment_map = {}
    if alignments:
        entries = read_jsonl(alignments)
        require(len({r["id"] for r in entries}) == len(entries), "duplicate alignment ID")
        alignment_map = {r["id"]: r for r in entries}
    if section == "dedup":
        metrics, scored = score_dedup(golds, rows)
    elif section == "task_labels":
        metrics, scored = score_labels(golds, rows)
    elif section == "retrieval":
        metrics, scored = score_retrieval(golds, rows, manifest)
        qualify_retrieval_metrics(metrics, manifest, scored)
    elif section in {"routing", "sql"}:
        result_reference = None
        if section == "routing" and manifest.get("query_result_reference") is not None:
            from eval.query_results import validate_query_results
            result_reference = validate_query_results(dataset, manifest, verify_sources=False)
        from eval.answer_reviews import load_answer_reviews
        reviews = load_answer_reviews(answer_reviews, manifest, predictions, golds, rows)
        metrics, scored = score_query(golds, rows, sql=section == "sql", result_reference=result_reference,
                                     answer_reviews=reviews)
    else:
        metrics, scored = score_extraction(golds, rows, dataset, alignment_map,
                                          fixture=manifest.get("kind") == "fixture", review_policy=review_policy)
    metrics["efficiency"] = efficiency(rows)
    metrics["slices"] = slices(scored)
    verified = sum(g["human_verified"] for g in golds)
    eligible = split == "test" and verified == len(golds) and manifest.get("kind") != "fixture"
    agent_verified = sum(g.get("agent_verified") is True for g in golds)
    if review_policy == "delegated_agent":
        eligible = split == "test" and agent_verified == len(golds) and manifest.get("kind") != "fixture"
    eligibility_issues = release_eligibility_issues(manifest, section)
    eligible = eligible and not eligibility_issues
    gate = gates(section, metrics, eligible=eligible, default_pipeline=default_pipeline,
                 threshold_version=threshold_version)
    gate["review_policy"] = review_policy
    if eligibility_issues:
        gate["eligibility_issues"] = eligibility_issues
    return {"gold": manifest, "golds": golds, "rows": rows, "metrics": metrics, "scored": scored,
            "gate": gate, "verified": verified, "agent_verified": agent_verified,
            "eligibility_issues": eligibility_issues}


def _archive_gold(dataset: Path, manifest: dict, output: Path, section: str, split: str) -> None:
    """Copy only manifest-declared evidence, never a dataset directory wholesale."""
    references = set()

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"source_path", "source_facts_path"} and item:
                    require(isinstance(item, str), "gold evidence path must be a string")
                    references.add(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    # Source-contract validation checks both splits of the selected section;
    # unrelated sections can remain drafts with unavailable source files.
    visit(manifest[section] if manifest.get("source_evidence_contract") else
          [row for row in manifest[section] if row["split"] == split])
    if section == "retrieval" and manifest.get("source_evidence_contract"):
        visit(manifest.get("relevance_evidence", {}))
        visit({"source_facts_path": manifest.get("source_facts_path")})
    if section == "routing" and manifest.get("query_result_reference"):
        reference = manifest["query_result_reference"]
        visit(reference)
        facts_path = Path(reference["source_facts_path"])
        facts = read_json(inside(dataset, str(facts_path)))
        for document in facts["documents"]:
            references.add((facts_path.parent / document["source_path"]).as_posix())
    output = output.resolve()
    output.mkdir()
    for relative in sorted(references):
        source, target = inside(dataset, relative), inside(output, relative)
        require(target not in {output / "manifest.json", output / "freeze.json"}, "reserved gold evidence path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    write_json(output / "manifest.json", manifest)
    hashes = {p.relative_to(output).as_posix(): digest(p.read_bytes()) for p in sorted(output.rglob("*")) if p.is_file()}
    write_json(output / "freeze.json", {"dataset_sha256": digest(manifest), "file_hashes": hashes,
                                       "kind": "report_input_export"})


def run_section(dataset: Path, section: str, predictions: Path, output_root: Path, *,
                split="test", alignments: Path | None = None, default_pipeline="HYBRID", review_policy="human",
                threshold_version="quality_gate_v2", candidate: dict | None = None,
                protocol: dict | None = None, suite: dict | None = None, answer_reviews: Path | None = None) -> Path:
    for name, value in (("candidate", candidate), ("protocol", protocol), ("suite", suite)):
        require(value is None or isinstance(value, dict), f"{name} must be a mapping")
        if value is not None:
            digest(value)  # Reject non-JSON/non-finite identities before writing.
    dataset, predictions, output_root = Path(dataset), Path(predictions), Path(output_root)
    manifest = validate_gold(dataset, section, split=split, review_policy=review_policy)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    output = output_root / section / "results" / run_id
    output.mkdir(parents=True, exist_ok=False)
    (output / "predictions.jsonl").write_bytes(predictions.read_bytes())
    if alignments:
        (output / "alignments.jsonl").write_bytes(alignments.read_bytes())
    if answer_reviews is not None:
        require(section == "routing", "ANSWER_REVIEWS_REQUIRE_ROUTING")
        (output / "answer_reviews.json").write_bytes(Path(answer_reviews).read_bytes())
    _archive_gold(dataset, manifest, output / "gold", section, split)
    derived = score_inputs(output / "gold", section, output / "predictions.jsonl", split=split,
                           alignments=output / "alignments.jsonl" if alignments else None,
                           default_pipeline=default_pipeline, review_policy=review_policy,
                           threshold_version=threshold_version,
                           answer_reviews=output / "answer_reviews.json" if answer_reviews is not None else None)
    require(digest(derived["gold"]) == digest(manifest), "archived gold changed")
    golds, rows, metrics, scored, gate = (derived[k] for k in ("golds", "rows", "metrics", "scored", "gate"))
    snapshot = manifest.get("snapshot", {})
    verified, agent_verified, eligibility_issues = (derived[k] for k in ("verified", "agent_verified", "eligibility_issues"))
    scorer = scorer_identity()
    scoring = {"section": section, "split": split, "review_policy": review_policy,
               "default_pipeline": default_pipeline, "threshold_version": threshold_version,
               "protocol": protocol or {}, "scorer": scorer}
    config = provenance()
    config.update(default_pipeline=default_pipeline, snapshot=snapshot, models=manifest.get("models", {}),
                  threshold_version=threshold_version,
                  annotation_guide=manifest.get("annotation_guide"), review_policy=review_policy,
                  scoring=scoring, protocol=protocol or {}, candidate=candidate, suite=suite)
    receipt = predictions.with_suffix(".collection.json")
    if receipt.is_file():
        collection = read_json(receipt)
        require(collection.get("predictions_sha256") == digest((output / "predictions.jsonl").read_bytes()), "collection receipt prediction hash mismatch")
        config["collection"] = collection
        (output / "collection.json").write_bytes(receipt.read_bytes())
    config["observed_models"] = sorted({(c.get("operation_type"), c.get("model"), c.get("model_revision"), c.get("prompt_version"))
        for row in rows for c in row.get("model_calls", [])}, key=repr)
    metadata = {"dataset_version": manifest["version"], "kind": manifest.get("kind", "corpus"),
                "section": section, "split": split, "sample_count": len(golds),
                "test_count": len(golds) if split == "test" else 0, "human_verified_count": verified,
                "agent_verified_count": agent_verified, "review_policy": review_policy,
                "dataset_sha256": digest(manifest), "predictions_sha256": digest((output / "predictions.jsonl").read_bytes()),
                "alignments_sha256": digest((output / "alignments.jsonl").read_bytes()) if alignments else None,
                "answer_reviews_sha256": digest((output / "answer_reviews.json").read_bytes()) if answer_reviews is not None else None,
                "snapshot": snapshot, "run_id": run_id, "report_schema": REPORT_SCHEMA,
                "scoring": scoring, "candidate": candidate, "suite": suite}
    if manifest.get("source_evidence_contract"):
        metadata.update(source_evidence_contract=manifest["source_evidence_contract"],
                        release_eligibility_issues=eligibility_issues)
    if manifest.get("retrieval_evidence_scope"):
        metadata["reference_scope"] = manifest["retrieval_evidence_scope"]
    write_report(output, metadata, config, metrics, gate, scored)
    artifacts = {p.relative_to(output).as_posix(): digest(p.read_bytes())
                 for p in sorted(output.rglob("*")) if p.is_file()}
    write_json(output / "integrity.json", {"schema": REPORT_SCHEMA, "artifacts": artifacts,
               "scoring": scoring, "candidate": candidate, "suite": suite,
               "dataset_sha256": digest(manifest), "snapshot": snapshot,
               "note": "Content binding and reproducible scoring; not an authenticity signature or deployment attestation."})
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--section", choices=SECTIONS, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--alignments", type=Path)
    parser.add_argument("--answer-reviews", type=Path, help="Independent prediction-bound final answer assessments")
    parser.add_argument("--split", choices=("dev", "test"), default="test")
    parser.add_argument("--review-policy", choices=("human", "delegated_agent"), default="human")
    parser.add_argument("--default-pipeline", choices=("BM25", "DENSE", "HYBRID", "HYBRID_RERANK"), default="HYBRID")
    parser.add_argument("--threshold-version", choices=("quality_gate_v2", "quality_gate_v3"), default="quality_gate_v2")
    parser.add_argument("--output-root", type=Path, default=Path("eval"))
    args = parser.parse_args()
    try:
        output = run_section(args.dataset, args.section, args.predictions, args.output_root,
                             split=args.split, alignments=args.alignments, default_pipeline=args.default_pipeline,
                             review_policy=args.review_policy, threshold_version=args.threshold_version,
                             answer_reviews=args.answer_reviews)
    except (ValueError, KeyError, OSError, TypeError) as error:
        # An invalid run is a diagnostic receipt, never a partial quality score.
        output = args.output_root / args.section / "results" / ("invalid-" + uuid4().hex)
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "gates.json", {"status": "INVALID", "error": str(error)})
        print(f"INVALID: {error}\n{output}")
        return 1
    print(output)
    from eval.common import read_json
    return 1 if read_json(output / "gates.json")["status"] in {"BELOW_GATE", "INVALID", "NOT_RUN"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
