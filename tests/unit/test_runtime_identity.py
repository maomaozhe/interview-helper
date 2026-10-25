"""Offline deployment-measurement, process and remote gate binding tests."""
from copy import deepcopy

import pytest

from eval.common import digest, write_json
from eval.release import _candidate_verification
from eval.runtime_identity import (EVIDENCE_SCHEMA, FILE_SCHEMA, PROOF_SCHEMA, RuntimeIdentityGuard,
                                   measure_api_identity, runtime_fingerprint, verify_runtime_evidence)


def measured_evidence():
    def identity(files):
        hashes = {name: digest(value.encode()) for name, value in files.items()}
        return {"schema": FILE_SCHEMA, "files": hashes, "sha256": digest(hashes)}
    def process(pid):
        return {"pid": pid, "start_ticks": 100, "boot_id": "boot-a", "executable": "/usr/bin/runtime"}
    observed = {"api": {"identity": identity({"application/api.py": "api", "application/config.py": "config",
        "resources/prompts/query_agent_v13.md": "prompt"}), "process": process(10)},
        "pi": {"identity": identity({"pi/runtime.mjs": "runtime",
            "dependencies/@earendil-works/pi-agent-core/index.js": "agent",
            "dependencies/@earendil-works/pi-ai/index.js": "ai"}), "process": process(20)}}
    lock = {"expected_path": "/deployment/state/run/model-call.lock"}
    for role, pid in (("api", 10), ("worker", 30)):
        lock[role] = {"path": lock["expected_path"], "device": 12, "inode": 34, "process": process(pid)}
    measurement = {"schema": PROOF_SCHEMA, "method": "independent_process_files", "measurement_id": "ssh-probe-a",
                   "measured_at": "2026-10-15T00:00:00+00:00", **deepcopy(observed), "lock": lock}
    return {"schema": EVIDENCE_SCHEMA, "measurement": measurement, "measurement_sha256": digest(measurement),
            "observed": observed, "runtime_fingerprint": runtime_fingerprint(observed), "status": "REMOTE_SOURCE_BOUND"}


def remote_candidate(evidence):
    candidate = {"id": "candidate-a", "runtime_fingerprint": evidence["runtime_fingerprint"],
        "deployment_proof_sha256": evidence["measurement_sha256"],
        "components": {"routing": {"version": "query-v13", "model": "model-a", "prompt_version": "query-v13"}}}
    config = {"protocol": {"observation_scope": "http_end_to_end"}, "collection": {
        "schema": "collection_v2", "adapter": "http", "runtime_verification": "REMOTE_SOURCE_BOUND",
        "runtime_context": {"observed": {"runtime_evidence": evidence,
            "runtime_fingerprint": evidence["runtime_fingerprint"], "model_gate_identity": deepcopy(evidence["measurement"]["lock"])}}}}
    rows = [{"model_calls": [{"operation_type": "QUERY_PLAN", "model": "model-a", "prompt_version": "query-v13"}]}]
    return {"candidate": candidate, "section": "routing"}, config, rows


def test_identity_is_layout_independent_and_uses_packaged_resource_precedence(tmp_path):
    source = tmp_path / "source" / "src" / "interview_intelligence"
    installed = tmp_path / "installed" / "interview_intelligence"
    for root in (source, installed):
        root.mkdir(parents=True)
        (root / "api.py").write_text("api")
        (root / "config.py").write_text("config")
    (tmp_path / "source" / "prompts").mkdir()
    (tmp_path / "source" / "prompts" / "query.md").write_text("prompt")
    (installed / "resources" / "prompts").mkdir(parents=True)
    (installed / "resources" / "prompts" / "query.md").write_text("prompt")
    assert measure_api_identity(source) == measure_api_identity(installed)
    # Bytecode is irrelevant, but a changed prompt changes the runtime identity.
    (installed / "api.pyc").write_bytes(b"generated")
    assert measure_api_identity(source) == measure_api_identity(installed)
    (installed / "resources" / "prompts" / "query.md").write_text("changed")
    assert measure_api_identity(source)["sha256"] != measure_api_identity(installed)["sha256"]


