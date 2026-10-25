"""SQL eligibility and exact facts around a replaceable retriever."""

from __future__ import annotations

import math
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from interview_intelligence.analytics.detail import get_question_detail
from interview_intelligence.analytics.stats import _active_from, _conditions
from interview_intelligence.analytics.semantic_scope import load_semantic_scope
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusState, QuestionOccurrence, OccurrenceTaskAnnotation, SourceRevision,
    DocumentBuild, Interview, SourceDocument, QuestionRelation,
)
from interview_intelligence.relevance_scope import validate_relevance_scope
from interview_intelligence.providers.runtime import check_model_call_deadline
from interview_intelligence.search.catalog_selector import (
    CatalogSoftSelector, CATALOG_AUDIT_VERSION, CATALOG_SELECTOR_VERSION, catalog_audit_digest, canonical_digest,
)
from interview_intelligence.search.workset import (
    WORKSET_VERSION, QUALIFICATION_VERSION, WORKSET_BATCH_SIZE, INDEX_SCHEMA_VERSION,
    ids_digest, independent_negative_adjudication_valid, workset_digest,
    validate_engineering_authorization, validate_manifest, validate_batch_ids,
)

RETRIEVAL_RANK_AUDIT_VERSION = "bounded_retrieval_rank_audit_v1"
RETRIEVAL_RANK_LIMIT = 200
RETRIEVAL_STAGE_LIMIT = 100


def rrf(*rankings: list[tuple[str, float]], k: int = 60, weights: tuple[float, ...] | None = None) -> list[dict]:
    weights = tuple(1.0 for _ in rankings) if weights is None else weights
    if (len(weights) != len(rankings) or any(isinstance(weight, bool) or not isinstance(weight, (int, float))
            or not math.isfinite(weight) or weight < 0 for weight in weights)
            or rankings and not any(weight > 0 for weight in weights)):
        raise ValueError("invalid RRF weights")
    scores: dict[str, float] = {}
    stage_scores: dict[str, dict] = {}
    for index, ranking in enumerate(rankings):
        for rank, (canonical_id, score) in enumerate(ranking, 1):
            scores[canonical_id] = scores.get(canonical_id, 0.0) + weights[index] / (k + rank)
            stage_scores.setdefault(canonical_id, {})[f"stage_{index + 1}"] = score
    return [
        {"canonical_question_id": canonical_id, "rrf_score": score,
         "stage_scores": stage_scores[canonical_id]}
        for canonical_id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    ]


def eligible_canonical_ids(session: Session, filters: FilterSpec) -> list[str]:
    ids = list(session.scalars(
        select(QuestionOccurrence.canonical_question_id)
        .select_from(_active_from()).where(*_conditions(filters)).distinct()
    ))
    if len(ids) > 50_000:
        raise ValueError("FILTER_CAPACITY_EXCEEDED")
    return sorted(canonical_id for canonical_id in ids if canonical_id)


def target_eligibility_digest(identities: list[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(identities), separators=(",", ":")).encode()).hexdigest()


def target_catalog_contract(session, target_ids, prior):
    """Bind the ES catalog's titles to SQL without promoting the soft prior."""
    titles = dict(session.execute(select(CanonicalQuestion.id, CanonicalQuestion.canonical_text)
        .where(CanonicalQuestion.id.in_(target_ids))).all())
    if set(titles) != set(target_ids):
        raise ValueError("COMPLETE_CATALOG_SQL_MISMATCH")
    entries = [{"canonical_question_id": identity,
        "text_hash": hashlib.sha256(titles[identity].encode()).hexdigest()} for identity in sorted(target_ids)]
    return {"version": "sql_canonical_catalog_v1", "count": len(entries),
        "ids_hash": target_eligibility_digest(target_ids), "body_hash": canonical_digest(entries),
        "corpus_revision": prior.get("corpus_revision"), "entries": entries}


