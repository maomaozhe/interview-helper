"""Validate explicit suites and local evidence without probing services or models."""
from __future__ import annotations

import copy
import importlib
import os
import platform
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from eval.common import PIPELINES, SECTIONS, digest, provenance, read_json, read_jsonl


class SuiteValidationError(ValueError):
    """Safe configuration codes; never include supplied values or credentials."""


ADAPTER_SECTIONS = {
    "published": {"extraction", "task_labels"}, "sql": {"sql"},
    "provider": {"extraction", "task_labels", "dedup"},
    "retrieval": {"retrieval"}, "http": {"routing"},
}
MODEL_ADAPTERS = {"provider", "retrieval", "http"}
SERVICE_ADAPTERS = MODEL_ADAPTERS | {"sql"}
OPTIONS = {
    "snapshot_export", "base_url", "max_calls", "max_tokens", "allow_writes", "expected_actor",
    "extraction_prompt_version", "extraction_thinking_mode", "dedup_prompt_version",
    "dedup_verify_equivalence", "dedup_source_context", "deployment_evidence", "http_cookie_file",
}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL"} | {f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)}


def _require(condition, code):
    if not condition:
        raise SuiteValidationError(code)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _path(root, value):
    _require(_text(value) and "\x00" not in value, "SUITE_PATH_REQUIRED")
    return str((root / value).resolve())


def _no_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold().replace("-", "_")
            _require(not any(part in normalized for part in ("api_key", "password", "secret", "credential"))
                     and normalized not in {"token", "authorization", "database_url"},
                     "SUITE_CREDENTIALS_FORBIDDEN")
            _no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            _no_secrets(item)