@pytest.mark.parametrize("change", ["process", "source", "digest", "missing_dependency", "proof"])
def test_remote_evidence_rejects_source_process_and_proof_changes(change):
    evidence = measured_evidence()
    pinned = evidence["measurement_sha256"]
    assert verify_runtime_evidence(evidence, expected_proof_sha256=pinned)["status"] == "REMOTE_SOURCE_BOUND"
    if change == "process":
        evidence["observed"]["api"]["process"]["start_ticks"] += 1
    elif change == "source":
        identity = evidence["observed"]["api"]["identity"]
        identity["files"]["application/api.py"] = digest(b"other code")
        identity["sha256"] = digest(identity["files"])
    elif change == "digest":
        evidence["observed"]["api"]["identity"]["sha256"] = "0" * 64
    elif change == "missing_dependency":
        identity = evidence["observed"]["pi"]["identity"]
        del identity["files"]["dependencies/@earendil-works/pi-ai/index.js"]
        identity["sha256"] = digest(identity["files"])
    else:
        evidence["measurement"]["measurement_id"] = "self-reported-replacement"
        evidence["measurement_sha256"] = digest(evidence["measurement"])
    assert verify_runtime_evidence(evidence, expected_proof_sha256=pinned)["status"] == "UNVERIFIED"


def test_runtime_guard_rechecks_files_and_pins_the_independent_proof(tmp_path, monkeypatch):
    evidence = measured_evidence()
    current = deepcopy(evidence["observed"])
    monkeypatch.setattr("eval.runtime_identity.measure_api_identity", lambda: deepcopy(current["api"]["identity"]))
    monkeypatch.setattr("eval.runtime_identity.process_identity", lambda: deepcopy(current["api"]["process"]))
    proof = tmp_path / "proof.json"
    guard = RuntimeIdentityGuard(proof, lambda: deepcopy(current["pi"]))
    write_json(proof, evidence["measurement"])
    assert guard.context()["runtime_fingerprint"] == evidence["runtime_fingerprint"]
    current["api"]["identity"]["files"]["application/api.py"] = digest(b"modified")
    with pytest.raises(ValueError, match="API_RUNTIME_CHANGED_SINCE_STARTUP"):
        guard.context()
    current = deepcopy(evidence["observed"])
    evidence["measurement"]["measurement_id"] = "replaced-after-start"
    write_json(proof, evidence["measurement"])
    with pytest.raises(ValueError, match="CANDIDATE_DEPLOYMENT_PROOF_MISMATCH"):
        guard.context()


def test_remote_gate_requires_independent_candidate_pin_and_matching_shared_lock():
    evidence = measured_evidence()
    metadata, config, rows = remote_candidate(evidence)
    result = _candidate_verification(metadata, config, rows)
    assert result["status"] == "BOUND_REMOTE_SOURCE"
    assert result["candidate_binding_verified"] and result["candidate_deployment_verified"]
    del metadata["candidate"]["deployment_proof_sha256"]
    assert "CANDIDATE_DEPLOYMENT_PROOF_NOT_PINNED" in _candidate_verification(metadata, config, rows)["issues"]
    metadata["candidate"]["deployment_proof_sha256"] = evidence["measurement_sha256"]
    config["collection"]["runtime_context"]["observed"]["model_gate_identity"]["worker"]["inode"] = 99
    assert "SHARED_LOCK_OBSERVATION_MISMATCH" in _candidate_verification(metadata, config, rows)["issues"]


def test_remote_gate_never_accepts_declared_local_provenance_as_http_proof():
    evidence = measured_evidence()
    metadata, config, rows = remote_candidate(evidence)
    config["collection"]["runtime_context"]["observed"].pop("runtime_evidence")
    result = _candidate_verification(metadata, config, rows)
    assert not result["candidate_deployment_verified"]
    assert "RUNTIME_EVIDENCE_MISSING" in result["issues"]