def _validate_catalog_selection_audit(audit, scope, eligible, target_ids, contract, candidate_ids):
    def require(condition):
        if not condition:
            raise ValueError("RELEVANCE_SCOPE_NOT_APPLIED")
    require(scope is not None and scope["target_unit"] == "BUSINESS_APPLICATION"
        and scope["retrieval_mode"] == "CANDIDATES" and isinstance(contract, dict))
    require(isinstance(audit, dict) and set(audit) == {"version", "selector_version", "scope_hash",
        "requested", "available", "attempted", "applied", "status", "reason", "catalog", "prepared",
        "provider", "provider_admitted", "request_budget", "reserved_ids", "selected_ids", "fallback", "candidate_ids_hash", "audit_hash"})
    require(audit["version"] == CATALOG_AUDIT_VERSION and audit["selector_version"] == CATALOG_SELECTOR_VERSION
        and audit["scope_hash"] == scope["scope_hash"] and audit["requested"] is True)
    require(all(type(audit[key]) is bool for key in ("available", "attempted", "provider_admitted", "applied")))
    require(audit["status"] in {"COMPLETED", "FAILED", "UNAVAILABLE"}
        and audit["reason"] in {None, "empty_new_catalog", "selector_unavailable", "catalog_projection_unavailable",
            "catalog_input_capacity", "catalog_preparation_failed", "request_budget", "profile_budget", "screen_failed"})
    catalog = audit["catalog"]
    require(isinstance(catalog, dict) and set(catalog) == {"expected_count", "enumerated_count", "complete",
        "ids_hash", "body_hash", "corpus_revision", "index_schema_version", "title_utf8_bytes"})
    require(type(catalog["complete"]) is bool and type(catalog["enumerated_count"]) is int
        and 0 <= catalog["enumerated_count"] <= len(target_ids)
        and type(catalog["expected_count"]) is int and catalog["expected_count"] == len(target_ids)
        and catalog["ids_hash"] == contract["ids_hash"] and catalog["body_hash"] == contract["body_hash"]
        and catalog["corpus_revision"] == contract["corpus_revision"]
        and (catalog["corpus_revision"] is None or type(catalog["corpus_revision"]) is int and catalog["corpus_revision"] >= 0)
        and catalog["index_schema_version"] == "search_v1_jieba_whitespace"
        and type(catalog["title_utf8_bytes"]) is int and catalog["title_utf8_bytes"] >= 0)
    if catalog["complete"]:
        require(catalog["enumerated_count"] == len(target_ids))
    reserved, selected = audit["reserved_ids"], audit["selected_ids"]
    require(isinstance(candidate_ids, list) and len(candidate_ids) <= 50
        and all(type(identity) is str and identity in eligible for identity in candidate_ids)
        and len(set(candidate_ids)) == len(candidate_ids) and isinstance(reserved, list) and len(reserved) <= 10
        and all(type(identity) is str and identity in eligible for identity in reserved) and len(set(reserved)) == len(reserved)
        and candidate_ids[:len(reserved)] == reserved)
    require(isinstance(selected, list) and len(selected) <= 40
        and all(type(identity) is str and identity in target_ids and identity not in reserved for identity in selected)
        and len(set(selected)) == len(selected)
        and candidate_ids[len(reserved):len(reserved) + len(selected)] == selected)
    require(audit["candidate_ids_hash"] == target_eligibility_digest(candidate_ids))
    require(isinstance(audit["fallback"], dict) and set(audit["fallback"]) == {"applied", "mode"}
        and type(audit["fallback"]["applied"]) is bool
        and audit["fallback"]["applied"] is (not audit["applied"])
        and audit["fallback"]["mode"] == (None if audit["applied"] else
            "base_vector_goal_lexical" if audit["available"] else "legacy_target_embedding"))
    if audit["applied"]:
        require(audit["available"] and catalog["complete"] and audit["status"] == "COMPLETED")
        require(audit["attempted"] or not selected and audit["reason"] == "empty_new_catalog")
        require(not audit["attempted"] or audit["provider_admitted"])
    else:
        require(not selected and audit["status"] != "COMPLETED")
    prepared = audit["prepared"]
    if prepared is not None:
        require(isinstance(prepared, dict) and set(prepared) == {"prepared_hash", "input_token_upper_bound",
            "output_token_limit", "token_upper_bound"} and isinstance(prepared["prepared_hash"], str)
            and len(prepared["prepared_hash"]) == 64 and all(character in "0123456789abcdef" for character in prepared["prepared_hash"])
            and type(prepared["input_token_upper_bound"]) is int and prepared["input_token_upper_bound"] >= 0
            and type(prepared["output_token_limit"]) is int and prepared["output_token_limit"] == 6144
            and type(prepared["token_upper_bound"]) is int
            and prepared["token_upper_bound"] == prepared["input_token_upper_bound"] + 6144)
    require(not audit["attempted"] or audit["available"] and prepared is not None and catalog["complete"])
    require(not audit["provider_admitted"] or audit["attempted"])
    require(audit["status"] != "FAILED" or audit["attempted"])
    require(audit["status"] != "UNAVAILABLE" or not audit["attempted"])
    provider = audit["provider"]
    require((provider is not None) == audit["attempted"])
    if provider is not None:
        require(isinstance(provider, dict) and not set(provider) - {"operation_type", "model", "model_revision",
            "prompt_version", "input_tokens", "output_tokens", "latency_ms", "queue_ms", "interval_ms",
            "provider_ms", "_token_charge", "status", "retry_count", "error_code", "usage_complete"}
            and provider.get("operation_type") == "BUSINESS_CATALOG_SELECTION"
            and provider.get("prompt_version") == CATALOG_SELECTOR_VERSION and provider.get("retry_count") == 0
            and provider.get("status") in {"SUCCEEDED", "FAILED"})
        for key in ("model", "model_revision"):
            require(provider.get(key) is None or isinstance(provider[key], str) and len(provider[key]) <= 256)
        for key in ("input_tokens", "output_tokens", "latency_ms", "queue_ms", "interval_ms", "provider_ms", "_token_charge"):
            require(provider.get(key) is None or type(provider[key]) is int and provider[key] >= 0)
        require(type(provider.get("usage_complete")) is bool and provider["usage_complete"] is (
            type(provider.get("input_tokens")) is int and type(provider.get("output_tokens")) is int))
        require(provider.get("error_code") in {None, "CATALOG_SELECTION_FAILED"})
        require((provider["status"] == "SUCCEEDED") == audit["applied"])
    request_budget = audit["request_budget"]
    if request_budget is not None:
        require(isinstance(request_budget, dict) and set(request_budget) == {"before", "after"})
        for snapshot in request_budget.values():
            if snapshot is not None:
                require(isinstance(snapshot, dict) and set(snapshot) == {"max_calls", "max_tokens", "calls", "tokens", "reserved_tokens", "remaining_ms"}
                    and all(type(value) is int and value >= 0 for value in snapshot.values()))
    require(audit["audit_hash"] == catalog_audit_digest(audit))