def _normalize(value, root):
    _require(isinstance(value, dict), "SUITE_OBJECT_REQUIRED")
    suite = copy.deepcopy(value)
    _no_secrets(suite)
    _require(suite.get("schema_version") == "evaluation_suite_v1", "SUITE_SCHEMA_UNSUPPORTED")
    _require(isinstance(suite.get("id"), str) and IDENTIFIER.fullmatch(suite["id"]), "SUITE_ID_INVALID")
    candidate = suite.get("candidate")
    _require(isinstance(candidate, dict) and _text(candidate.get("id")), "CANDIDATE_ID_REQUIRED")
    components = candidate.get("components")
    _require(isinstance(components, dict) and components, "CANDIDATE_COMPONENTS_REQUIRED")
    for name, component in components.items():
        _require(isinstance(name, str) and IDENTIFIER.fullmatch(name)
                 and isinstance(component, dict) and _text(component.get("version")), "CANDIDATE_VERSION_REQUIRED")
        if "files" in component:
            _require(isinstance(component["files"], dict) and component["files"], "CANDIDATE_FILES_INVALID")
            normalized = {}
            for filename, expected in component["files"].items():
                _require(isinstance(expected, str) and HASH.fullmatch(expected), "CANDIDATE_FILE_HASH_INVALID")
                absolute = _path(root, filename)
                _require(absolute not in normalized, "CANDIDATE_FILE_DUPLICATE")
                normalized[absolute] = expected
            component["files"] = normalized
    modules = suite.get("modules")
    _require(isinstance(modules, list) and modules, "SUITE_MODULES_REQUIRED")
    ids = set()
    for module in modules:
        _require(isinstance(module, dict), "SUITE_MODULE_INVALID")
        _require(not set(module) - {"id", "section", "dataset", "split", "review_policy", "threshold_version",
                                    "default_pipeline", "predictions", "collect", "alignments", "answer_reviews"}, "SUITE_MODULE_FIELD_UNKNOWN")
        key = module.get("id")
        _require(isinstance(key, str) and IDENTIFIER.fullmatch(key) and not key.endswith(".")
                 and key.split(".", 1)[0].upper() not in WINDOWS_DEVICES and key.casefold() not in ids,
                 "SUITE_MODULE_ID_INVALID")
        ids.add(key.casefold())
        section = module.get("section")
        _require(section in SECTIONS, "SUITE_SECTION_INVALID")
        _require(section in components, "CANDIDATE_SECTION_MISSING")
        _require(module.get("split") in {"dev", "test"}, "SUITE_SPLIT_REQUIRED")
        _require(module.get("review_policy") in {"human", "delegated_agent"}, "SUITE_REVIEW_POLICY_REQUIRED")
        _require(module.get("default_pipeline") in PIPELINES, "SUITE_PIPELINE_REQUIRED")
        _require(module.get("threshold_version") in {"quality_gate_v2", "quality_gate_v3"}, "SUITE_THRESHOLD_REQUIRED")
        _require(not (section == "task_labels" and module["threshold_version"] == "quality_gate_v2"),
                 "SECTION_THRESHOLDS_NOT_CONFIGURED")
        module["dataset"] = _path(root, module.get("dataset"))
        _require(("predictions" in module) != ("collect" in module), "PREDICTIONS_OR_COLLECTION_REQUIRED")
        for field in ("predictions", "alignments", "answer_reviews"):
            if field in module:
                module[field] = _path(root, module[field])
        _require("alignments" not in module or section == "extraction", "ALIGNMENTS_REQUIRE_EXTRACTION")
        _require("answer_reviews" not in module or section == "routing", "ANSWER_REVIEWS_REQUIRE_ROUTING")
        _require("answer_reviews" not in module or "predictions" in module,
                 "ANSWER_REVIEWS_REQUIRE_STORED_PREDICTIONS")
        if "collect" not in module:
            continue
        collection = module["collect"]
        _require(isinstance(collection, dict) and not set(collection) - {"adapter", "options"}, "COLLECTION_INVALID")
        adapter = collection.get("adapter")
        _require(adapter in ADAPTER_SECTIONS and section in ADAPTER_SECTIONS[adapter], "ADAPTER_SECTION_MISMATCH")
        _require(section != "extraction" or "alignments" in module, "COLLECTED_EXTRACTION_ALIGNMENT_PATH_REQUIRED")
        options = collection.get("options", {})
        _require(isinstance(options, dict) and not set(options) - OPTIONS, "COLLECTION_OPTIONS_INVALID")
        collection["options"] = options
        for flag in ("allow_writes", "dedup_verify_equivalence", "dedup_source_context"):
            _require(flag not in options or type(options[flag]) is bool, "COLLECTION_BOOLEAN_INVALID")
        if adapter in MODEL_ADAPTERS:
            _require(all(type(options.get(k)) is int and options[k] > 0 for k in ("max_calls", "max_tokens")),
                     "POSITIVE_MODEL_BUDGET_REQUIRED")
        elif "max_calls" in options or "max_tokens" in options:
            raise SuiteValidationError("MODEL_BUDGET_ON_NON_MODEL_ADAPTER")
        if adapter == "published":
            options["snapshot_export"] = _path(root, options.get("snapshot_export"))
        else:
            _require("snapshot_export" not in options, "SNAPSHOT_EXPORT_REQUIRES_PUBLISHED")
        if adapter == "http":
            for filename in ("deployment_evidence", "http_cookie_file"):
                if filename in options:
                    options[filename] = _path(root, options[filename])
            _require(_text(options.get("base_url")), "EVALUATION_BASE_URL_REQUIRED")
            parsed = urlsplit(options["base_url"])
            _require(parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
                     and not parsed.password and not parsed.query and not parsed.fragment,
                     "EVALUATION_BASE_URL_INVALID")
            actor = options.get("expected_actor")
            _require(isinstance(actor, str) and actor.startswith("eval-") and IDENTIFIER.fullmatch(actor),
                     "EVALUATION_ACTOR_REQUIRED")
        else:
            _require(not {"base_url", "expected_actor", "allow_writes", "deployment_evidence", "http_cookie_file"}.intersection(options), "HTTP_OPTIONS_REQUIRE_HTTP")
        if any(k.startswith("extraction_") for k in options):
            _require(adapter == "provider" and section == "extraction", "EXTRACTION_OPTIONS_REQUIRE_PROVIDER")
        if any(k.startswith("dedup_") for k in options):
            _require(adapter == "provider" and section == "dedup", "DEDUP_OPTIONS_REQUIRE_PROVIDER")
        if "extraction_thinking_mode" in options:
            _require(options["extraction_thinking_mode"] in {"auto", "enabled", "disabled"}, "THINKING_MODE_INVALID")
        for field in ("extraction_prompt_version", "dedup_prompt_version"):
            if field in options:
                supported = ({f"extract_question_v{version}" for version in range(2, 7)}
                             if field == "extraction_prompt_version" else {f"dedup_judge_v{version}" for version in range(1, 4)})
                _require(isinstance(options[field], str) and options[field] in supported, "PROMPT_VERSION_INVALID")
                declared = components[section].get("prompt_version")
                _require(declared is None or options[field] == declared, "CANDIDATE_PROMPT_MISMATCH")
    release = suite.get("release")
    by_id = {module["id"]: module for module in modules}
    if release is not None:
        _require(isinstance(release, dict) and not set(release) - {"modules", "required_regressions"},
                 "RELEASE_CONFIGURATION_INVALID")
        primary = release.get("modules")
        regressions = release.get("required_regressions", {})
        _require(isinstance(primary, dict) and primary and isinstance(regressions, dict), "RELEASE_MODULES_REQUIRED")
        for section, key in primary.items():
            _require(section in SECTIONS and isinstance(key, str) and key in by_id
                     and by_id[key]["section"] == section, "RELEASE_PRIMARY_REFERENCE_INVALID")
        for name, key in regressions.items():
            _require(isinstance(name, str) and IDENTIFIER.fullmatch(name) and isinstance(key, str)
                     and key in by_id, "RELEASE_REGRESSION_REFERENCE_INVALID")
        _require(set(primary.values()) | set(regressions.values()) == set(by_id), "RELEASE_MODULE_OMITTED")
    else:
        _require(len({module["section"] for module in modules}) == len(modules),
                 "DUPLICATE_SECTIONS_REQUIRE_RELEASE_MAPPING")
    return suite


