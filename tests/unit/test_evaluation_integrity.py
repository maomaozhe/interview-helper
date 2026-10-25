"""Offline evidence recomputation, candidate bindings and paired comparisons."""
from pathlib import Path

import pytest

from eval.common import digest, read_json, read_jsonl, write_json, write_jsonl
from eval.compare import compare
from eval.release import _candidate_verification, check_bundle, verify_report
from eval.report import gates
from eval.run import run_section, scorer_identity
from eval.validate_gold import load_dataset


CANDIDATE = {"id": "candidate-a", "components": {"sql": {"version": "sql-a"},
                                                    "routing": {"version": "query-a", "model": "other-model"}}}


@pytest.fixture
def make_report(tmp_path, monkeypatch):
    monkeypatch.setattr("eval.run.provenance", lambda: {"git_commit": "candidate-source", "file_hashes": {}})
    dataset = tmp_path / "gold"
    dataset.mkdir()
    manifest = {"version": "offline-proof", "kind": "fixture", "status": "draft", "sql": [{
        "id": "sql", "group_id": "sql", "split": "dev", "human_verified": False, "request": {},
        "assertions": [{"id": "rows", "path": "result.rows", "op": "eq", "value": [1]},
                       {"id": "count", "path": "result.total", "op": "eq", "value": 1}]}]}
    write_json(dataset / "manifest.json", manifest)
    predictions = tmp_path / "predictions.jsonl"

    def make(*, rows=None, candidate=CANDIDATE, threshold="quality_gate_v2", protocol=None, formal=False, collection=None):
        if formal:
            review = {"reviewer": "reviewer", "reviewed_at": "2026-10-15T00:00:00+00:00", "guide_version": "v1"}
            manifest.update(kind="corpus", status="frozen", snapshot={"corpus_revision": 1, "indexed_revision": 1,
                "task_annotation_revision": 1, "as_of": "2026-10-15"})
            template = manifest["sql"][0]
            manifest["sql"] = [{**template, "id": f"sql-{i}", "group_id": f"sql-{i}", "split": "test",
                                "human_verified": True, "review": review} for i in range(20)]
            write_json(dataset / "manifest.json", manifest)
        if rows is None:
            rows = [{"id": gold["id"], "snapshot": manifest.get("snapshot", {}),
                     "result": {"rows": [1], "total": 1}, "model_calls": [], "elapsed_ms": 10}
                    for gold in manifest["sql"]]
        write_jsonl(predictions, rows)
        if collection is not None:
            gold = load_dataset(dataset)
            source_hashes = {"src/candidate.py": "declared-source-file-hash"}
            write_json(predictions.with_suffix(".collection.json"), {"schema": "collection_v2", "adapter": "sql",
                "section": "sql", "split": "test" if formal else "dev", "review_policy": "human",
                "candidate_binding": candidate, "candidate_sha256": digest(candidate),
                "predictions_sha256": digest(predictions.read_bytes()), "dataset_sha256": digest(gold),
                "selection_sha256": digest(gold["sql"]), "snapshot": gold.get("snapshot", {}),
                "samples": len(rows), "failed": sum(bool(row.get("failed")) for row in rows),
                "runtime_verification": "LOCAL_SOURCE_BOUND", "provenance": {
                    "source_sha256": digest(source_hashes), "file_hashes": source_hashes}, **collection})
        return run_section(dataset, "sql", predictions, tmp_path / "reports", split="test" if formal else "dev",
                           candidate=candidate, threshold_version=threshold,
                           protocol=protocol or {"module_id": "sql", "observation_scope": "stored_predictions"},
                           suite={"id": "suite-a", "sha256": "suite-content"})

    return make


def reseal_artifacts(report):
    """Simulate internally consistent but incorrect copied report outputs."""
    seal = read_json(report / "integrity.json")
    seal["artifacts"] = {name: digest((report / name).read_bytes()) for name in seal["artifacts"]}
    write_json(report / "integrity.json", seal)