def validate_catalog_selection_audit(audit, scope, eligible, target_ids, contract, candidate_ids):
    """Fail closed with a stable host error for malformed private receipts."""
    try:
        _validate_catalog_selection_audit(audit, scope, eligible, target_ids, contract, candidate_ids)
    except (TypeError, KeyError, OverflowError):
        raise ValueError("RELEVANCE_SCOPE_NOT_APPLIED") from None


def retrieval_rank_audit_digest(audit: dict) -> str:
    return hashlib.sha256(json.dumps({key: value for key, value in audit.items() if key != "audit_hash"},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_retrieval_rank_audit(audit, scope, eligible, target_ids, candidate_ids):
    """Bound private diagnostics and recheck every disclosed canonical ID.

    The digest binds the captured rankings; it grants no new eligibility or
    business classification. Only a validated BUSINESS_APPLICATION host goal
    uses this audit, and projections/snapshot pages must not repeat it.
    """
    def require(condition):
        if not condition:
            raise ValueError("RELEVANCE_SCOPE_NOT_APPLIED")
    require(isinstance(audit, dict) and scope is not None and scope["target_unit"] == "BUSINESS_APPLICATION")
    require(set(audit) == {"version", "scope_hash", "ranking_scope", "exhaustive", "max_per_ranking",
        "per_stage_limit", "broad_eligible_ids_hash", "target_eligible_ids_hash", "target_prior", "rankings", "summary", "selection", "audit_hash"})
    require(audit["version"] == RETRIEVAL_RANK_AUDIT_VERSION and audit["scope_hash"] == scope["scope_hash"]
        and audit["ranking_scope"] == "bounded_retrieval_hits" and audit["exhaustive"] is False
        and audit["max_per_ranking"] == RETRIEVAL_RANK_LIMIT and audit["per_stage_limit"] == RETRIEVAL_STAGE_LIMIT)
    require(audit["broad_eligible_ids_hash"] == target_eligibility_digest(eligible)
        and audit["target_eligible_ids_hash"] == target_eligibility_digest(target_ids))
    prior = audit["target_prior"]
    require(isinstance(prior, dict) and not set(prior) - {"version", "source", "semantic_version", "semantic_scope_hash",
        "corpus_revision", "task_annotation_revision", "target_eligible_count", "business_membership_confirmed"}
        and prior.get("business_membership_confirmed") is False
        and prior.get("version") == "engineering_recall_prior_v1"
        and isinstance(prior.get("source"), str)
        and prior.get("source") in {"current_occurrence_engineering_audit", "wide_unaudited_fallback",
            "caller_supplied_target_ids", "wide_eligible_default"}
        and type(prior.get("target_eligible_count")) is int and prior["target_eligible_count"] == len(target_ids))
    for key in ("semantic_version", "semantic_scope_hash"):
        require(prior.get(key) is None or isinstance(prior[key], str) and len(prior[key]) <= 100)
    for key in ("corpus_revision", "task_annotation_revision"):
        require(prior.get(key) is None or type(prior[key]) is int and prior[key] >= 0)
    selection, rankings = audit["selection"], audit["rankings"]
    require(isinstance(selection, dict) and set(selection) == {"policy", "broad_reserved", "target_reserved_new", "pool_max", "pool"}
        and selection["policy"] == "broad10_business40_remaining_v2"
        and selection["broad_reserved"] == 10 and selection["target_reserved_new"] == 40 and selection["pool_max"] == 50)
    require(isinstance(selection["pool"], list) and len(selection["pool"]) <= 50)
    selected = {}
    for row in selection["pool"]:
        require(isinstance(row, dict) and set(row) == {"canonical_question_id", "source"})
        identity = row["canonical_question_id"]
        require(isinstance(identity, str) and identity in eligible and identity not in selected
            and isinstance(row["source"], str) and 0 < len(row["source"]) <= 80)
        selected[identity] = row["source"]
    require(list(selected) == candidate_ids)
    require(isinstance(rankings, dict) and set(rankings) == {"broad", "target"})
    for name, allowed in (("broad", set(eligible)), ("target", set(target_ids))):
        rows, seen, positions_seen = rankings[name], set(), {"lexical": set(), "dense": set()}
        require(isinstance(rows, list) and len(rows) <= RETRIEVAL_RANK_LIMIT)
        for rank, row in enumerate(rows, 1):
            require(isinstance(row, dict) and set(row) == {"canonical_question_id", "rank", "rrf_score", "stage_ranks", "selected", "selection_source"})
            identity, score = row["canonical_question_id"], row["rrf_score"]
            require(isinstance(identity, str) and identity in allowed and identity not in seen
                and type(row["rank"]) is int and row["rank"] == rank
                and type(score) in (int, float) and math.isfinite(score)
                and type(row["selected"]) is bool and row["selected"] == (identity in selected)
                and row["selection_source"] == selected.get(identity))
            stages = row["stage_ranks"]
            require(isinstance(stages, dict) and stages and not set(stages) - {"lexical", "dense"}
                and all(type(position) is int and 1 <= position <= RETRIEVAL_STAGE_LIMIT for position in stages.values()))
            for stage, position in stages.items():
                require(position not in positions_seen[stage])
                positions_seen[stage].add(position)
            seen.add(identity)
    target_selected = sum(row["selected"] for row in rankings["target"])
    summary = {"broad_ranked": len(rankings["broad"]), "target_ranked": len(rankings["target"]),
        "pool_count": len(selected), "target_ranked_selected": target_selected,
        "target_ranked_outside_pool": len(rankings["target"]) - target_selected}
    require(isinstance(audit["summary"], dict) and set(audit["summary"]) == set(summary)
        and all(type(audit["summary"][key]) is int and audit["summary"][key] == value for key, value in summary.items()))
    try:
        expected = retrieval_rank_audit_digest(audit)
    except (TypeError, ValueError):
        raise ValueError("RELEVANCE_SCOPE_NOT_APPLIED") from None
    require(isinstance(audit["audit_hash"], str) and audit["audit_hash"] == expected)


def engineering_target_eligibility(session: Session, filters: FilterSpec, eligible: list[str]) -> tuple[list[str], dict]:
    """Use reviewed engineering membership only as a soft target-recall prior.

    The audit is loaded under the same occurrence filters. A canonical whose
    positive occurrence is outside those filters cannot acquire eligibility
    from that other occurrence. Pending source judgments remain in the branch;
    none of this is a positive business-application classification.
    """
    scope = load_semantic_scope(session, filters, task="ENGINEERING_DESIGN")
    identities = sorted(set(session.scalars(select(QuestionOccurrence.canonical_question_id).where(
        QuestionOccurrence.id.in_(scope.selected_occurrence_ids))).all()).intersection(eligible))
    coverage = scope.metadata()
    audited = coverage["accepted"] + coverage["rejected"] + coverage.get("unresolved", 0)
    fallback = audited == 0
    if fallback:
        identities = list(eligible)
    state = session.get(CorpusState, 1)
    return identities, {"version": "engineering_recall_prior_v1", "task": scope.task,
        "semantic_version": scope.version, "semantic_scope_hash": scope.scope_hash,
        "source": "current_occurrence_engineering_audit" if not fallback else "wide_unaudited_fallback",
        "corpus_revision": state.current_revision if state else None,
        "task_annotation_revision": state.task_annotation_revision if state else None,
        "eligible_count": len(eligible), "target_eligible_count": len(identities),
        "target_eligible_ids_hash": target_eligibility_digest(identities),
        "coverage": coverage, "business_membership_confirmed": False,
        "broad_scope": "original_eligible_ids", "requested": True,
        "available": not fallback, "backend_available": True,
        "applied": not fallback, "fallback": fallback,
        "fallback_reason": "no_current_engineering_audit" if fallback else None}


def _engineering_workset_basis(session, retriever, filters):
    """Exact SQL membership and source identity; no retrieval/provider calls."""
    check_model_call_deadline()
    state = session.execute(select(CorpusState.current_revision, CorpusState.indexed_revision,
        CorpusState.canonical_revision, CorpusState.task_annotation_revision).where(CorpusState.id == 1)).one_or_none()
    if state is None:
        raise ValueError("WORKSET_SNAPSHOT_UNAVAILABLE")
    revisions = dict(zip(("corpus_revision", "indexed_revision", "canonical_revision", "task_annotation_revision"), state))
    if any(type(value) is not int or value < 0 for value in revisions.values()):
        raise ValueError("WORKSET_SNAPSHOT_UNAVAILABLE")
    scope = load_semantic_scope(session, filters, task="ENGINEERING_DESIGN")
    occurrence_ids = list(scope.selected_occurrence_ids)
    identities = sorted(set(session.scalars(select(QuestionOccurrence.canonical_question_id)
        .select_from(_active_from()).where(*_conditions(filters), QuestionOccurrence.id.in_(occurrence_ids)))))
    if len(identities) > 50000:
        raise ValueError("FILTER_CAPACITY_EXCEEDED")
    titles = dict(session.execute(select(CanonicalQuestion.id, CanonicalQuestion.canonical_text)
        .where(CanonicalQuestion.id.in_(identities))).all())
    if set(titles) != set(identities):
        raise ValueError("WORKSET_SNAPSHOT_UNAVAILABLE")
    # Read scalar columns instead of cached ORM objects. Even a source/audit
    # edit without a revision bump must invalidate cached workset continuations.
    bindings = [{"occurrence_id": row[0], "canonical_question_id": row[1], "raw_question": row[2],
        "source_spans": row[3], "source_revision_id": row[4], "raw_file_hash": row[5], "decoded_text_hash": row[6],
        "engineering_audit": (row[7].get("semantic_scopes", {}).get("ENGINEERING_DESIGN")
            if isinstance(row[7], dict) and isinstance(row[7].get("semantic_scopes", {}), dict) else None)}
        for row in session.execute(select(QuestionOccurrence.id, QuestionOccurrence.canonical_question_id,
            QuestionOccurrence.raw_question, QuestionOccurrence.source_spans, SourceRevision.id,
            SourceRevision.raw_file_hash, SourceRevision.decoded_text_hash, OccurrenceTaskAnnotation.evidence)
            .select_from(_active_from()).outerjoin(OccurrenceTaskAnnotation,
                OccurrenceTaskAnnotation.occurrence_id == QuestionOccurrence.id)
            .where(*_conditions(filters)).order_by(QuestionOccurrence.id))]
    # Reference frames can contain earlier questions outside the user's topic
    # or response filters. Bind their current physical source identities and
    # observed edges too, without adding those questions to SQL eligibility.
    source_interviews = select(QuestionOccurrence.interview_id).where(
        QuestionOccurrence.id.in_(occurrence_ids)).distinct()
    context_from = (QuestionOccurrence.__table__
        .join(Interview, QuestionOccurrence.interview_id == Interview.id)
        .join(DocumentBuild, Interview.build_id == DocumentBuild.id)
        .join(SourceRevision, DocumentBuild.source_revision_id == SourceRevision.id)
        .join(SourceDocument, SourceRevision.source_document_id == SourceDocument.id))
    reference_bindings = [{"occurrence_id": row[0], "source_interview_id": row[1],
        "build_id": row[2], "question_order": row[3], "raw_question": row[4],
        "source_spans": row[5], "source_revision_id": row[6], "raw_file_hash": row[7],
        "decoded_text_hash": row[8]} for row in session.execute(select(
            QuestionOccurrence.id, QuestionOccurrence.interview_id, Interview.build_id,
            QuestionOccurrence.question_order, QuestionOccurrence.raw_question,
            QuestionOccurrence.source_spans, SourceRevision.id, SourceRevision.raw_file_hash,
            SourceRevision.decoded_text_hash).select_from(context_from)
            .where(*_conditions(FilterSpec()), Interview.id.in_(source_interviews))
            .order_by(QuestionOccurrence.id))]
    reference_ids = [row["occurrence_id"] for row in reference_bindings]
    relation_columns = [column for column in QuestionRelation.__table__.columns if column.name != "created_at"]
    reference_relations = [dict(zip((column.name for column in relation_columns), row))
        for row in session.execute(select(*relation_columns).where(
            QuestionRelation.relation_type == "OBSERVED_FOLLOWUP",
            QuestionRelation.target_occurrence_id.in_(reference_ids)).order_by(QuestionRelation.id))]
    rows = [{"canonical_question_id": identity, "canonical_text": titles[identity]} for identity in identities]
    qualification = {"version": QUALIFICATION_VERSION, "task": "ENGINEERING_DESIGN",
        "semantic_version": scope.version, "semantic_scope_hash": scope.scope_hash, "count": len(identities),
        "ids_hash": ids_digest(identities), "catalog_body_hash": canonical_digest([
            {"canonical_question_id": row["canonical_question_id"],
             "text_hash": hashlib.sha256(row["canonical_text"].encode()).hexdigest()} for row in rows]),
        "source_bindings_hash": canonical_digest({"qualified_sources": bindings,
            "reference_sources": reference_bindings, "reference_relations": reference_relations}),
        "coverage": scope.metadata()}
    return rows, occurrence_ids, qualification, revisions


def _workset_proof_policy(retriever):
    from interview_intelligence.search.business_scope import BUSINESS_SCOPE_CONTRACT
    from interview_intelligence.search.workset import WORKSET_SOURCE_POLICY_VERSION, WORKSET_BATCH_POLICY_VERSION
    ranker = getattr(retriever, "reranker", None)
    def prompt_hash(name):
        prompt = getattr(ranker, name, None)
        return hashlib.sha256(prompt.encode()).hexdigest() if type(prompt) is str else None
    effective = getattr(ranker, "clarified_prompt_identity", None)
    if callable(effective):
        prompt_identity = effective()
        if (type(prompt_identity) is not dict or set(prompt_identity) != {
                "reranker_prompt_hash", "scope_verifier_prompt_hash"}
                or any(type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
                    for value in prompt_identity.values())):
            raise ValueError("WORKSET_PROOF_POLICY_INVALID")
    else:
        prompt_identity = {"reranker_prompt_hash": prompt_hash("system_prompt"),
            "scope_verifier_prompt_hash": prompt_hash("scope_verifier_prompt")}
    return {"reranker_version": getattr(ranker, "version", None),
        "scope_verifier_version": getattr(ranker, "scope_verifier_version", None),
        "business_scope_contract": BUSINESS_SCOPE_CONTRACT, **prompt_identity,
        "workset_source_policy_version": WORKSET_SOURCE_POLICY_VERSION,
        "workset_batch_policy_version": WORKSET_BATCH_POLICY_VERSION,
    }


def prepare_engineering_workset(session, retriever, query, filters, *, relevance_scope,
        engineering_authorization, on_progress=None):
    """Persistable complete universe; optional nomination changes only priority."""
    if type(query) is not str or not 1 <= len(query) <= 2000:
        raise ValueError("invalid workset query")
    scope = validate_relevance_scope(relevance_scope)
    authorization = validate_engineering_authorization(engineering_authorization, scope, filters)
    proof_policy = _workset_proof_policy(retriever)
    rows, _, qualification, revisions = _engineering_workset_basis(session, retriever, filters)
    identities = [row["canonical_question_id"] for row in rows]
    order, audit = identities, {"status": "SQL_ORDER", "reason": "backend_ordering_unavailable", "provider_admitted": False}
    if identities and getattr(retriever, "supports_engineering_workset", False):
        contract = {"version": "sql_canonical_catalog_v1", "count": len(rows), "ids_hash": ids_digest(identities),
            "body_hash": qualification["catalog_body_hash"], "corpus_revision": revisions["corpus_revision"],
            "entries": [{"canonical_question_id": row["canonical_question_id"],
                "text_hash": hashlib.sha256(row["canonical_text"].encode()).hexdigest()} for row in rows]}
        result = retriever.prepare_workset_order(query, identities, relevance_scope=scope, target_catalog=contract,
            on_progress=on_progress)
        order, audit = result.get("candidate_ids"), result.get("audit", {})
        if (type(order) is not list or len(order) != len(identities) or len(set(order)) != len(order)
                or set(order) != set(identities)):
            raise ValueError("WORKSET_COMPLETE_ENUMERATION_REQUIRED")
    manifest = {"version": WORKSET_VERSION, "candidate_ids": order, "candidate_ids_hash": ids_digest(order),
        "order_hash": canonical_digest(order), "filters": filters.model_dump(mode="json"),
        "relevance_scope_hash": scope["scope_hash"], "authorization": authorization, "qualification": qualification,
        "revisions": revisions, "index_schema_version": INDEX_SCHEMA_VERSION,
        "embedding_version": getattr(getattr(retriever, "encoder", None), "version", None),
        "proof_policy": proof_policy,
        "batch_size": WORKSET_BATCH_SIZE, "query_hash": hashlib.sha256(query.encode()).hexdigest()}
    manifest["manifest_hash"] = workset_digest(manifest)
    validate_engineering_workset(session, retriever, filters, workset=manifest, relevance_scope=scope)
    return {"workset": manifest, "meta": {"workset_preparation_audit": audit,
        "candidate_universe_count": len(order), "candidate_universe_complete": True,
        "business_count_complete": False, "relevance_scope_hash": scope["scope_hash"]}}


def validate_engineering_workset(session, retriever, filters, *, workset, relevance_scope):
    """Pure current-fact check for a cached NEXT; never reorders or calls models."""
    scope = validate_relevance_scope(relevance_scope)
    manifest = validate_manifest(workset, scope, filters)
    rows, _, qualification, revisions = _engineering_workset_basis(session, retriever, filters)
    if (qualification != manifest["qualification"] or revisions != manifest["revisions"]
            or ids_digest([row["canonical_question_id"] for row in rows]) != manifest["candidate_ids_hash"]
            or _workset_proof_policy(retriever) != manifest["proof_policy"]
            or getattr(getattr(retriever, "encoder", None), "version", None) != manifest["embedding_version"]):
        raise ValueError("SNAPSHOT_CHANGED")


def process_engineering_workset_batch(session, retriever, query, filters, *, workset, batch_ids,
        relevance_scope, on_progress=None):
    """Offer at most20 fixed members; consume only a validated ordered prefix."""
    scope = validate_relevance_scope(relevance_scope)
    validate_engineering_workset(session, retriever, filters, workset=workset, relevance_scope=scope)
    identities = validate_batch_ids(workset, batch_ids)
    if type(query) is not str or hashlib.sha256(query.encode()).hexdigest() != workset["query_hash"]:
        raise ValueError("WORKSET_QUERY_CHANGED")
    _, occurrence_ids, _, _ = _engineering_workset_basis(session, retriever, filters)
    engineering_scope = load_semantic_scope(session, filters, task="ENGINEERING_DESIGN")
    engineering_confirmed = set(engineering_scope.accepted_occurrence_ids)
    engineering_pending = {row["occurrence_id"]: row for row in engineering_scope.pending_records}
    from interview_intelligence.search.evidence import candidate_source_context
    context_loader = lambda ids: candidate_source_context(session, ids, filters, occurrence_ids=occurrence_ids, include_coverage=True)
    titles = dict(session.execute(select(CanonicalQuestion.id, CanonicalQuestion.canonical_text)
        .where(CanonicalQuestion.id.in_(identities))).all())
    if getattr(retriever, "supports_engineering_workset", False):
        result = retriever.hydrate_and_rerank_ids(query, identities, relevance_scope=scope,
            target_catalog={"corpus_revision": workset["revisions"]["corpus_revision"],
                "embedding_version": workset["embedding_version"], "index_schema_version": workset["index_schema_version"],
                "titles": titles}, candidate_context_loader=context_loader, on_progress=on_progress)
    else:
        result = {"data": [], "outcomes": [], "meta": {"workset_batch_audit": {"status": "UNSUPPORTED"}}}
    if type(result) is not dict:
        raise ValueError("INVALID_ENGINEERING_WORKSET_BATCH_RESULT")
    # Older deterministic/nonadaptive backends omit the field and therefore
    # consume the whole offered window. A declared adaptive subset must be an
    # exact nonempty prefix, never arbitrary selected IDs or a skipped gap.
    processed_ids = result.get("processed_candidate_ids", identities)
    if (type(processed_ids) is not list or not processed_ids
            or any(type(identity) is not str for identity in processed_ids)
            or len(processed_ids) > len(identities) or processed_ids != identities[:len(processed_ids)]):
        raise ValueError("INVALID_ENGINEERING_WORKSET_BATCH_RESULT")
    collections = {}
    for name in ("data", "outcomes"):
        rows = result.get(name, [])
        if (type(rows) is not list or any(type(row) is not dict
                or type(row.get("canonical_question_id")) is not str for row in rows)):
            raise ValueError("INVALID_ENGINEERING_WORKSET_BATCH_RESULT")
        ids = [row["canonical_question_id"] for row in rows]
        indexed = {row["canonical_question_id"]: row for row in rows}
        if (len(indexed) != len(ids) or not set(ids) <= set(processed_ids)
                or ids != [identity for identity in processed_ids if identity in indexed]):
            raise ValueError("INVALID_ENGINEERING_WORKSET_BATCH_RESULT")
        collections[name] = indexed
    returned, outcomes = collections["data"], collections["outcomes"]
    result_meta = result.get("meta", {})
    batch_audit = result_meta.get("workset_batch_audit", {}) if type(result_meta) is dict else None
    if type(batch_audit) is not dict or any(key in batch_audit and batch_audit[key] != value for key, value in (
            ("offered_candidate_ids", identities), ("processed_candidate_ids", processed_ids),
            ("unprocessed_candidate_ids", identities[len(processed_ids):]))):
        raise ValueError("INVALID_ENGINEERING_WORKSET_BATCH_RESULT")
    data, complete_outcomes = [], []
    for identity in processed_ids:
        check_model_call_deadline()
        outcome = outcomes.get(identity, {})
        status = outcome.get("status", "PENDING")
        if status not in {"CONFIRMED", "PENDING", "EXCLUDED"}:
            raise ValueError("INVALID_ENGINEERING_WORKSET_BATCH_RESULT")
        match = returned.get(identity)
        negative_unverified = status == "EXCLUDED" and (outcome.get("source_verified_negative") is not True
            or not independent_negative_adjudication_valid(outcome, scope["scope_hash"]))
        if negative_unverified:
            status = "PENDING"
        if status == "CONFIRMED" and (match is None or match.get("relevance_scope_status") != "CONFIRMED"
                or match.get("relevance_scope_hash") != scope["scope_hash"]):
            status = "PENDING"
        reason = ("独立范围复核回执不足，业务范围待核验" if negative_unverified else
            outcome.get("reason") if type(outcome.get("reason")) is str else "本题尚未取得有效业务范围结论，已保留待核验")
        complete_outcomes.append({"canonical_question_id": identity, "status": status, "reason": reason})
        if status == "EXCLUDED":
            continue
        try:
            detail = get_question_detail(session, identity, filters)
        except KeyError:
            detail = {}
        matching = list(session.execute(select(QuestionOccurrence.id, QuestionOccurrence.raw_question)
            .where(QuestionOccurrence.canonical_question_id == identity, QuestionOccurrence.id.in_(occurrence_ids))))
        allowed = {row[0] for row in matching}
        confirmed_count = sum(row[0] in engineering_confirmed for row in matching)
        pending_sources = [{**engineering_pending[row[0]], "raw_question": row[1]}
            for row in matching if row[0] in engineering_pending]
        match = {**(match or {}), "canonical_question_id": identity, "relevance_scope_status": status,
            "relevance_scope_hash": scope["scope_hash"], "relevance_scope_reason": reason}
        data.append({"canonical_question_id": identity, "canonical_text": titles[identity],
            "topic_id": detail.get("topic_id"), "question_type": detail.get("question_type"),
            "variants": sorted({row[1] for row in matching}), "occurrence_count": len(matching),
            "frequency_basis": "engineering_qualified_occurrences",
            "confirmed_occurrence_count": confirmed_count, "pending_occurrence_count": len(pending_sources),
            "semantic_verification": "CONFIRMED" if confirmed_count else "PENDING", "pending_sources": pending_sources,
            "sources": [row for row in detail.get("sources", []) if row["occurrence_id"] in allowed][:3],
            "retrieval": match, "relevance_scope_status": status, "relevance_scope_hash": scope["scope_hash"],
            "relevance_scope_reason": reason})
    validate_engineering_workset(session, retriever, filters, workset=workset, relevance_scope=scope)
    counts = {key.lower(): sum(row["status"] == key for row in complete_outcomes) for key in ("CONFIRMED", "PENDING", "EXCLUDED")}
    return {"processed_candidate_ids": processed_ids, "data": data, "outcomes": complete_outcomes, "meta": {**result.get("meta", {}),
        "workset_batch_audit": {**batch_audit, "offered_candidate_ids": identities,
            "processed_candidate_ids": processed_ids, "unprocessed_candidate_ids": identities[len(processed_ids):]},
        "workset_manifest_hash": workset["manifest_hash"], "relevance_scope_hash": scope["scope_hash"],
        "offered_batch_size": len(identities),
        "batch_ids": processed_ids, "batch_size": len(processed_ids), "batch_counts": counts,
        "candidate_universe_count": len(workset["candidate_ids"]), "candidate_universe_complete": True,
        "business_count_complete": False}}


def search_questions(
    session: Session, retriever, query: str, filters: FilterSpec,
    *, pipeline: str = "HYBRID", top_k: int = 10, relevance_query: str | None = None,
    lexical_facets: list[str] | None = None, preferred_question_type: str | None = None,
    relevance_scope: dict | None = None, on_progress=None,
) -> dict:
    if not 1 <= len(query) <= 500 or not 1 <= top_k <= 50:
        raise ValueError("invalid search query or top_k")
    scope = validate_relevance_scope(relevance_scope) if relevance_scope is not None else None
    if scope is not None and not getattr(retriever, "supports_relevance_scope", False):
        raise ValueError("RELEVANCE_SCOPE_UNSUPPORTED")
    if on_progress:
        on_progress({"stage":"eligibility"})
    eligible = eligible_canonical_ids(session, filters)
    if not eligible:
        return {"data": [], "meta": {"pipeline": pipeline, "eligible_count": 0,
                                      "candidate_count": 0, "candidate_ids": [],
                                      **({"relevance_scope": scope, "relevance_scope_hash": scope["scope_hash"],
                                          "relevance_scope_applied": True} if scope is not None else {})}}
    options = {}
    if scope is not None:
        options["relevance_scope"] = scope
    target_prior = None
    if scope is not None and scope["target_unit"] == "BUSINESS_APPLICATION":
        if getattr(retriever, "supports_target_eligibility", False):
            target_ids, target_prior = engineering_target_eligibility(session, filters, eligible)
            options.update(target_eligible_ids=target_ids, target_eligibility=target_prior)
            if (pipeline == "HYBRID_RERANK" and scope["retrieval_mode"] == "CANDIDATES"
                    and getattr(retriever, "supports_complete_catalog", False)):
                options["target_catalog"] = target_catalog_contract(session, target_ids, target_prior)
        else:
            target_prior = {"version": "engineering_recall_prior_v1", "source": "backend_unsupported_wide_fallback",
                "fallback": True, "eligible_count": len(eligible), "target_eligible_count": len(eligible),
                "business_membership_confirmed": False, "broad_scope": "original_eligible_ids",
                "requested": True, "available": False, "backend_available": False,
                "applied": False, "fallback_reason": "backend_unsupported"}
    if on_progress:
        if getattr(retriever, "supports_progress", False):
            options["on_progress"] = on_progress
        else:
            on_progress({"stage":"retrieving"})
    if getattr(retriever, "supports_relevance_query", False):
        options["relevance_query"] = relevance_query
    if lexical_facets and getattr(retriever, "supports_lexical_facets", False):
        options["lexical_facets"] = lexical_facets
    if pipeline == "HYBRID_RERANK" and getattr(retriever, "supports_candidate_context", False):
        from interview_intelligence.search.evidence import candidate_source_context
        options["candidate_context_loader"] = lambda ids: candidate_source_context(session, ids, filters)
    if (preferred_question_type is not None and filters.question_type is None
            and pipeline in {"HYBRID", "HYBRID_RERANK"}
            and getattr(retriever, "supports_question_type_preference", False)):
        kind = FilterSpec(question_type=preferred_question_type).question_type
        preferred = eligible_canonical_ids(session, filters.model_copy(update={"question_type": kind}))
        options.update(preferred_question_type=kind.value,
                       preferred_eligible_ids=sorted(set(preferred).intersection(eligible)))
    retrieval = retriever.retrieve(query, eligible, pipeline, top_k, **options)
    if scope is not None:
        metadata = retrieval.get("meta", {})
        if (metadata.get("relevance_scope_hash") != scope["scope_hash"]
                or metadata.get("relevance_scope_applied") is not True):
            raise ValueError("RELEVANCE_SCOPE_NOT_APPLIED")
    if "target_eligible_ids" in options and (
            retrieval.get("meta", {}).get("target_eligible_ids_applied") is not True
            or retrieval.get("meta", {}).get("target_eligible_ids_hash") != target_prior["target_eligible_ids_hash"]):
        raise ValueError("RELEVANCE_SCOPE_NOT_APPLIED")
    candidate_ids = retrieval.get("meta", {}).get("candidate_ids")
    if candidate_ids is not None and (not isinstance(candidate_ids, list)
            or any(not isinstance(identity, str) or identity not in eligible for identity in candidate_ids)):
        raise ValueError("retriever returned an ineligible candidate ID")
    rank_audit = retrieval.get("meta", {}).get("retrieval_rank_audit")
    if rank_audit is not None:
        validate_retrieval_rank_audit(rank_audit, scope, eligible, options.get("target_eligible_ids", eligible), candidate_ids)
    catalog_audit = retrieval.get("meta", {}).get("catalog_selection_audit")
    if catalog_audit is not None:
        validate_catalog_selection_audit(catalog_audit, scope, eligible, options.get("target_eligible_ids", eligible),
            options.get("target_catalog"), candidate_ids)
        if rank_audit is not None and catalog_audit["reserved_ids"] != [
                row["canonical_question_id"] for row in rank_audit["rankings"]["broad"][:10]]:
            raise ValueError("RELEVANCE_SCOPE_NOT_APPLIED")
        if catalog_audit["catalog"]["complete"]:
            rows = [{"canonical_question_id": identity, "canonical_text": text} for identity, text in session.execute(
                select(CanonicalQuestion.id, CanonicalQuestion.canonical_text)
                .where(CanonicalQuestion.id.in_(options["target_eligible_ids"])))]
            if catalog_audit["catalog"]["title_utf8_bytes"] != sum(len(row["canonical_text"].encode()) for row in rows):
                raise ValueError("RELEVANCE_SCOPE_NOT_APPLIED")
            if catalog_audit["prepared"] is not None:
                expected_prepared = CatalogSoftSelector(None).prepare(relevance_query or query, rows, scope,
                    catalog_audit["reserved_ids"])
                if any(catalog_audit["prepared"][key] != expected_prepared[key] for key in catalog_audit["prepared"]):
                    raise ValueError("RELEVANCE_SCOPE_NOT_APPLIED")
    if on_progress:
        on_progress({"stage":"assembling", "count":len(retrieval["data"])})
    data = []
    for match in retrieval["data"]:
        canonical_id = match["canonical_question_id"]
        if canonical_id not in eligible:
            raise ValueError("retriever returned an ineligible canonical ID")
        detail = get_question_detail(session, canonical_id, filters)
        data.append({
            "canonical_question_id": canonical_id,
            "canonical_text": detail["canonical_text"],
            "topic_id": detail["topic_id"],
            "question_type": detail["question_type"],
            "variants": detail["variants"],
            "occurrence_count": detail["occurrence_count"],
            "sources": detail["sources"][:3],
            "retrieval": match,
            **({"relevance_scope_status": match["relevance_scope_status"],
                "relevance_scope_reason": match.get("relevance_scope_reason", ""),
                **({"relevance_scope_hash": match["relevance_scope_hash"]}
                   if "relevance_scope_hash" in match else {})}
               if match.get("relevance_scope_status") in {"CONFIRMED", "PENDING"} else {}),
        })
    return {"data": data, "meta": {**retrieval.get("meta", {}), "eligible_count": len(eligible),
        **({"target_eligibility": target_prior} if target_prior is not None else {})}}