def load_suite(path) -> dict:
    """Resolve paths relative to the suite, reject ambiguous configuration."""
    path = Path(path).resolve()
    try:
        value = read_json(path)
    except (OSError, ValueError, UnicodeError) as error:
        raise SuiteValidationError("SUITE_FILE_UNREADABLE_OR_INVALID") from error
    try:
        suite = _normalize(value, path.parent)
    except (TypeError, KeyError) as error:
        raise SuiteValidationError("SUITE_CONFIGURATION_INVALID") from error
    suite["suite_path"] = str(path)
    return suite


def _environment():
    _require(sys.version_info[:2] == (3, 12), "PYTHON_3_12_REQUIRED")
    for name in ("pydantic", "pydantic_settings", "sqlalchemy", "yaml", "httpx", "numpy", "openai", "eval.scoring"):
        importlib.import_module(name)
    module = importlib.import_module("interview_intelligence")
    expected = Path(__file__).resolve().parents[1] / "src" / "interview_intelligence"
    _require(module.__file__ and Path(module.__file__).resolve().parent == expected.resolve(), "WORKSPACE_SRC_IMPORT_REQUIRED")
    identity = provenance()
    return {"python": platform.python_version(), "local_source_sha256": identity["source_sha256"],
            "git_commit": identity["git_commit"], "scope": "LOCAL_WORKSPACE_ONLY_NOT_REMOTE_ATTESTATION"}


def _candidate_files(suite):
    for component in suite["candidate"]["components"].values():
        for filename, expected in component.get("files", {}).items():
            _require(digest(Path(filename).read_bytes()) == expected, "CANDIDATE_FILE_HASH_MISMATCH")


