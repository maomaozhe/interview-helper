"""Host-authorized complete engineering candidate identities, without storage.

These contracts qualify a universe and a bounded processing window. They do not
classify that universe as business applications or authorize a generic SEARCH
to acquire the engineering collection's hard scope.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from interview_intelligence.relevance_scope import validate_relevance_scope
from interview_intelligence.search.catalog_selector import canonical_digest

WORKSET_VERSION = "engineering_candidate_workset_v1"
QUALIFICATION_VERSION = "engineering_workset_qualification_v1"
AUTHORIZATION_VERSION = "engineering_collection_authorization_v1"
WORKSET_BATCH_SIZE = 20
WORKSET_SOURCE_POLICY_VERSION = "engineering_workset_source_proof_v4"
WORKSET_BATCH_POLICY_VERSION = "engineering_workset_adaptive_prefix_v1"
INDEX_SCHEMA_VERSION = "search_v1_jieba_whitespace"


def require(condition, code="INVALID_ENGINEERING_WORKSET"):
    if not condition:
        raise ValueError(code)


def ids_digest(identities):
    return hashlib.sha256(json.dumps(sorted(identities), separators=(",", ":")).encode()).hexdigest()


def independent_negative_adjudication_valid(proof, scope_hash):
    """Require a host-produced independent rejection for this exact scope.

    Source coverage and semantic negative proofs are checked by the caller.
    A primary label or the older source_verified_negative flag alone cannot
    establish that independent adjudication actually completed.
    """
    from interview_intelligence.search.business_scope import CLARIFIED_ADJUDICATION_CONTRACT

    return (type(proof) is dict and type(scope_hash) is str
        and proof.get("scope_adjudication_contract") == CLARIFIED_ADJUDICATION_CONTRACT
        and proof.get("scope_adjudication_status") == "VERIFIED"
        and proof.get("scope_verification_status") == "SCOPE_REJECTED"
        and proof.get("relevance_scope_hash") == scope_hash)


def workset_digest(manifest):
    return canonical_digest({key: value for key, value in manifest.items() if key != "manifest_hash"})


def validate_engineering_authorization(authorization, scope, filters):
    from interview_intelligence.agent.query_scope import engineering_all_source_messages

    scope = validate_relevance_scope(scope)
    require(scope["target_unit"] == "BUSINESS_APPLICATION" and scope["retrieval_mode"] == "CANDIDATES",
        "ENGINEERING_WORKSET_NOT_AUTHORIZED")
    require(type(authorization) is dict and set(authorization) == {
        "version", "semantic_task", "source", "source_messages", "source_evidence", "scope_hash", "filters"},
        "ENGINEERING_WORKSET_NOT_AUTHORIZED")
    messages = authorization["source_messages"]
    require(authorization["version"] == AUTHORIZATION_VERSION and authorization["semantic_task"] == "ENGINEERING_DESIGN"
        and authorization["source"] == "original_user_collection_request"
        and authorization["scope_hash"] == scope["scope_hash"]
        and authorization["filters"] == filters.model_dump(mode="json")
        and type(messages) is list and 1 <= len(messages) <= 32
        and all(type(message) is str and 1 <= len(message) <= 4096 for message in messages),
        "ENGINEERING_WORKSET_NOT_AUTHORIZED")
    evidence = authorization["source_evidence"]
    require(type(evidence) is list and len(evidence) == len(messages), "ENGINEERING_WORKSET_NOT_AUTHORIZED")
    for index, (message, binding) in enumerate(zip(messages, evidence)):
        require(type(binding) is dict and set(binding) == {
            "message_index", "quote", "start_char", "end_char", "input_sha256"}
            and type(binding["message_index"]) is int and binding["message_index"] == index
            and binding["quote"] == message and type(binding["start_char"]) is int and binding["start_char"] == 0
            and type(binding["end_char"]) is int and binding["end_char"] == len(message)
            and binding["input_sha256"] == hashlib.sha256(message.encode()).hexdigest(), "ENGINEERING_WORKSET_NOT_AUTHORIZED")
    require(bool(engineering_all_source_messages(messages, filters)), "ENGINEERING_WORKSET_NOT_AUTHORIZED")
    return deepcopy(authorization)


def validate_manifest(manifest, scope, filters):
    scope = validate_relevance_scope(scope)
    require(type(manifest) is dict and set(manifest) == {"version", "candidate_ids", "candidate_ids_hash", "order_hash",
        "filters", "relevance_scope_hash", "authorization", "qualification", "revisions", "index_schema_version",
        "embedding_version", "proof_policy", "batch_size", "query_hash", "manifest_hash"})
    identities = manifest["candidate_ids"]
    require(manifest["version"] == WORKSET_VERSION and type(identities) is list and len(identities) <= 50000
        and all(type(identity) is str and identity for identity in identities) and len(set(identities)) == len(identities)
        and manifest["candidate_ids_hash"] == ids_digest(identities)
        and manifest["order_hash"] == canonical_digest(identities)
        and manifest["filters"] == filters.model_dump(mode="json") and manifest["relevance_scope_hash"] == scope["scope_hash"]
        and type(manifest["batch_size"]) is int and manifest["batch_size"] == WORKSET_BATCH_SIZE
        and manifest["index_schema_version"] == INDEX_SCHEMA_VERSION
        and (manifest["embedding_version"] is None or type(manifest["embedding_version"]) is str))
    validate_engineering_authorization(manifest["authorization"], scope, filters)
    qualification = manifest["qualification"]
    require(type(qualification) is dict and set(qualification) == {"version", "task", "semantic_version", "semantic_scope_hash",
        "count", "ids_hash", "catalog_body_hash", "source_bindings_hash", "coverage"}
        and qualification["version"] == QUALIFICATION_VERSION and qualification["task"] == "ENGINEERING_DESIGN"
        and type(qualification["count"]) is int and qualification["count"] == len(identities)
        and qualification["ids_hash"] == ids_digest(identities) and type(qualification["coverage"]) is dict)
    revisions = manifest["revisions"]
    require(type(revisions) is dict and set(revisions) == {
        "corpus_revision", "indexed_revision", "canonical_revision", "task_annotation_revision"}
        and all(type(value) is int and value >= 0 for value in revisions.values()))
    policy = manifest["proof_policy"]
    require(type(policy) is dict and set(policy) == {"reranker_version", "scope_verifier_version", "business_scope_contract", "workset_source_policy_version", "workset_batch_policy_version",
        "reranker_prompt_hash", "scope_verifier_prompt_hash"}
        and policy["workset_source_policy_version"] == WORKSET_SOURCE_POLICY_VERSION
        and policy["workset_batch_policy_version"] == WORKSET_BATCH_POLICY_VERSION
        and all(value is None or type(value) is str and value for value in policy.values()))
    for value in (manifest["query_hash"], qualification["semantic_scope_hash"], qualification["catalog_body_hash"],
            qualification["source_bindings_hash"], manifest["manifest_hash"]):
        require(type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value))
    require(manifest["manifest_hash"] == workset_digest(manifest))
    return deepcopy(manifest)


def validate_batch_ids(manifest, batch_ids):
    require(type(batch_ids) is list and 1 <= len(batch_ids) <= WORKSET_BATCH_SIZE
        and all(type(identity) is str and identity in manifest["candidate_ids"] for identity in batch_ids)
        and len(set(batch_ids)) == len(batch_ids), "INVALID_ENGINEERING_WORKSET_BATCH")
    return list(batch_ids)