def test_sealed_fixture_recomputes_without_original_gold(make_report, tmp_path):
    report = make_report()
    (tmp_path / "gold/manifest.json").unlink()
    result = verify_report(report, expected_candidate=CANDIDATE)
    assert result["status"] == "VERIFIED" and result["strict_verified"]
    assert result["gate"]["status"] == "NOT_ELIGIBLE"
    assert result["candidate_deployment_verified"] is False
    assert result["config"]["suite"]["id"] == "suite-a"
    assert result["config"]["protocol"]["module_id"] == "sql"
    assert result["config"]["scoring"]["scorer"] == scorer_identity()


@pytest.mark.parametrize("artifact", ["predictions.jsonl", "metrics.json", "per_sample.jsonl", "config.json", "gates.json"])
def test_any_changed_bound_artifact_is_rejected(make_report, artifact):
    report = make_report()
    with (report / artifact).open("a", encoding="utf-8") as target:
        target.write(" ")
    assert verify_report(report)["errors"] == [f"REPORT_ARTIFACT_HASH_MISMATCH: {artifact}"]


def test_metrics_and_gate_cannot_be_forged_together_even_with_matching_hashes(make_report):
    report = make_report(rows=[{"id": "sql", "result": {"rows": [], "total": 0}, "model_calls": []}])
    metrics = read_json(report / "metrics.json")
    metrics["task_success_rate"] = 1
    write_json(report / "metrics.json", metrics)
    gate = gates("sql", metrics, eligible=False)
    gate["review_policy"] = "human"
    write_json(report / "gates.json", gate)
    reseal_artifacts(report)
    assert verify_report(report)["errors"] == ["METRICS_RECOMPUTATION_MISMATCH"]


def test_per_sample_content_is_recomputed_not_just_hashed(make_report):
    report = make_report()
    rows = read_jsonl(report / "per_sample.jsonl")
    rows[0]["checks"][0]["actual"] = "fabricated evidence"
    write_jsonl(report / "per_sample.jsonl", rows)
    reseal_artifacts(report)
    assert verify_report(report)["errors"] == ["SAMPLES_RECOMPUTATION_MISMATCH"]


def test_candidate_and_scorer_bindings_are_checked(make_report):
    report = make_report()
    assert verify_report(report, expected_candidate={**CANDIDATE, "id": "candidate-b"})["errors"] == ["CANDIDATE_MISMATCH"]
    seal = read_json(report / "integrity.json")
    seal["scoring"]["scorer"]["sha256"] = "older-scorer"
    for name in ("manifest.json", "config.json"):
        value = read_json(report / name)
        value["scoring"] = seal["scoring"]
        write_json(report / name, value)
    write_json(report / "integrity.json", seal)
    reseal_artifacts(report)
    assert verify_report(report)["errors"] == ["SCORER_VERSION_MISMATCH"]
    assert verify_report(report, strict=False)["status"] == "LEGACY_UNVERIFIED"


def test_legacy_inspection_requires_explicit_mode(make_report):
    report = make_report()
    (report / "integrity.json").unlink()
    assert verify_report(report)["errors"] == ["LEGACY_REPORT_REQUIRES_EXPLICIT_REPLAY"]
    legacy = verify_report(report, strict=False)
    assert legacy["status"] == "LEGACY_UNVERIFIED" and not legacy["strict_verified"]


def test_comparison_reports_protocol_changes_without_claiming_quality_gain(make_report):
    before = make_report(rows=[{"id": "sql", "result": {"rows": [], "total": 0}}])
    after = make_report(threshold="quality_gate_v3")
    changes = compare(before, after)
    assert not changes["compatible"] and not changes["quality_comparable"]
    assert "threshold_version" in changes["protocol_changes"]
    assert "delta" not in changes["metric_changes"]["task_success_rate"]