def _predictions(module, manifest):
    rows = read_jsonl(Path(module["predictions"]))
    golds = [g for g in manifest[module["section"]] if g["split"] == module["split"]]
    ids = [r.get("id") for r in rows]
    _require(all(isinstance(key, str) for key in ids) and len(set(ids)) == len(ids)
             and set(ids) == {g["id"] for g in golds}, "PREDICTION_IDS_MISMATCH")
    for row in rows:
        _require(type(row.get("failed", False)) is bool, "PREDICTION_FAILED_FLAG_INVALID")
        actual = row.get("snapshot", {})
        _require(isinstance(actual, dict) and all(actual.get(k) == v for k, v in manifest.get("snapshot", {}).items()),
                 "PREDICTION_SNAPSHOT_MISMATCH")
    receipt = Path(module["predictions"]).with_suffix(".collection.json")
    if receipt.is_file():
        collection = read_json(receipt)
        _require(collection.get("predictions_sha256") == digest(Path(module["predictions"]).read_bytes()),
                 "COLLECTION_PREDICTION_HASH_MISMATCH")
        _require(all(k not in collection or collection[k] == module[k] for k in ("section", "split", "review_policy")),
                 "COLLECTION_PROTOCOL_MISMATCH")
    if module["section"] == "extraction":
        from eval.scoring import score_extraction
        alignments = read_jsonl(Path(module["alignments"])) if module.get("alignments") else []
        alignment_ids = [r.get("id") for r in alignments]
        _require(all(isinstance(key, str) for key in alignment_ids) and len(set(alignment_ids)) == len(alignment_ids)
                 and set(alignment_ids) <= set(ids), "ALIGNMENT_IDS_INVALID")
        by_id = {r["id"]: r for r in rows}
        score_extraction(golds, [by_id[g["id"]] for g in golds], Path(module["dataset"]),
                         {a["id"]: a for a in alignments}, fixture=manifest.get("kind") == "fixture",
                         review_policy=module["review_policy"])
    if module.get("answer_reviews"):
        from eval.answer_reviews import load_answer_reviews
        load_answer_reviews(Path(module["answer_reviews"]), manifest, Path(module["predictions"]), golds, rows)
    return {"prediction_count": len(rows), "predictions_sha256": digest(Path(module["predictions"]).read_bytes())}


def _published(module, manifest):
    export = Path(module["collect"]["options"]["snapshot_export"])
    stamp, facts = read_json(export / "manifest.json"), read_json(export / "facts.json")
    _require(stamp.get("facts_sha256") == digest(facts), "SNAPSHOT_EXPORT_HASH_MISMATCH")
    actual = facts.get("snapshot", {})
    _require(isinstance(actual, dict) and actual == stamp.get("snapshot")
             and all(actual.get(k) == v for k, v in manifest.get("snapshot", {}).items()), "SNAPSHOT_EXPORT_IDENTITY_MISMATCH")
    # Run only the pure local projection, so absent occurrence/source IDs fail now.
    from eval.collect import PublishedCollector
    collector = PublishedCollector(facts, module["section"])
    for gold in manifest[module["section"]]:
        if gold["split"] == module["split"]:
            collector(gold, [])
    return {"facts_sha256": digest(facts)}


def _model_environment():
    _require(platform.system() == "Linux", "SHARED_LINUX_MODEL_GATE_REQUIRED")
    from interview_intelligence.config import Settings
    # Inspect only the lock path; do not load .env or print environment settings.
    path = Path(os.environ.get("MODEL_LOCK_PATH", str(Settings.model_fields["model_lock_path"].default)))
    _require(path.resolve() == Path("/app/runtime/model-call.lock"), "SHARED_LINUX_MODEL_GATE_REQUIRED")


