"""Aggregate immutable module reports; missing and failing gates block release."""
import argparse
from pathlib import Path

from eval.common import SECTIONS, digest, inside, read_json, read_jsonl, write_json
from eval.report import gates
from eval.run import REPORT_SCHEMA, score_inputs, scorer_identity
from eval.runtime_identity import verify_runtime_evidence, verify_shared_lock_evidence
from eval.source_gold import release_eligibility_issues
from eval.validate_gold import require, validate_gold


def _candidate_verification(metadata, config, rows):
    """Verify observed local candidate bindings without claiming deployment proof."""
    candidate, collection = metadata.get("candidate"), config.get("collection", {})
    issues = []
    if not isinstance(candidate, dict) or not candidate:
        issues.append("CANDIDATE_DECLARATION_MISSING")
    if not isinstance(collection, dict) or collection.get("schema") != "collection_v2":
        issues.append("CANDIDATE_COLLECTION_EVIDENCE_MISSING")
    if issues:
        return {"status": "UNVERIFIED", "issues": issues, "candidate_binding_verified": False,
                "candidate_deployment_verified": False}
    remote = collection.get("adapter") == "http" and collection.get("runtime_verification") == "REMOTE_SOURCE_BOUND"
    if not remote and collection.get("runtime_verification") != "LOCAL_SOURCE_BOUND":
        issues.append("REMOTE_SOURCE_NOT_VERIFIED")
    if collection.get("adapter") == "published":
        issues.append("PUBLISHED_SNAPSHOT_IS_NOT_CANDIDATE_EXECUTION")
    if config.get("protocol", {}).get("observation_scope") == "http_end_to_end" and collection.get("adapter") != "http":
        issues.append("END_TO_END_OBSERVATIONS_MISSING")
    require(isinstance(candidate.get("components", {}), dict), "CANDIDATE_COMPONENTS_MUST_BE_A_MAPPING")
    component = candidate.get("components", {}).get(metadata["section"], {})
    require(isinstance(component, dict), "CANDIDATE_COMPONENT_MUST_BE_A_MAPPING")
    expected = component.get("runtime_fingerprint", candidate.get("runtime_fingerprint"))
    if remote:
        proof_hash = component.get("deployment_proof_sha256", candidate.get("deployment_proof_sha256"))
        if not expected or not proof_hash:
            issues.append("CANDIDATE_DEPLOYMENT_PROOF_NOT_PINNED")
        if config.get("protocol", {}).get("observation_scope") != "http_end_to_end":
            issues.append("REMOTE_OBSERVATION_SCOPE_MISMATCH")
        runtime_context = collection.get("runtime_context")
        require(isinstance(runtime_context, dict) and isinstance(runtime_context.get("observed"), dict),
                "REMOTE_RUNTIME_CONTEXT_MISSING")
        context = runtime_context["observed"]
        evidence = context.get("runtime_evidence")
        checked = verify_runtime_evidence(evidence, expected_fingerprint=expected, expected_proof_sha256=proof_hash)
        issues.extend(checked["errors"])
        if checked["status"] == "REMOTE_SOURCE_BOUND":
            try:
                lock = verify_shared_lock_evidence(evidence["measurement"].get("lock"), context.get("model_gate_identity"))
                require(lock["api"]["process"] == evidence["observed"]["api"]["process"], "LOCK_API_PROCESS_MISMATCH")
                require(context.get("runtime_fingerprint") == checked["runtime_fingerprint"], "CONTEXT_RUNTIME_FINGERPRINT_MISMATCH")
            except (ValueError, KeyError, TypeError) as error:
                issues.append(str(error))
    else:
        provenance = collection.get("provenance", {})
        require(isinstance(provenance, dict), "COLLECTION_PROVENANCE_MUST_BE_A_MAPPING")
        if not expected or provenance.get("source_sha256") != expected:
            issues.append("CANDIDATE_RUNTIME_FINGERPRINT_NOT_BOUND")
        if not isinstance(provenance.get("file_hashes"), dict) or digest(provenance["file_hashes"]) != provenance.get("source_sha256"):
            issues.append("COLLECTION_SOURCE_FINGERPRINT_INCONSISTENT")
    primary = {"extraction": "EXTRACTION", "task_labels": "TASK_ANNOTATION", "dedup": "DEDUP_JUDGE",
               "retrieval": "RERANK", "routing": "QUERY_PLAN"}.get(metadata["section"])
    calls = [call for row in rows for call in row.get("model_calls", [])]
    if any("model_calls" not in row or row.get("telemetry_unavailable") for row in rows):
        issues.append("MODEL_TELEMETRY_INCOMPLETE")
    required_models = (("embedding_model", "reranker_model", "prompt_version") if metadata["section"] == "retrieval"
                       else ("model", "prompt_version") if metadata["section"] != "sql" else ())
    for name in required_models:
        if not isinstance(component.get(name), str) or not component[name].strip():
            issues.append(f"CANDIDATE_EXPECTATION_MISSING: {name}")
    if metadata["section"] == "sql" and calls:
        issues.append("SQL_MODEL_CALLS_UNEXPECTED")
    for declaration, field, operation in (("model", "model", primary), ("prompt_version", "prompt_version", primary),
            ("model_revision", "model_revision", primary),
            ("embedding_model", "model", "EMBEDDING"), ("reranker_model", "model", "RERANK"),
            ("embedding_model_revision", "model_revision", "EMBEDDING"),
            ("reranker_model_revision", "model_revision", "RERANK")):
        if component.get(declaration) is not None:
            relevant = [call for call in calls if call.get("operation_type") == operation]
            if not relevant or any(call.get(field) != component[declaration] for call in relevant):
                issues.append(f"CANDIDATE_OBSERVATION_NOT_BOUND: {declaration}")
    return {"status": ("BOUND_REMOTE_SOURCE" if remote else "BOUND_LOCAL_SOURCE") if not issues else "UNVERIFIED", "issues": issues,
            "candidate_binding_verified": not issues, "candidate_deployment_verified": remote and not issues,
            "deployment_scope": "independently_measured_evaluation_processes" if remote and not issues else None}