def test_compare_includes_unknown_metrics_and_assertion_changes_in_still_failed_cases(make_report):
    before = make_report(rows=[{"id": "sql", "result": {"rows": [], "total": 0}}])
    after = make_report(rows=[{"id": "sql", "result": {"rows": [1], "total": 0}, "elapsed_ms": 20}])
    changes = compare(before, after)
    assert changes["quality_comparable"]
    assert changes["new_failures"] == [] and changes["recovered"] == []
    assert changes["metric_changes"]["efficiency.p50_ms"]["transition"] == "NEWLY_MEASURED"
    changed = next(item for item in changes["assertion_changes"] if item["assertion"] == "turn0.rows")
    assert changed["before"]["status"] == "FAIL" and changed["after"]["status"] == "PASS"


def test_strict_bundle_rejects_different_candidate(make_report, tmp_path, monkeypatch):
    report = make_report(formal=True)
    monkeypatch.setattr("eval.release.SECTIONS", ("sql",))
    bundle = {"candidate": {**CANDIDATE, "id": "candidate-b"}, "modules": {"sql": {
        "dataset": (report / "gold").relative_to(tmp_path).as_posix(), "report": report.relative_to(tmp_path).as_posix()}}}
    result = check_bundle(bundle, tmp_path)
    assert result["status"] == "BLOCKED"
    assert result["modules"]["sql"]["error"] == "CANDIDATE_MISMATCH"


def test_legacy_bundle_can_never_certify_a_new_release(make_report, tmp_path, monkeypatch):
    report = make_report(formal=True)
    monkeypatch.setattr("eval.release.SECTIONS", ("sql",))
    bundle = {"modules": {"sql": {"dataset": (report / "gold").relative_to(tmp_path).as_posix(),
                                  "report": report.relative_to(tmp_path).as_posix()}}}
    result = check_bundle(bundle, tmp_path, strict=False)
    assert result["status"] == "LEGACY_UNVERIFIED" and not result["strict_verified"]


@pytest.mark.parametrize("remote", [False, True])
def test_release_requires_actual_source_binding_but_evidence_verification_stays_valid(make_report, tmp_path, monkeypatch, remote):
    source_hashes = {"src/candidate.py": "declared-source-file-hash"}
    candidate = {**CANDIDATE, "runtime_fingerprint": digest(source_hashes)}
    report = make_report(formal=True, candidate=candidate, collection={
        "adapter": "http" if remote else "sql",
        "runtime_verification": "REMOTE_SOURCE_NOT_VERIFIED" if remote else "LOCAL_SOURCE_BOUND"})
    monkeypatch.setattr("eval.release.SECTIONS", ("sql",))
    bundle = {"candidate": candidate, "modules": {"sql": {
        "dataset": (report / "gold").relative_to(tmp_path).as_posix(), "report": report.relative_to(tmp_path).as_posix()}}}
    assert verify_report(report)["status"] == "VERIFIED"
    result = check_bundle(bundle, tmp_path)
    assert result["status"] == ("BLOCKED" if remote else "PASSED")
    assert result["modules"]["sql"]["candidate_binding_verified"] is not remote
    assert result["modules"]["sql"]["candidate_deployment_verified"] is False
    if remote:
        assert "REMOTE_SOURCE_NOT_VERIFIED" in result["modules"]["sql"]["candidate_issues"]


def test_changed_alignment_is_detected_even_after_updating_all_its_hashes(tmp_path, monkeypatch):
    from eval.demo import demo
    monkeypatch.setattr("eval.run.provenance", lambda: {})
    report = demo(tmp_path / "exercise")["extraction"]
    rows = read_jsonl(report / "alignments.jsonl")
    rows[0]["pairs"] = []
    write_jsonl(report / "alignments.jsonl", rows)
    metadata = read_json(report / "manifest.json")
    metadata["alignments_sha256"] = digest((report / "alignments.jsonl").read_bytes())
    write_json(report / "manifest.json", metadata)
    reseal_artifacts(report)
    assert verify_report(report)["errors"] == ["METRICS_RECOMPUTATION_MISMATCH"]