def _http_configuration(suite, module):
    options = module["collect"]["options"]
    if options.get("http_cookie_file"):
        from eval.collect import load_http_cookies
        load_http_cookies(options["http_cookie_file"])
    if options.get("deployment_evidence"):
        from eval.runtime_identity import runtime_fingerprint, verify_runtime_evidence, verify_shared_lock_evidence
        proof = read_json(Path(options["deployment_evidence"]))
        _require(isinstance(proof, dict), "DEPLOYMENT_EVIDENCE_INVALID")
        observed = {name: proof.get(name) for name in ("api", "pi")}
        fingerprint = runtime_fingerprint(observed)
        candidate, component = suite["candidate"], suite["candidate"]["components"][module["section"]]
        evidence = {"schema": "runtime_evidence_v1", "measurement": proof,
                    "measurement_sha256": digest(proof), "observed": observed,
                    "runtime_fingerprint": fingerprint, "status": "REMOTE_SOURCE_BOUND"}
        checked = verify_runtime_evidence(evidence,
            expected_fingerprint=component.get("runtime_fingerprint", candidate.get("runtime_fingerprint")),
            expected_proof_sha256=component.get("deployment_proof_sha256", candidate.get("deployment_proof_sha256")))
        _require(checked["status"] == "REMOTE_SOURCE_BOUND", "DEPLOYMENT_EVIDENCE_INVALID")
        verify_shared_lock_evidence(proof.get("lock"), proof.get("lock"))
        _require(proof["lock"]["api"]["process"] == proof["api"]["process"], "SHARED_LOCK_API_PROCESS_MISMATCH")


def _alignment_draft(module, manifest):
    rows = read_jsonl(Path(module["alignments"]))
    ids = [row.get("id") for row in rows]
    selected = {g["id"] for g in manifest[module["section"]] if g["split"] == module["split"]}
    _require(all(isinstance(key, str) for key in ids) and len(ids) == len(set(ids)) and set(ids) <= selected,
             "ALIGNMENT_IDS_INVALID")