def verify_report(path, *, expected_candidate=None, strict=True):
    """Recompute sealed reports offline; legacy inspection never claims verification."""
    folder = Path(path)
    try:
        metadata = read_json(folder / "manifest.json")
        config = read_json(folder / "config.json")
        stored_gate = read_json(folder / "gates.json")
        require(all(isinstance(value, dict) for value in (metadata, config, stored_gate)), "REPORT_OBJECTS_MUST_BE_MAPPINGS")
        require("collection" not in config or isinstance(config["collection"], dict), "COLLECTION_RECEIPT_MUST_BE_A_MAPPING")
        seal_path = folder / "integrity.json"
        if not seal_path.is_file():
            require(not strict, "LEGACY_REPORT_REQUIRES_EXPLICIT_REPLAY")
            return {"status": "LEGACY_UNVERIFIED", "errors": [], "strict_verified": False,
                    "gate": stored_gate, "metadata": metadata, "config": config,
                    "candidate": metadata.get("candidate"), "candidate_deployment_verified": False}
        seal = read_json(seal_path)
        require(isinstance(seal, dict), "REPORT_SEAL_MUST_BE_A_MAPPING")
        require(seal.get("schema") == REPORT_SCHEMA == metadata.get("report_schema"), "REPORT_SCHEMA_MISMATCH")
        artifacts = seal.get("artifacts")
        required = {"manifest.json", "config.json", "metrics.json", "gates.json", "per_sample.jsonl",
                    "predictions.jsonl", "gold/manifest.json", "gold/freeze.json"}
        require(isinstance(artifacts, dict) and required <= artifacts.keys(), "REPORT_ARTIFACT_BINDINGS_MISSING")
        for name, expected in artifacts.items():
            file = inside(folder, name)
            require(file.is_file() and digest(file.read_bytes()) == expected, f"REPORT_ARTIFACT_HASH_MISMATCH: {name}")
        scoring = seal["scoring"]
        require(isinstance(scoring, dict), "SCORING_PROTOCOL_MUST_BE_A_MAPPING")
        require(isinstance(scoring.get("protocol"), dict), "REPORT_PROTOCOL_MUST_BE_A_MAPPING")
        require(scoring == metadata.get("scoring") == config.get("scoring"), "SCORING_PROTOCOL_BINDING_MISMATCH")
        require(scoring.get("protocol") == config.get("protocol"), "PROTOCOL_BINDING_MISMATCH")
        require(seal.get("candidate") == metadata.get("candidate") == config.get("candidate"), "CANDIDATE_BINDING_MISMATCH")
        require(seal.get("suite") == metadata.get("suite") == config.get("suite"), "SUITE_BINDING_MISMATCH")
        if expected_candidate is not None:
            require(seal.get("candidate") == expected_candidate, "CANDIDATE_MISMATCH")
        for key in ("section", "split", "review_policy"):
            require(scoring.get(key) == metadata.get(key), f"SCORING_METADATA_MISMATCH: {key}")
        for key in ("default_pipeline", "threshold_version", "review_policy"):
            require(scoring.get(key) == config.get(key), f"SCORING_CONFIG_MISMATCH: {key}")
        require(metadata.get("snapshot") == seal.get("snapshot") == config.get("snapshot"), "REPORT_SNAPSHOT_MISMATCH")
        require(digest((folder / "predictions.jsonl").read_bytes()) == metadata.get("predictions_sha256"), "PREDICTIONS_HASH_MISMATCH")
        alignment = folder / "alignments.jsonl"
        if metadata.get("alignments_sha256") is not None:
            require("alignments.jsonl" in artifacts and alignment.is_file(), "ALIGNMENT_BINDING_MISSING")
            require(digest(alignment.read_bytes()) == metadata["alignments_sha256"], "ALIGNMENT_HASH_MISMATCH")
        else:
            require(not alignment.exists(), "UNBOUND_ALIGNMENT")
        answer_reviews = folder / "answer_reviews.json"
        if metadata.get("answer_reviews_sha256") is not None:
            require("answer_reviews.json" in artifacts and answer_reviews.is_file(), "ANSWER_REVIEW_BINDING_MISSING")
            require(digest(answer_reviews.read_bytes()) == metadata["answer_reviews_sha256"], "ANSWER_REVIEW_HASH_MISMATCH")
        else:
            require(not answer_reviews.exists(), "UNBOUND_ANSWER_REVIEWS")
        collection_path = folder / "collection.json"
        if collection_path.exists() or config.get("collection") is not None:
            require("collection.json" in artifacts and collection_path.is_file(), "COLLECTION_BINDING_MISSING")
            collection = read_json(collection_path)
            require(isinstance(collection, dict), "COLLECTION_RECEIPT_MUST_BE_A_MAPPING")
            require(collection == config.get("collection"), "COLLECTION_RECEIPT_MISMATCH")
            require(collection.get("predictions_sha256") == metadata["predictions_sha256"], "COLLECTION_PREDICTIONS_MISMATCH")
        if not strict:
            return {"status": "LEGACY_UNVERIFIED", "errors": [], "strict_verified": False,
                    "gate": stored_gate, "metadata": metadata, "config": config,
                    "candidate": seal.get("candidate"), "candidate_deployment_verified": False}
        require(scoring.get("scorer") == scorer_identity(), "SCORER_VERSION_MISMATCH")
        derived = score_inputs(folder / "gold", scoring["section"], folder / "predictions.jsonl",
                               split=scoring["split"], review_policy=scoring["review_policy"],
                               default_pipeline=scoring["default_pipeline"], threshold_version=scoring["threshold_version"],
                               alignments=alignment if alignment.is_file() else None,
                               answer_reviews=answer_reviews if answer_reviews.is_file() else None)
        gold = derived["gold"]
        require(digest(gold) == metadata.get("dataset_sha256") == seal.get("dataset_sha256"), "DATASET_HASH_MISMATCH")
        require(gold.get("snapshot", {}) == metadata["snapshot"], "GOLD_SNAPSHOT_MISMATCH")
        require(gold["version"] == metadata.get("dataset_version") and gold.get("kind", "corpus") == metadata.get("kind"),
                "DATASET_METADATA_MISMATCH")
        require(gold.get("source_evidence_contract") == metadata.get("source_evidence_contract")
                and gold.get("retrieval_evidence_scope") == metadata.get("reference_scope"), "REFERENCE_SCOPE_MISMATCH")
        require(config.get("models") == gold.get("models", {}) and config.get("annotation_guide") == gold.get("annotation_guide"),
                "GOLD_CONFIG_MISMATCH")
        observed_models = sorted({(c.get("operation_type"), c.get("model"), c.get("model_revision"), c.get("prompt_version"))
            for row in derived["rows"] for c in row.get("model_calls", [])}, key=repr)
        require(digest(observed_models) == digest(config.get("observed_models")), "OBSERVED_MODELS_MISMATCH")
        if config.get("collection", {}).get("schema") == "collection_v2":
            collection = config["collection"]
            for key in ("section", "split", "review_policy"):
                require(collection.get(key) == scoring[key], f"COLLECTION_PROTOCOL_MISMATCH: {key}")
            require(collection.get("dataset_sha256") == digest(gold), "COLLECTION_DATASET_MISMATCH")
            require(collection.get("selection_sha256") == digest(derived["golds"]), "COLLECTION_SELECTION_MISMATCH")
            require(collection.get("snapshot") == metadata["snapshot"], "COLLECTION_SNAPSHOT_MISMATCH")
            require(collection.get("samples") == len(derived["rows"])
                    and collection.get("failed") == sum(bool(row.get("failed")) for row in derived["rows"]), "COLLECTION_SAMPLE_COUNTS_MISMATCH")
            require(collection.get("candidate_binding") == seal.get("candidate"), "COLLECTION_CANDIDATE_MISMATCH")
            require(collection.get("candidate_sha256") == (digest(seal["candidate"]) if seal.get("candidate") is not None else None),
                    "COLLECTION_CANDIDATE_HASH_MISMATCH")
        counts = {"sample_count": len(derived["golds"]),
                  "test_count": len(derived["golds"]) if scoring["split"] == "test" else 0,
                  "human_verified_count": derived["verified"], "agent_verified_count": derived["agent_verified"]}
        require(all(metadata.get(key) == value for key, value in counts.items()), "REVIEW_ELIGIBILITY_MISMATCH")
        require(digest(derived["metrics"]) == digest(read_json(folder / "metrics.json")), "METRICS_RECOMPUTATION_MISMATCH")
        require(digest(derived["scored"]) == digest(read_jsonl(folder / "per_sample.jsonl")), "SAMPLES_RECOMPUTATION_MISMATCH")
        require(digest(derived["gate"]) == digest(stored_gate), "GATE_RECOMPUTATION_MISMATCH")
        candidate_verification = _candidate_verification(metadata, config, derived["rows"])
        return {"status": "VERIFIED", "errors": [], "strict_verified": True, "gate": derived["gate"],
                "metadata": metadata, "config": config, "candidate": seal.get("candidate"),
                "candidate_verification": candidate_verification,
                "candidate_binding_verified": candidate_verification["candidate_binding_verified"],
                "candidate_deployment_verified": candidate_verification["candidate_deployment_verified"],
                "scope": "Sealed inputs reproduce this report; deployment binding covers only independently measured evaluation processes."}
    except (ValueError, KeyError, TypeError, OSError) as error:
        return {"status": "INVALID", "errors": [str(error)], "strict_verified": False,
                "candidate_deployment_verified": False}