def test_source_binding_cannot_replace_explicit_model_and_prompt_expectations():
    hashes = {"src/candidate.py": "source-file-hash"}
    component = {"version": "dedup-v1"}
    candidate = {"id": "candidate-a", "runtime_fingerprint": digest(hashes), "components": {"dedup": component}}
    metadata = {"section": "dedup", "candidate": candidate}
    config = {"protocol": {"observation_scope": "component"}, "collection": {"schema": "collection_v2",
        "adapter": "provider", "runtime_verification": "LOCAL_SOURCE_BOUND",
        "provenance": {"source_sha256": digest(hashes), "file_hashes": hashes}}}
    rows = [{"model_calls": [{"operation_type": "DEDUP_JUDGE", "model": "model-a", "prompt_version": "prompt-a"}]}]
    result = _candidate_verification(metadata, config, rows)
    assert not result["candidate_binding_verified"]
    assert "CANDIDATE_EXPECTATION_MISSING: model" in result["issues"]
    assert "CANDIDATE_EXPECTATION_MISSING: prompt_version" in result["issues"]
    component.update(model="model-a", prompt_version="prompt-a")
    assert _candidate_verification(metadata, config, rows)["candidate_binding_verified"]
    component["model"] = "model-b"
    assert "CANDIDATE_OBSERVATION_NOT_BOUND: model" in _candidate_verification(metadata, config, rows)["issues"]


def test_protocol_rebinding_cannot_hide_changed_threshold_from_recomputation(make_report):
    report = make_report()
    seal = read_json(report / "integrity.json")
    seal["scoring"]["threshold_version"] = "quality_gate_v3"
    for name in ("manifest.json", "config.json"):
        value = read_json(report / name)
        value["scoring"] = seal["scoring"]
        if name == "config.json":
            value["threshold_version"] = "quality_gate_v3"
        write_json(report / name, value)
    write_json(report / "integrity.json", seal)
    reseal_artifacts(report)
    assert verify_report(report)["errors"] == ["GATE_RECOMPUTATION_MISMATCH"]


@pytest.mark.parametrize("declaration,section,operation", [
    ("model_revision", "dedup", "DEDUP_JUDGE"),
    ("embedding_model_revision", "retrieval", "EMBEDDING"),
    ("reranker_model_revision", "retrieval", "RERANK"),
])
@pytest.mark.parametrize("observed_revision", ["revision-a", "revision-b", None])
def test_release_binds_declared_provider_revisions(declaration, section, operation, observed_revision):
    hashes = {"src/candidate.py": "source-file-hash"}
    component = {"version": "component-v1", "kind": "model", "metadata": {"label": "candidate"},
                 "prompt_version": "prompt-a", declaration: "revision-a"}
    if section == "retrieval":
        component.update(embedding_model="embedding-a", reranker_model="reranker-a")
        calls = [{"operation_type": "EMBEDDING", "model": "embedding-a"},
                 {"operation_type": "RERANK", "model": "reranker-a", "prompt_version": "prompt-a"}]
    else:
        component["model"] = "model-a"
        calls = [{"operation_type": "DEDUP_JUDGE", "model": "model-a", "prompt_version": "prompt-a"}]
    if observed_revision is not None:
        next(call for call in calls if call["operation_type"] == operation)["model_revision"] = observed_revision
    metadata = {"section": section, "candidate": {"id": "candidate-a", "runtime_fingerprint": digest(hashes),
                                                   "components": {section: component}}}
    config = {"protocol": {"observation_scope": "component"}, "collection": {"schema": "collection_v2",
        "adapter": "provider", "runtime_verification": "LOCAL_SOURCE_BOUND",
        "provenance": {"source_sha256": digest(hashes), "file_hashes": hashes}}}
    result = _candidate_verification(metadata, config, [{"model_calls": calls}])
    assert result["candidate_binding_verified"] is (observed_revision == "revision-a")
    assert result["issues"] == ([] if observed_revision == "revision-a" else
                                [f"CANDIDATE_OBSERVATION_NOT_BOUND: {declaration}"])
    del component[declaration]
    assert _candidate_verification(metadata, config, [{"model_calls": calls}])["candidate_binding_verified"]