def preflight(suite: dict, *, allow_live=False) -> dict:
    """PASS means local static readiness, never a verified remote deployment."""
    checks, modules = [], []

    def check(name, action, success, failure):
        try:
            value = action()
        except (ValueError, OSError, ImportError, KeyError, TypeError, AttributeError, StopIteration) as error:
            code = str(error) if isinstance(error, SuiteValidationError) else failure
            checks.append({"name": name, "status": "BLOCKED", "detail": code})
            return None
        checks.append({"name": name, "status": "PASS", "detail": success})
        return value

    path = suite.get("suite_path") if isinstance(suite, dict) else None
    root = Path(path).parent if _text(path) else Path.cwd()
    normalized = check("suite", lambda: _normalize(suite, root), "SUITE_EXPLICIT_CONFIGURATION_VALID", "SUITE_CONFIGURATION_INVALID")
    if normalized is None:
        return {"status": "BLOCKED", "checks": checks, "modules": [], "runtime_verification": "NOT_PERFORMED"}
    suite = normalized
    environment = check("environment", _environment, "PYTHON_DEPENDENCIES_AND_WORKSPACE_SRC_READY",
                        "DEPENDENCY_OR_WORKSPACE_IMPORT_UNAVAILABLE")
    check("candidate.files", lambda: _candidate_files(suite), "LOCAL_FILE_HASHES_CHECKED_DECLARATION_ONLY", "CANDIDATE_FILE_UNREADABLE")
    from eval.validate_gold import validate_gold
    for module in suite["modules"]:
        key, section = module["id"], module["section"]
        adapter = module.get("collect", {}).get("adapter", "stored")
        summary = {"id": key, "section": section, "mode": adapter,
                   "runtime_verification": "PENDING" if adapter in SERVICE_ADAPTERS else "NOT_APPLICABLE",
                   "candidate_verification": "DECLARED_ONLY"}
        modules.append(summary)
        manifest = check(key + ".gold", lambda: validate_gold(Path(module["dataset"]), section,
                         split=module["split"], review_policy=module["review_policy"]),
                         "GOLD_SOURCES_FREEZE_REVIEW_AND_SPLIT_VALID", "GOLD_INVALID_CHECK_SOURCE_HASHES_REVIEW_AND_SPLIT")
        if manifest is None:
            continue
        summary.update(dataset_sha256=digest(manifest),
                       sample_count=sum(g["split"] == module["split"] for g in manifest[section]),
                       reference_kind=manifest.get("kind", "corpus"))
        if adapter != "stored" and section == "extraction":
            if Path(module["alignments"]).exists():
                check(key + ".alignment_input", lambda: _alignment_draft(module, manifest),
                      "ALIGNMENT_SYNTAX_VALID_PREDICTION_BINDING_PENDING", "ALIGNMENT_FILE_INVALID")
            checks.append({"name": key + ".alignment_review", "status": "PENDING",
                           "detail": "COLLECTED_EXTRACTION_REQUIRES_PREDICTION_BOUND_REVIEW_BEFORE_SCORING"})
            summary["alignment_verification"] = "PENDING"
        if adapter == "stored":
            info = check(key + ".predictions", lambda: _predictions(module, manifest),
                         "PREDICTION_IDS_SNAPSHOT_AND_ALIGNMENT_VALID", "PREDICTION_OR_ALIGNMENT_INVALID")
            if info:
                summary.update(info)
        elif adapter == "published":
            info = check(key + ".published", lambda: _published(module, manifest),
                         "LOCAL_PUBLISHED_FACTS_HASH_AND_IDENTITIES_VALID", "PUBLISHED_EXPORT_INVALID")
            if info:
                summary.update(info)
        else:
            if adapter == "http":
                check(key + ".http_configuration", lambda: _http_configuration(suite, module),
                      "HTTP_COOKIE_AND_DECLARED_PROOF_FORMAT_VALID_RUNTIME_PENDING", "HTTP_CONFIGURATION_INVALID")
            def snapshot_contract():
                snapshot = manifest.get("snapshot", {})
                _require(isinstance(snapshot, dict) and all(k in snapshot for k in
                         ("corpus_revision", "indexed_revision", "task_annotation_revision", "as_of")),
                         "COLLECTION_SNAPSHOT_REQUIRED")
                if adapter == "retrieval":
                    _require(snapshot["corpus_revision"] == snapshot["indexed_revision"]
                             and len(manifest.get("physical_indices", [])) == 1, "PINNED_RETRIEVAL_INDEX_REQUIRED")
            check(key + ".snapshot", snapshot_contract, "EXPECTED_RUNTIME_SNAPSHOT_DECLARED",
                  "COLLECTION_SNAPSHOT_INVALID")
            check(key + ".live_permission", lambda: _require(allow_live is True, "LIVE_COLLECTION_REQUIRES_ALLOW_LIVE"),
                  "LIVE_COLLECTION_EXPLICITLY_ENABLED", "LIVE_COLLECTION_NOT_ENABLED")
            if adapter in {"provider", "retrieval"}:
                check(key + ".shared_lock", _model_environment, "LOCAL_LINUX_SHARED_LOCK_PATH_READY",
                      "SHARED_MODEL_GATE_CONFIGURATION_UNAVAILABLE")
            checks.append({"name": key + ".runtime", "status": "PENDING",
                           "detail": "COLLECTOR_MUST_VERIFY_SNAPSHOT_ACTOR_AND_MODEL_IDENTITY_BEFORE_CALLS"})
    return {"status": "BLOCKED" if any(c["status"] == "BLOCKED" for c in checks) else "PASS",
            "scope": "LOCAL_STATIC_PREFLIGHT_ONLY", "environment": environment or {}, "checks": checks, "modules": modules,
            "runtime_verification": "PENDING" if any(m["runtime_verification"] == "PENDING" for m in modules) else "NOT_APPLICABLE"}