def check_bundle(bundle, root, *, strict=True):
    entries=bundle.get("modules",{})
    require(set(entries)<=set(SECTIONS),"UNKNOWN_MODULE")
    modules={};reference=None
    expected_candidate = bundle.get("candidate")

    def check_report(section, entry):
        nonlocal reference
        try:
            require(section in SECTIONS,"UNKNOWN_MODULE")
            require(isinstance(entry,dict),"REPORT_ENTRY_MUST_BE_A_MAPPING")
            folder=inside(root,entry["report"])
            verification = verify_report(folder, expected_candidate=expected_candidate, strict=strict)
            require(verification["status"] != "INVALID", "; ".join(verification["errors"]))
            metadata=read_json(folder/"manifest.json")
            require(metadata["section"]==section and metadata["split"]=="test" and metadata["kind"]!="fixture","NON_RELEASE_REPORT")
            policy=metadata.get("review_policy","human")
            gold=validate_gold(inside(root,entry["dataset"]),section,review_policy=policy)
            require(digest(gold)==metadata["dataset_sha256"],"DATASET_HASH_MISMATCH")
            require(digest((folder/"predictions.jsonl").read_bytes())==metadata["predictions_sha256"],"PREDICTIONS_HASH_MISMATCH")
            require(gold["snapshot"]==metadata["snapshot"],"REPORT_SNAPSHOT_MISMATCH")
            if reference is None:reference=metadata["snapshot"]
            require(metadata["snapshot"]==reference,"BUNDLE_SNAPSHOT_MISMATCH")
            selected=[g for g in gold[section] if g["split"]=="test"]
            flag="agent_verified" if policy=="delegated_agent" else "human_verified"
            require(len(selected)==metadata["sample_count"] and all(g.get(flag) is True for g in selected),"REVIEW_ELIGIBILITY_MISMATCH")
            config=read_json(folder/"config.json")
            stored=read_json(folder/"gates.json")
            threshold_version=config.get("threshold_version","quality_gate_v2")
            require(not bundle.get("threshold_version") or threshold_version==bundle["threshold_version"],
                    "BUNDLE_THRESHOLD_VERSION_MISMATCH")
            eligibility_issues=release_eligibility_issues(gold,section)
            derived=gates(section,read_json(folder/"metrics.json"),eligible=not eligibility_issues,
                default_pipeline=config.get("default_pipeline","HYBRID"), threshold_version=threshold_version)
            require(all(stored.get(k)==derived[k] for k in ("status","checks","threshold_version")),"GATE_REPORT_MISMATCH")
            if strict:
                expected_policy = entry.get("review_policy", bundle.get("review_policy"))
                require(expected_policy is None or policy == expected_policy, "BUNDLE_REVIEW_POLICY_MISMATCH")
                candidate_issues = list(verification["candidate_verification"]["issues"])
                if not isinstance(expected_candidate, dict) or not expected_candidate:
                    candidate_issues.append("BUNDLE_CANDIDATE_REQUIRED")
            else:
                candidate_issues = []
            status = "NOT_ELIGIBLE" if strict and derived["status"] == "PASSED" and candidate_issues else derived["status"]
            return {"status":status,"quality_gate_status":derived["status"],"checks":derived["checks"],"samples":len(selected),
                **({"eligibility_issues":eligibility_issues} if eligibility_issues else {}),
                "review_policy":policy,"dataset":entry["dataset"],"report":entry["report"],
                "dataset_sha256":metadata["dataset_sha256"],"predictions_sha256":metadata["predictions_sha256"],
                "verification": verification["status"], "strict_verified": verification["strict_verified"],
                "candidate": metadata.get("candidate"), "candidate_issues": candidate_issues,
                "candidate_binding_verified": strict and not candidate_issues,
                "candidate_deployment_verified": strict and verification.get("candidate_deployment_verified", False)}
        except (ValueError,KeyError,TypeError,OSError) as error:
            return {"status":"INVALID","error":str(error)}

    for section in SECTIONS:
        modules[section]=check_report(section,entries[section]) if section in entries else {"status":"MISSING"}
    required=bundle.get("required_regressions",{})
    require(isinstance(required,dict),"REQUIRED_REGRESSIONS_MUST_BE_A_MAPPING")
    regressions={name:check_report(entry.get("section") if isinstance(entry,dict) else None,entry)
                 for name,entry in required.items()}
    blockers=[section for section,value in modules.items() if value["status"]!="PASSED"]
    blockers += ["regression:"+name for name,value in regressions.items() if value["status"]!="PASSED"]
    return {"schema":"evaluation_release_gate_v1","status":"BLOCKED" if blockers else "PASSED" if strict else "LEGACY_UNVERIFIED",
        "snapshot":reference,"modules":modules,"required_regressions":regressions,"blockers":blockers,
        "strict_verified": strict and not blockers, "historical_only": not strict,
        "scope":"Frozen offline module gates only. Passing does not certify unseen-traffic performance or production capacity."}


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--bundle",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True);p.add_argument("--root",type=Path,default=Path("."))
    p.add_argument("--legacy", action="store_true", help="inspect historical reports without claiming strict release verification")
    args=p.parse_args();result=check_bundle(read_json(args.bundle),args.root,strict=not args.legacy)
    write_json(args.output,result);print({"status":result["status"],"blockers":result["blockers"]})
    raise SystemExit(0 if result["status"]=="PASSED" else 1)
