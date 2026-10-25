"""Bind evaluation observations to an independently measured running deployment.

Measurements contain hashes and process metadata, never configuration values.
The operator must pin the measurement hash in the candidate through an independent
deployment inspection; a service's self-report is not an authenticity signature.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path, PurePosixPath
import re

from eval.common import digest, read_json
from eval.validate_gold import require

FILE_SCHEMA = "runtime_file_identity_v1"
PROOF_SCHEMA = "runtime_deployment_measurement_v1"
EVIDENCE_SCHEMA = "runtime_evidence_v1"
_HASH = re.compile(r"[0-9a-f]{64}")


def _files(root):
    root = Path(root).resolve()
    require(root.is_dir(), "RUNTIME_IDENTITY_DIRECTORY_MISSING")
    return (path for path in sorted(root.rglob("*")) if path.is_file()
            and not {"__pycache__", ".git", "node_modules"}.intersection(path.relative_to(root).parts)
            and path.suffix not in {".pyc", ".pyo"} and path.name != ".env"
            and not path.name.startswith(".env.") and not path.name.endswith(".private.json"))


def measure_api_identity(package_root=None, resource_roots=None):
    """Hash an actual installed package; explicit roots support independent probes.

    Canonical names are independent of checkout/site-packages installation layout.
    Resource selection mirrors resource_path: packaged files override source files.
    """
    if package_root is None:
        spec = importlib.util.find_spec("interview_intelligence")
        require(spec is not None and spec.origin is not None, "API_PACKAGE_NOT_FOUND")
        package_root = Path(spec.origin).parent
    package_root = Path(package_root).resolve()
    hashes = {}
    for path in _files(package_root):
        relative = path.relative_to(package_root)
        if relative.parts[0] == "resources":
            continue
        hashes["application/" + relative.as_posix()] = digest(path.read_bytes())
    for name in ("config", "prompts"):
        roots = ([Path(resource_roots[name])] if resource_roots and name in resource_roots else
                 [package_root.parents[1] / name, package_root / "resources" / name])
        for root in roots:
            if root.is_dir():
                for path in _files(root):
                    hashes[f"resources/{name}/" + path.relative_to(root.resolve()).as_posix()] = digest(path.read_bytes())
    require("application/api.py" in hashes and "application/config.py" in hashes,
            "API_IDENTITY_CORE_FILES_MISSING")
    require(any(key.startswith("resources/prompts/") for key in hashes), "API_PROMPT_RESOURCES_MISSING")
    return {"schema": FILE_SCHEMA, "files": hashes, "sha256": digest(hashes)}


def process_identity(pid=None):
    """Read Linux kernel process identity, excluding argv/environment secrets."""
    pid = os.getpid() if pid is None else pid
    require(type(pid) is int and pid > 0 and os.name != "nt", "LINUX_PROCESS_IDENTITY_REQUIRED")
    root = Path("/proc") / str(pid)
    stat = (root / "stat").read_text()
    # comm can contain spaces or ')'; fields after its last ')' start at field 3.
    fields = stat[stat.rfind(")") + 2:].split()
    return {"pid": pid, "start_ticks": int(fields[19]),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "executable": str((root / "exe").resolve(strict=True))}


def runtime_fingerprint(observed):
    return digest({name: observed[name]["identity"]["sha256"] for name in ("api", "pi")})


def _check_identity(identity, component):
    require(isinstance(identity, dict) and identity.get("schema") == FILE_SCHEMA, "RUNTIME_FILE_IDENTITY_SCHEMA_INVALID")
    hashes = identity.get("files")
    require(isinstance(hashes, dict) and hashes, "RUNTIME_FILE_IDENTITY_EMPTY")
    for name, value in hashes.items():
        require(isinstance(name, str) and name and "\\" not in name and ":" not in name
                and not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts,
                "RUNTIME_FILE_IDENTITY_PATH_INVALID")
        require(isinstance(value, str) and _HASH.fullmatch(value) is not None, "RUNTIME_FILE_HASH_INVALID")
    required = ("application/api.py", "application/config.py") if component == "api" else ("pi/runtime.mjs",)
    require(all(name in hashes for name in required), "RUNTIME_CORE_FILES_MISSING")
    prefixes = ("resources/prompts/",) if component == "api" else (
        "dependencies/@earendil-works/pi-agent-core/", "dependencies/@earendil-works/pi-ai/")
    require(all(any(name.startswith(prefix) for name in hashes) for prefix in prefixes), "RUNTIME_DEPENDENCIES_MISSING")
    require(identity.get("sha256") == digest(hashes), "RUNTIME_FILE_DIGEST_MISMATCH")


def _check_process(process):
    require(isinstance(process, dict) and type(process.get("pid")) is int and process["pid"] > 0
            and type(process.get("start_ticks")) is int and process["start_ticks"] > 0
            and isinstance(process.get("boot_id"), str) and process["boot_id"]
            and isinstance(process.get("executable"), str) and process["executable"].startswith("/"),
            "RUNTIME_PROCESS_IDENTITY_INVALID")


def verify_shared_lock_evidence(expected, observed):
    """Compare independent lock measurements; an arbitrary path is not proof."""
    require(isinstance(expected, dict) and isinstance(observed, dict), "SHARED_LOCK_EVIDENCE_REQUIRED")
    path = expected.get("expected_path")
    require(isinstance(path, str) and path.startswith("/") and observed.get("expected_path") == path,
            "SHARED_LOCK_PATH_MISMATCH")
    for role in ("api", "worker"):
        item = expected.get(role)
        require(isinstance(item, dict) and item.get("path") == path
                and type(item.get("device")) is int and item["device"] >= 0
                and type(item.get("inode")) is int and item["inode"] > 0,
                "SHARED_LOCK_EVIDENCE_INVALID")
        _check_process(item.get("process"))
        require(observed.get(role) == item, "SHARED_LOCK_OBSERVATION_MISMATCH")
    require((expected["api"]["device"], expected["api"]["inode"]) ==
            (expected["worker"]["device"], expected["worker"]["inode"]), "SHARED_LOCK_FILE_MISMATCH")
    return observed


def verify_runtime_evidence(evidence, *, expected_fingerprint=None, expected_proof_sha256=None):
    """Recheck file maps/process bindings; trust is anchored by the pinned proof hash."""
    try:
        require(isinstance(evidence, dict) and evidence.get("schema") == EVIDENCE_SCHEMA, "RUNTIME_EVIDENCE_MISSING")
        proof = evidence.get("measurement")
        require(isinstance(proof, dict) and proof.get("schema") == PROOF_SCHEMA
                and proof.get("method") == "independent_process_files", "INDEPENDENT_DEPLOYMENT_MEASUREMENT_REQUIRED")
        require(all(isinstance(proof.get(key), str) and proof[key].strip() for key in ("measurement_id", "measured_at")),
                "DEPLOYMENT_MEASUREMENT_ID_REQUIRED")
        proof_hash = digest(proof)
        require(evidence.get("measurement_sha256") == proof_hash, "DEPLOYMENT_MEASUREMENT_DIGEST_MISMATCH")
        if expected_proof_sha256 is not None:
            require(expected_proof_sha256 == proof_hash, "CANDIDATE_DEPLOYMENT_PROOF_MISMATCH")
        observed = evidence.get("observed")
        require(isinstance(observed, dict), "RUNTIME_OBSERVATIONS_MISSING")
        for name in ("api", "pi"):
            item = observed.get(name)
            require(isinstance(item, dict), "RUNTIME_COMPONENT_MISSING")
            _check_identity(item.get("identity"), name)
            _check_process(item.get("process"))
            require(proof.get(name) == item, f"DEPLOYMENT_{name.upper()}_MEASUREMENT_MISMATCH")
        fingerprint = runtime_fingerprint(observed)
        require(evidence.get("runtime_fingerprint") == fingerprint, "RUNTIME_FINGERPRINT_MISMATCH")
        if expected_fingerprint is not None:
            require(expected_fingerprint == fingerprint, "CANDIDATE_RUNTIME_FINGERPRINT_MISMATCH")
        require(evidence.get("status") == "REMOTE_SOURCE_BOUND", "RUNTIME_EVIDENCE_NOT_BOUND")
        return {"status": "REMOTE_SOURCE_BOUND", "errors": [], "runtime_fingerprint": fingerprint,
                "measurement_sha256": proof_hash}
    except (ValueError, KeyError, TypeError, OSError) as error:
        return {"status": "UNVERIFIED", "errors": [str(error)]}


class RuntimeIdentityGuard:
    """Freeze startup identity; independently supplied proof may arrive after start."""
    def __init__(self, proof_path, pi_observer):
        self.proof_path, self.pi_observer = Path(proof_path), pi_observer
        self.startup_api = {"identity": measure_api_identity(), "process": process_identity()}
        self.proof_sha256 = None

    def context(self):
        api = {"identity": measure_api_identity(), "process": process_identity()}
        require(api == self.startup_api, "API_RUNTIME_CHANGED_SINCE_STARTUP")
        observed = {"api": api, "pi": self.pi_observer()}
        proof = read_json(self.proof_path)
        evidence = {"schema": EVIDENCE_SCHEMA, "measurement": proof, "measurement_sha256": digest(proof),
                    "observed": observed, "runtime_fingerprint": runtime_fingerprint(observed),
                    "status": "REMOTE_SOURCE_BOUND"}
        checked = verify_runtime_evidence(evidence, expected_proof_sha256=self.proof_sha256)
        require(checked["status"] == "REMOTE_SOURCE_BOUND", "; ".join(checked["errors"]))
        self.proof_sha256 = checked["measurement_sha256"]
        return {"runtime_fingerprint": checked["runtime_fingerprint"], "runtime_evidence": evidence}


def main():
    parser = argparse.ArgumentParser(description="Read-only, credential-free installed API identity probe")
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--config-root", type=Path)
    parser.add_argument("--prompts-root", type=Path)
    parser.add_argument("--pid", type=int)
    args = parser.parse_args()
    import json
    roots = {name: value for name, value in (("config", args.config_root), ("prompts", args.prompts_root)) if value}
    print(json.dumps({"identity": measure_api_identity(args.package_root, roots), "process": process_identity(args.pid)}, sort_keys=True))


if __name__ == "__main__":
    main()
