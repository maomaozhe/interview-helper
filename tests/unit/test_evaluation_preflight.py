"""Preflight must reject ambiguous evidence before any external operation."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from eval.common import digest, read_json, write_json, write_jsonl
from eval.preflight import SuiteValidationError, load_suite, preflight
from eval.prepare import draft


def suite_files(tmp_path, *, section="task_labels"):
    dataset = tmp_path / "gold"
    item = draft("case-1", "group-1", ["synthetic"])
    if section == "task_labels":
        item.update(response_form="CODE", coding_focus="ENGINEERING", occurrence_id="occ-1")
        result = {"response_form": "CODE", "coding_focus": "ENGINEERING"}
    elif section == "sql":
        item.update(request={"page_size": 2}, assertions=[{"id": "rows", "path": "result.rows", "op": "eq", "value": []}])
        result = {"rows": []}
    else:
        item.update(message="list questions", required_tools=["list_questions"],
                    assertions=[{"id": "rows", "path": "result.rows", "op": "exists"}])
        result = {"rows": []}
    write_json(dataset / "manifest.json", {"version": "fixture-v1", "kind": "fixture", "status": "draft", section: [item]})
    write_jsonl(tmp_path / "predictions.jsonl", [{"id": item["id"], "result": result, "failed": False}])
    suite = {"schema_version": "evaluation_suite_v1", "id": "offline-check",
             "candidate": {"id": "candidate-v1", "components": {section: {"kind": "synthetic", "version": "v1"}}},
             "modules": [{"id": "main", "section": section, "dataset": "gold", "split": "dev",
                          "review_policy": "human", "threshold_version": "quality_gate_v3",
                          "default_pipeline": "HYBRID_RERANK", "predictions": "predictions.jsonl"}]}
    write_json(tmp_path / "suite.json", suite)
    return tmp_path / "suite.json", suite


def save_load(path, suite):
    write_json(path, suite)
    return load_suite(path)


def details(report):
    return {check["detail"] for check in report["checks"]}


def test_stored_suite_resolves_paths_and_never_probes_services(tmp_path, monkeypatch):
    path, _ = suite_files(tmp_path)
    import httpx
    monkeypatch.setattr(httpx.Client, "__init__", lambda *a, **k: pytest.fail("preflight attempted a service client"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    suite = load_suite(path)
    assert suite["modules"][0]["dataset"] == str((tmp_path / "gold").resolve())
    assert suite["suite_path"] == str(path.resolve())
    report = preflight(suite)
    assert report["status"] == "PASS"
    assert report["scope"] == "LOCAL_STATIC_PREFLIGHT_ONLY"
    assert len(report["environment"]["local_source_sha256"]) == 64
    assert report["environment"]["scope"] == "LOCAL_WORKSPACE_ONLY_NOT_REMOTE_ATTESTATION"
    assert "file_hashes" not in report["environment"]
    assert report["runtime_verification"] == "NOT_APPLICABLE"
    assert report["modules"][0]["candidate_verification"] == "DECLARED_ONLY"


@pytest.mark.parametrize("field,code", [
    ("split", "SUITE_SPLIT_REQUIRED"), ("review_policy", "SUITE_REVIEW_POLICY_REQUIRED"),
    ("threshold_version", "SUITE_THRESHOLD_REQUIRED"), ("default_pipeline", "SUITE_PIPELINE_REQUIRED"),
])
def test_protocol_choices_are_explicit(tmp_path, field, code):
    path, suite = suite_files(tmp_path)
    del suite["modules"][0][field]
    with pytest.raises(SuiteValidationError, match=code):
        save_load(path, suite)


@pytest.mark.parametrize("identifier", ["case.", "CON", "NUL.txt", "COM1", "lpt9.log"])
def test_module_output_paths_are_portable_to_windows(tmp_path, identifier):
    path, suite = suite_files(tmp_path)
    suite["modules"][0]["id"] = identifier
    with pytest.raises(SuiteValidationError, match="SUITE_MODULE_ID_INVALID"):
        save_load(path, suite)


def test_case_only_module_ids_cannot_share_an_output_directory(tmp_path):
    path, suite = suite_files(tmp_path)
    duplicate = copy.deepcopy(suite["modules"][0])
    duplicate["id"] = "MAIN"
    suite["modules"].append(duplicate)
    with pytest.raises(SuiteValidationError, match="SUITE_MODULE_ID_INVALID"):
        save_load(path, suite)


def test_unconfigured_classification_thresholds_cannot_be_a_successful_preflight(tmp_path):
    path, suite = suite_files(tmp_path)
    suite["modules"][0]["threshold_version"] = "quality_gate_v2"
    with pytest.raises(SuiteValidationError, match="SECTION_THRESHOLDS_NOT_CONFIGURED"):
        save_load(path, suite)


def test_candidate_requires_relevant_component_and_checks_local_files(tmp_path):
    path, suite = suite_files(tmp_path)
    suite["candidate"]["components"] = {"routing": {"version": "v1"}}
    with pytest.raises(SuiteValidationError, match="CANDIDATE_SECTION_MISSING"):
        save_load(path, suite)
    source = tmp_path / "prompt.md"
    source.write_text("frozen prompt", encoding="utf-8")
    suite["candidate"]["components"] = {"task_labels": {"version": "v1", "files": {"prompt.md": digest(source.read_bytes())}}}
    normalized = save_load(path, suite)
    assert preflight(normalized)["status"] == "PASS"
    source.write_text("changed prompt", encoding="utf-8")
    report = preflight(normalized)
    assert report["status"] == "BLOCKED"
    assert "CANDIDATE_FILE_HASH_MISMATCH" in details(report)


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "snapshot", "receipt"])
def test_prediction_identity_mismatches_block_before_scoring(tmp_path, mutation):
    path, _ = suite_files(tmp_path)
    predicted = {"id": "case-1", "result": {"response_form": "CODE", "coding_focus": "ENGINEERING"}}
    if mutation == "duplicate":
        write_jsonl(tmp_path / "predictions.jsonl", [predicted, predicted])
    elif mutation == "missing":
        write_jsonl(tmp_path / "predictions.jsonl", [])
    elif mutation == "snapshot":
        manifest = read_json(tmp_path / "gold" / "manifest.json")
        manifest["snapshot"] = {"corpus_revision": 2}
        write_json(tmp_path / "gold" / "manifest.json", manifest)
    else:
        write_json(tmp_path / "predictions.collection.json", {"predictions_sha256": "0" * 64})
    report = preflight(load_suite(path))
    assert report["status"] == "BLOCKED"
    assert any(c["name"] == "main.predictions" and c["status"] == "BLOCKED" for c in report["checks"])


def test_gold_failure_is_safe_and_does_not_echo_private_error_text(tmp_path, monkeypatch):
    path, _ = suite_files(tmp_path)
    import eval.validate_gold as validator
    def reject(*args, **kwargs):
        raise ValueError("secret-provider-url-and-user-text")
    monkeypatch.setattr(validator, "validate_gold", reject)
    report = preflight(load_suite(path))
    assert report["status"] == "BLOCKED"
    assert "secret-provider" not in repr(report)
    assert "GOLD_INVALID_CHECK_SOURCE_HASHES_REVIEW_AND_SPLIT" in details(report)


def test_changed_frozen_gold_is_rejected_without_scoring(tmp_path):
    path, _ = suite_files(tmp_path)
    manifest_path = tmp_path / "gold" / "manifest.json"
    manifest = read_json(manifest_path)
    write_json(tmp_path / "gold" / "freeze.json", {"dataset_sha256": digest(manifest), "file_hashes": {}})
    manifest["task_labels"][0]["coding_focus"] = "ALGORITHM"
    write_json(manifest_path, manifest)
    report = preflight(load_suite(path))
    assert report["status"] == "BLOCKED"
    assert "GOLD_INVALID_CHECK_SOURCE_HASHES_REVIEW_AND_SPLIT" in details(report)


def test_wrong_python_fails_with_actionable_code(tmp_path, monkeypatch):
    path, _ = suite_files(tmp_path)
    import eval.preflight as module
    monkeypatch.setattr(module.sys, "version_info", (3, 11, 9))
    report = preflight(load_suite(path))
    assert report["status"] == "BLOCKED"
    assert "PYTHON_3_12_REQUIRED" in details(report)


def live_suite(tmp_path, *, adapter="http"):
    section = "routing" if adapter == "http" else "sql" if adapter == "sql" else "task_labels"
    path, suite = suite_files(tmp_path, section=section)
    module = suite["modules"][0]
    del module["predictions"]
    options = {} if adapter == "sql" else {"max_calls": 10, "max_tokens": 100000}
    if adapter == "http":
        options.update(base_url="http://127.0.0.1:18002", expected_actor="eval-test-run")
    module["collect"] = {"adapter": adapter, "options": options}
    manifest_path = tmp_path / "gold" / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["snapshot"] = {"corpus_revision": 1, "indexed_revision": 1, "task_annotation_revision": 0, "as_of": "2026-10-15"}
    write_json(manifest_path, manifest)
    return path, suite


@pytest.mark.parametrize("adapter", ["http", "sql"])
def test_service_modes_need_authorization_but_local_pass_never_proves_runtime(tmp_path, adapter, monkeypatch):
    path, suite = live_suite(tmp_path, adapter=adapter)
    import httpx
    monkeypatch.setattr(httpx.Client, "__init__", lambda *a, **k: pytest.fail("preflight attempted a service client"))
    normalized = save_load(path, suite)
    report = preflight(normalized)
    assert report["status"] == "BLOCKED"
    assert "LIVE_COLLECTION_REQUIRES_ALLOW_LIVE" in details(report)
    report = preflight(normalized, allow_live=True)
    assert report["status"] == "PASS"
    assert report["runtime_verification"] == "PENDING"
    assert any(c["name"] == "main.runtime" and c["status"] == "PENDING" for c in report["checks"])


@pytest.mark.parametrize("field,value,code", [
    ("max_calls", 0, "POSITIVE_MODEL_BUDGET_REQUIRED"),
    ("max_tokens", True, "POSITIVE_MODEL_BUDGET_REQUIRED"),
    ("expected_actor", "normal-user", "EVALUATION_ACTOR_REQUIRED"),
    ("base_url", "http://person:secret@localhost:18002", "EVALUATION_BASE_URL_INVALID"),
    ("base_url", "http://localhost:18002?api_key=secret", "EVALUATION_BASE_URL_INVALID"),
])
def test_http_unsafe_or_incomplete_configuration_is_rejected(tmp_path, field, value, code):
    path, suite = live_suite(tmp_path)
    suite["modules"][0]["collect"]["options"][field] = value
    with pytest.raises(SuiteValidationError, match=code):
        save_load(path, suite)


def test_provider_requires_shared_linux_lock_without_loading_credentials(tmp_path, monkeypatch):
    path, suite = live_suite(tmp_path, adapter="provider")
    import eval.preflight as module
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    report = preflight(save_load(path, suite), allow_live=True)
    assert "SHARED_LINUX_MODEL_GATE_REQUIRED" in details(report)
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setenv("MODEL_LOCK_PATH", str(tmp_path / "independent.lock"))
    report = preflight(load_suite(path), allow_live=True)
    assert "SHARED_LINUX_MODEL_GATE_REQUIRED" in details(report)


def test_service_collection_cannot_guess_a_snapshot(tmp_path):
    path, suite = live_suite(tmp_path, adapter="sql")
    manifest_path = tmp_path / "gold" / "manifest.json"
    manifest = read_json(manifest_path)
    del manifest["snapshot"]
    write_json(manifest_path, manifest)
    report = preflight(save_load(path, suite), allow_live=True)
    assert report["status"] == "BLOCKED"
    assert "COLLECTION_SNAPSHOT_REQUIRED" in details(report)


def test_http_cookie_preflight_checks_private_format_without_reporting_value(tmp_path):
    path, suite = live_suite(tmp_path)
    cookie = tmp_path / "cookies.json"
    write_json(cookie, {"ii_tenant_session": "private-session-value"})
    cookie.chmod(0o600)
    suite["modules"][0]["collect"]["options"]["http_cookie_file"] = "cookies.json"
    report = preflight(save_load(path, suite), allow_live=True)
    assert report["status"] == "PASS" and "private-session-value" not in str(report)
    write_json(cookie, {"unrelated": "private-session-value"})
    report = preflight(load_suite(path), allow_live=True)
    assert report["status"] == "BLOCKED" and "private-session-value" not in str(report)


def test_published_facts_are_offline_and_hash_bound(tmp_path, monkeypatch):
    path, suite = suite_files(tmp_path)
    module = suite["modules"][0]
    del module["predictions"]
    module["collect"] = {"adapter": "published", "options": {"snapshot_export": "snapshot"}}
    facts = {"snapshot": {}, "questions": [{"id": "occ-1", "task_annotation": {"coding_focus": "ENGINEERING"}}]}
    write_json(tmp_path / "snapshot" / "facts.json", facts)
    write_json(tmp_path / "snapshot" / "manifest.json", {"snapshot": {}, "facts_sha256": digest(facts)})
    import httpx
    monkeypatch.setattr(httpx.Client, "__init__", lambda *a, **k: pytest.fail("preflight attempted a service client"))
    normalized = save_load(path, suite)
    assert preflight(normalized)["status"] == "PASS"
    facts["questions"][0]["task_annotation"] = None
    write_json(tmp_path / "snapshot" / "facts.json", facts)
    report = preflight(normalized)
    assert "SNAPSHOT_EXPORT_HASH_MISMATCH" in details(report)


def test_duplicate_sections_cannot_silently_omit_regressions(tmp_path):
    path, suite = suite_files(tmp_path)
    second = copy.deepcopy(suite["modules"][0])
    second["id"] = "regression"
    suite["modules"].append(second)
    with pytest.raises(SuiteValidationError, match="DUPLICATE_SECTIONS_REQUIRE_RELEASE_MAPPING"):
        save_load(path, suite)
    suite["release"] = {"modules": {"task_labels": "main"}, "required_regressions": {"old-cases": "regression"}}
    assert preflight(save_load(path, suite))["status"] == "PASS"
    suite["release"]["required_regressions"] = {}
    with pytest.raises(SuiteValidationError, match="RELEASE_MODULE_OMITTED"):
        save_load(path, suite)


def test_release_references_are_section_checked(tmp_path):
    path, suite = suite_files(tmp_path)
    suite["release"] = {"modules": {"sql": "main"}}
    with pytest.raises(SuiteValidationError, match="RELEASE_PRIMARY_REFERENCE_INVALID"):
        save_load(path, suite)


def test_suite_credentials_and_host_overrides_are_rejected_without_echo(tmp_path):
    path, suite = suite_files(tmp_path)
    suite["candidate"]["api_key"] = "sk-never-print-me"
    with pytest.raises(SuiteValidationError, match="SUITE_CREDENTIALS_FORBIDDEN") as error:
        save_load(path, suite)
    assert "sk-never" not in str(error.value)
    del suite["candidate"]["api_key"]
    del suite["modules"][0]["predictions"]
    suite["modules"][0]["collect"] = {"adapter": "provider", "options": {"section": "sql", "max_calls": 1, "max_tokens": 10}}
    with pytest.raises(SuiteValidationError, match="COLLECTION_OPTIONS_INVALID"):
        save_load(path, suite)


def test_missing_alignment_or_prediction_hash_change_blocks_extraction(tmp_path):
    from eval.demo import demo
    output = tmp_path / "exercise"
    demo(output)
    fixture = output / "fixture"
    suite = {"schema_version": "evaluation_suite_v1", "id": "extract-check",
             "candidate": {"id": "synthetic", "components": {"extraction": {"version": "v1"}}},
             "modules": [{"id": "extract", "section": "extraction", "dataset": str(fixture),
                          "split": "dev", "review_policy": "human", "threshold_version": "quality_gate_v3",
                          "default_pipeline": "HYBRID_RERANK", "predictions": str(fixture / "extraction.predictions.jsonl")}]}
    path = tmp_path / "suite.json"
    assert preflight(save_load(path, suite))["status"] == "BLOCKED"
    suite["modules"][0]["alignments"] = str(fixture / "alignments.jsonl")
    assert preflight(save_load(path, suite))["status"] == "PASS"
    alignment = {"id": "extraction", "kind": "synthetic_alignment", "human_verified": False,
                 "prediction_sha256": "0" * 64, "pairs": [], "sessions": []}
    write_jsonl(fixture / "alignments.jsonl", [alignment])
    assert preflight(load_suite(path))["status"] == "BLOCKED"

    # A future collection binds the path before prediction exists. Its review is
    # a later stage, rather than requiring an impossible pre-existing result hash.
    module = suite["modules"][0]
    del module["predictions"]
    del module["alignments"]
    module["collect"] = {"adapter": "published", "options": {"snapshot_export": "snapshot"}}
    with pytest.raises(SuiteValidationError, match="COLLECTED_EXTRACTION_ALIGNMENT_PATH_REQUIRED"):
        save_load(path, suite)
    module["alignments"] = "new-alignment.jsonl"
    gold = read_json(fixture / "manifest.json")["extraction"][0]
    facts = {"snapshot": {}, "documents": [{"source_hash": gold["source_hash"], "path": "source.md", "build_id": "b",
                                           "decision": "INCLUDED", "document_kind": "INTERVIEW"}],
             "interviews": [], "questions": [], "followups": []}
    write_json(tmp_path / "snapshot" / "facts.json", facts)
    write_json(tmp_path / "snapshot" / "manifest.json", {"snapshot": {}, "facts_sha256": digest(facts)})
    report = preflight(save_load(path, suite))
    assert report["status"] == "PASS"
    assert report["modules"][0]["alignment_verification"] == "PENDING"
    write_jsonl(tmp_path / "new-alignment.jsonl", [{"id": "wrong-source"}])
    assert preflight(load_suite(path))["status"] == "BLOCKED"
