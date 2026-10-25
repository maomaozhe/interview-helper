"""Elasticsearch BM25/dense retrieval and revision-fenced full index rebuilds."""

from __future__ import annotations

from interview_intelligence.search.retry import RelevanceProviderUnavailable

import hashlib
import math
import re
from collections import Counter, defaultdict
from uuid import uuid4

import httpx
import jieba
from sqlalchemy import select

from interview_intelligence.analytics.stats import _active_from
from interview_intelligence.contracts import QuestionType
from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusState, IndexSyncTask, Interview, QuestionOccurrence,
    SourceDocument, DocumentBuild, EmbeddingCache,
)
from interview_intelligence.relevance_scope import validate_relevance_scope
from interview_intelligence.providers.runtime import check_model_call_deadline, request_timeout, current_limits
from interview_intelligence.search.catalog_selector import (
    CatalogSoftSelector, CATALOG_AUDIT_VERSION, CATALOG_SELECTOR_VERSION, CATALOG_INPUT_BYTE_LIMIT,
    canonical_digest, catalog_digest, catalog_audit_digest,
)
from interview_intelligence.search.business_scope import BUSINESS_SCOPE_CONTRACT
from interview_intelligence.search.service import (
    rrf, target_eligibility_digest, retrieval_rank_audit_digest, validate_retrieval_rank_audit,
    RETRIEVAL_RANK_AUDIT_VERSION, RETRIEVAL_RANK_LIMIT, RETRIEVAL_STAGE_LIMIT,
)
from interview_intelligence.search.workset import (
    WORKSET_BATCH_SIZE, ids_digest, independent_negative_adjudication_valid, require as require_workset,
)


ALIAS = "interview_questions"
SCHEMA_VERSION = "search_v1_jieba_whitespace"
CONSTRUCTION_TASK_QUERY = "如何 开发 实现 一个 功能 做 建设 网页 网站"
CONSTRUCTION_CONTEXT_BOOST = 0.2
BUSINESS_APPLICATION_TARGET = "面向用户需求、业务目标和业务流程的应用或功能系统设计"
BUSINESS_APPLICATION_RESERVED = 40
BUSINESS_APPLICATION_POOL_POLICY = "broad10_business40_remaining_v2"


def _validate_workset_capacity(selection, ready_ids, scope_hash, max_tokens, max_calls):
    """A capacity receipt selects proof input, never new SQL eligibility."""
    from interview_intelligence.search.workset import WORKSET_BATCH_POLICY_VERSION
    valid = lambda condition: require_workset(condition, "INVALID_ENGINEERING_WORKSET_BATCH_RESULT")
    valid(type(selection) is dict and set(selection) == {"version", "status", "reason", "scope_hash",
        "offered_candidate_ids", "selected_candidate_ids", "max_tokens", "max_calls", "required_calls",
        "required_tokens", "remaining_calls", "remaining_tokens", "prepared"})
    valid(selection["version"] == WORKSET_BATCH_POLICY_VERSION and selection["scope_hash"] == scope_hash
        and type(selection["reason"]) is str and selection["reason"] in {"EMPTY_WINDOW", "INSUFFICIENT_MODEL_CALLS",
            "FIRST_CANDIDATE_EXCEEDS_TOKEN_BUDGET", "MAXIMUM_FITTING_PREFIX"}
        and selection["offered_candidate_ids"] == ready_ids
        and type(selection["max_tokens"]) is int and selection["max_tokens"] == max_tokens
        and type(selection["max_calls"]) is int and selection["max_calls"] == max_calls)
    selected = selection["selected_candidate_ids"]
    valid(type(selected) is list and all(type(identity) is str for identity in selected)
        and selected == ready_ids[:len(selected)] and len(selected) <= len(ready_ids))
    for field in ("required_calls", "required_tokens", "remaining_calls", "remaining_tokens"):
        valid(type(selection[field]) is int and selection[field] >= 0)
    if selection["status"] == "NO_CAPACITY":
        valid(not selected and selection["required_calls"] == selection["required_tokens"] == 0
            and selection["reason"] in {"INSUFFICIENT_MODEL_CALLS", "FIRST_CANDIDATE_EXCEEDS_TOKEN_BUDGET"}
            and selection["prepared"] == {} and selection["remaining_calls"] == max_calls
            and selection["remaining_tokens"] == max_tokens)
    else:
        valid(selection["status"] == "READY" and selection["reason"] == "MAXIMUM_FITTING_PREFIX"
            and selected and selection["required_calls"] == 2
            and max_calls >= 2 and type(selection["prepared"]) is dict
            and set(selection["prepared"]) == {"primary", "scope"})
        total = 0
        for stage in selection["prepared"].values():
            valid(type(stage) is dict and set(stage) == {"prepared_sha256", "input_token_upper_bound",
                "token_upper_bound", "output_token_limit"})
            valid(type(stage["prepared_sha256"]) is str and bool(re.fullmatch(r"[0-9a-f]{64}", stage["prepared_sha256"]))
                and type(stage["input_token_upper_bound"]) is int and stage["input_token_upper_bound"] >= 0
                and type(stage["output_token_limit"]) is int and stage["output_token_limit"] == 6144
                and type(stage["token_upper_bound"]) is int
                and stage["token_upper_bound"] == stage["input_token_upper_bound"] + 6144)
            total += stage["token_upper_bound"]
        valid(total == selection["required_tokens"] <= max_tokens
            and selection["remaining_tokens"] == max_tokens - total
            and selection["remaining_calls"] == max_calls - 2)
    return selected


def _workset_remaining_budget():
    limits = current_limits.get()
    if limits is None:
        return 0, 0, "request_limits_unavailable"
    with limits.lock:
        limits.check()
        return (max(0, limits.max_tokens - limits.tokens - limits.token_reservations),
            max(0, limits.max_calls - limits.calls), "current_request_limits")


def tokenize(text: str) -> str:
    protected = re.findall(r"[A-Za-z][A-Za-z0-9_+.#-]*", text)
    words = [word.casefold() for word in jieba.cut(text) if word.strip()]
    return " ".join(dict.fromkeys([*words, *(word.casefold() for word in protected)]))


def _valid_embedding(vector, dimension: int) -> bool:
    try:
        return (len(vector) == dimension
                and all(math.isfinite(value) for value in vector)
                and any(value != 0 for value in vector))
    except (TypeError, ValueError):
        return False


def _facet_candidate_pool(main: list[dict], facets: list[list[tuple[str, float]]]) -> list[dict]:
    """Reserve the main Top20, then cover lexical branches within the same 50."""
    rows = {item["canonical_question_id"]: {
        **item, "stage_scores": dict(item["stage_scores"]), "stage_ranks": {}} for item in main}
    for index, ranking in enumerate(facets, 1):
        stage = f"lexical_facet_{index}"
        for rank, (canonical_id, score) in enumerate(ranking, 1):
            item = rows.setdefault(canonical_id, {"canonical_question_id": canonical_id,
                                                  "stage_scores": {}, "stage_ranks": {}})
            item["stage_scores"][stage] = score
            item["stage_ranks"][stage] = rank
    selected, seen = [], set()

    def append(canonical_id):
        if len(selected) >= 50 or canonical_id in seen:
            return False
        seen.add(canonical_id)
        selected.append(rows[canonical_id])
        return True

    for item in main[:20]:
        append(item["canonical_question_id"])
    positions, additions = [0] * len(facets), [0] * len(facets)

    def add_from_branches(limit):
        while len(selected) < 50:
            added = False
            for index, ranking in enumerate(facets):
                while positions[index] < len(ranking) and ranking[positions[index]][0] in seen:
                    positions[index] += 1
                if additions[index] >= limit or positions[index] >= len(ranking):
                    continue
                canonical_id = ranking[positions[index]][0]
                positions[index] += 1
                if append(canonical_id):
                    additions[index] += 1
                    added = True
            if not added:
                break

    add_from_branches(10)
    for item in main[20:]:
        append(item["canonical_question_id"])
        if len(selected) >= 50:
            break
    add_from_branches(50)
    return selected


def _select_reranked(rows, facets, top_k):
    """Keep semantic leaders, cover missing facets once, then resume that order."""
    ranked = sorted(rows, key=lambda row: (-row.get("relevance_grade", 0), row.get("rerank_rank", 0)))
    if not facets:
        return ranked[:top_k]
    selected = ranked[:min(5, top_k)]
    seen = {row["canonical_question_id"] for row in selected}
    for index in range(1, len(facets) + 1):
        if len(selected) >= top_k:
            break
        stage = f"lexical_facet_{index}"
        if any(row.get("stage_ranks", {}).get(stage, 51) <= 10 for row in selected):
            continue
        row = min((row for row in ranked if row.get("stage_ranks", {}).get(stage, 51) <= 10
                   and row["canonical_question_id"] not in seen),
                  key=lambda row: row["stage_ranks"][stage], default=None)
        if row is not None:
            selected.append(row)
            seen.add(row["canonical_question_id"])
    for row in ranked:
        if len(selected) >= top_k:
            break
        if row["canonical_question_id"] not in seen:
            selected.append(row)
            seen.add(row["canonical_question_id"])
    return selected


def _preferred_candidate_pool(main, preferred, facets, construction=()):
    """Bounded union of broad retrieval, same-query type preference and facets."""
    rows = {item["canonical_question_id"]: {**item, "stage_scores": dict(item["stage_scores"]),
             "stage_ranks": {"broad_rrf": rank}} for rank, item in enumerate(main, 1)}
    for rank, item in enumerate(preferred, 1):
        row = rows.setdefault(item["canonical_question_id"], {"canonical_question_id": item["canonical_question_id"],
                              "stage_scores": {}, "stage_ranks": {}})
        row["stage_scores"].update({"preferred_" + key: value for key, value in item["stage_scores"].items()})
        row["preferred_rrf_score"] = item["rrf_score"]
        row["stage_ranks"]["preferred_rrf"] = rank
        row["stage_ranks"].update({"preferred_" + key: value for key, value in item.get("stage_ranks", {}).items()})
    for index, ranking in enumerate(facets, 1):
        for rank, (canonical_id, score) in enumerate(ranking, 1):
            row = rows.setdefault(canonical_id, {"canonical_question_id": canonical_id,
                                  "stage_scores": {}, "stage_ranks": {}})
            row["stage_scores"][f"lexical_facet_{index}"] = score
            row["stage_ranks"][f"lexical_facet_{index}"] = rank
    for rank, (canonical_id, score) in enumerate(construction, 1):
        row = rows.setdefault(canonical_id, {"canonical_question_id": canonical_id,
                              "stage_scores": {}, "stage_ranks": {}})
        row["stage_scores"]["construction_task"] = score
        row["stage_ranks"]["construction_task"] = rank
    selected, seen = [], set()

    def append(canonical_id, source):
        if len(selected) >= 50 or canonical_id in seen:
            return False
        seen.add(canonical_id)
        rows[canonical_id]["candidate_selection_source"] = source
        selected.append(rows[canonical_id])
        return True

    for item in main[:10 if facets else 20]:
        append(item["canonical_question_id"], "broad_reserved")
    added, limit = 0, 30
    for item in preferred:
        if added >= limit or len(selected) >= 50:
            break
        added += append(item["canonical_question_id"], "preferred")
    # The original broad/preferred leaders retain their places. Task wording
    # diversity uses only the remaining slots; ordinary facets remain available
    # for evidence and to fill a sparse construction branch.
    for canonical_id, _ in construction:
        if len(selected) >= 50:
            break
        append(canonical_id, "construction_task")
    positions = [0] * len(facets)
    while len(selected) < 50:
        added_facet = False
        for index, ranking in enumerate(facets):
            while positions[index] < len(ranking) and ranking[positions[index]][0] in seen:
                positions[index] += 1
            if positions[index] < len(ranking):
                added_facet |= append(ranking[positions[index]][0], "facet")
                positions[index] += 1
        if not added_facet:
            break
    for ranking, source in ((main, "broad_fill"), (preferred, "preferred_fill")):
        for item in ranking:
            if len(selected) >= 50:
                break
            append(item["canonical_question_id"], source)
    return selected


def _business_target_query(scope: dict) -> str:
    """Describe the user's target unit without inventing a business domain."""
    evidence = list(dict.fromkeys(item["quote"] for item in scope["target_evidence"]))
    # The scope retains every original message; this bounded recall string only
    # supplies target evidence and its neutral meaning to the encoder/lexical ES
    # branch. It adds neither a traditional-only restriction nor an AI exclusion.
    return " ".join([" ".join(evidence)[:440], BUSINESS_APPLICATION_TARGET]).strip()


def _business_candidate_pool(main: list[dict], base: list[dict], business: list[dict]) -> list[dict]:
    """Reserve broad and business-target coverage inside the same 50 candidates."""
    rows = {item["canonical_question_id"]: {**item, "stage_scores": dict(item["stage_scores"]),
            "stage_ranks": {"broad_rrf": rank, **item.get("stage_ranks", {})}}
            for rank, item in enumerate(main, 1)}
    for item in base:
        row = rows.setdefault(item["canonical_question_id"], {"canonical_question_id": item["canonical_question_id"],
                              "stage_scores": {}, "stage_ranks": {}})
        row.update({key: value for key, value in item.items() if key not in {"stage_scores", "stage_ranks"}})
        row["stage_scores"].update(item.get("stage_scores", {}))
        row["stage_ranks"].update(item.get("stage_ranks", {}))
    for rank, item in enumerate(business, 1):
        row = rows.setdefault(item["canonical_question_id"], {"canonical_question_id": item["canonical_question_id"],
                              "stage_scores": {}, "stage_ranks": {}})
        row["business_application_rrf_score"] = item["rrf_score"]
        row["stage_scores"].update({"business_application_" + key: value
                                    for key, value in item["stage_scores"].items()})
        row["stage_ranks"]["business_application_rrf"] = rank
        row["stage_ranks"].update({"business_application_" + key: value
                                   for key, value in item.get("stage_ranks", {}).items()})
    selected, seen = [], set()

    def append(identity, source):
        if identity in seen or len(selected) >= 50:
            return False
        seen.add(identity)
        rows[identity]["candidate_selection_source"] = source
        selected.append(rows[identity])
        return True

    for item in main[:10]:
        append(item["canonical_question_id"], "broad_reserved")
    additions = 0
    for item in business:
        if additions >= BUSINESS_APPLICATION_RESERVED:
            break
        additions += append(item["canonical_question_id"], "business_application")
    # Do not let a pre-existing preferred-type branch consume the entire
    # remainder before lexical facets have an opportunity to contribute.
    stages = sorted({stage for item in base for stage in item.get("stage_ranks", {})
                     if stage.startswith("lexical_facet_")})
    stages = (["preferred_rrf"] if any("preferred_rrf" in item.get("stage_ranks", {}) for item in base) else []) + stages
    if any("construction_task" in item.get("stage_ranks", {}) for item in base):
        stages.append("construction_task")
    branches = [iter(sorted((item for item in base if stage in item.get("stage_ranks", {})),
                             key=lambda item: item["stage_ranks"][stage])) for stage in stages]
    while len(selected) < 50:
        added = False
        for branch in branches:
            for item in branch:
                if append(item["canonical_question_id"], item.get("candidate_selection_source", "facet")):
                    added = True
                    break
        if not added:
            break
    for item in base:
        append(item["canonical_question_id"], item.get("candidate_selection_source", "broad_fill"))
    for item in business:
        append(item["canonical_question_id"], "business_application_fill")
    return selected


def _business_rank_audit(scope, eligible_ids, target_ids, target_prior, main, business, rankings, business_rankings, pool, pipeline):
    """Capture bounded pre-pool rankings once, without source text or model input."""
    selected = {row["canonical_question_id"]: row["candidate_selection_source"] for row in pool}
    def capture(fused, stages, labels):
        if (len(fused) > RETRIEVAL_RANK_LIMIT or len(stages) != len(labels)
                or any(len(stage) > RETRIEVAL_STAGE_LIMIT or len({item[0] for item in stage}) != len(stage) for stage in stages)):
            raise ValueError("RELEVANCE_SCOPE_NOT_APPLIED")
        positions = [{identity: rank for rank, (identity, _) in enumerate(stage, 1)} for stage in stages]
        return [{"canonical_question_id": row["canonical_question_id"], "rank": rank, "rrf_score": row["rrf_score"],
            "stage_ranks": {label: positions[index][row["canonical_question_id"]] for index, label in enumerate(labels)
                if row["canonical_question_id"] in positions[index]},
            "selected": row["canonical_question_id"] in selected,
            "selection_source": selected.get(row["canonical_question_id"])} for rank, row in enumerate(fused, 1)]
    labels = ["lexical", "dense"] if len(rankings) == 2 else ["dense" if pipeline == "DENSE" else "lexical"]
    prior = {key: value for key, value in (target_prior or {}).items() if key in {
        "version", "source", "semantic_version", "semantic_scope_hash", "corpus_revision", "task_annotation_revision", "target_eligible_count"}}
    prior.setdefault("source", "caller_supplied_target_ids" if target_ids != eligible_ids else "wide_eligible_default")
    prior.setdefault("version", "engineering_recall_prior_v1")
    prior["target_eligible_count"] = len(target_ids)
    prior["business_membership_confirmed"] = False
    audit = {"version": RETRIEVAL_RANK_AUDIT_VERSION, "scope_hash": scope["scope_hash"],
        "ranking_scope": "bounded_retrieval_hits", "exhaustive": False,
        "max_per_ranking": RETRIEVAL_RANK_LIMIT, "per_stage_limit": RETRIEVAL_STAGE_LIMIT,
        "broad_eligible_ids_hash": target_eligibility_digest(eligible_ids),
        "target_eligible_ids_hash": target_eligibility_digest(target_ids), "target_prior": prior,
        "rankings": {"broad": capture(main, rankings, labels), "target": capture(business, business_rankings, labels)},
        "selection": {"policy": BUSINESS_APPLICATION_POOL_POLICY, "broad_reserved": 10,
            "target_reserved_new": BUSINESS_APPLICATION_RESERVED, "pool_max": 50,
            "pool": [{"canonical_question_id": row["canonical_question_id"], "source": row["candidate_selection_source"]} for row in pool]}}
    target_selected = sum(row["selected"] for row in audit["rankings"]["target"])
    audit["summary"] = {"broad_ranked": len(main), "target_ranked": len(business), "pool_count": len(pool),
        "target_ranked_selected": target_selected, "target_ranked_outside_pool": len(business) - target_selected}
    audit["audit_hash"] = retrieval_rank_audit_digest(audit)
    validate_retrieval_rank_audit(audit, scope, eligible_ids, target_ids, list(selected))
    return audit


class CatalogProjectionUnavailable(ValueError):
    def __init__(self, reason, enumerated_count=0, title_utf8_bytes=0):
        super().__init__(reason)
        self.enumerated_count, self.title_utf8_bytes = enumerated_count, title_utf8_bytes


def _catalog_candidate_pool(main, base, selected_ids):
    """Soft nominations occupy the same forty slots, without fake ES ranks."""
    selected = [{"canonical_question_id": identity, "rrf_score": 0.0, "stage_scores": {}}
        for identity in selected_ids]
    pool = _business_candidate_pool(main, base, selected)
    for row in pool:
        row.pop("business_application_rrf_score", None)
        row["stage_ranks"].pop("business_application_rrf", None)
        if row["candidate_selection_source"] == "business_application":
            row["candidate_selection_source"] = "catalog_soft_selection"
    return pool


class ElasticsearchRetriever:
    supports_candidate_context = True
    supports_relevance_query = True
    supports_lexical_facets = True
    supports_question_type_preference = True
    supports_relevance_scope = True
    supports_target_eligibility = True
    supports_complete_catalog = True
    supports_engineering_workset = True
    supports_progress = True
    def __init__(self, url: str, encoder=None, *, reranker=None, client=None, alias: str = ALIAS, catalog_selector=None):
        self.client = client or httpx.Client(base_url=url.rstrip("/"), timeout=60, trust_env=False)
        self.encoder = encoder
        self.reranker = reranker
        self.alias = alias
        self.catalog_selector = catalog_selector

    def _complete_catalog(self, pit_holder, target_ids, contract):
        """Read every same-PIT title and fence it against the SQL contract."""
        expected = {entry["canonical_question_id"]: entry["text_hash"] for entry in contract["entries"]}
        if (set(expected) != set(target_ids) or len(expected) != len(contract["entries"])
                or contract.get("version") != "sql_canonical_catalog_v1" or contract.get("count") != len(target_ids)
                or contract.get("ids_hash") != target_eligibility_digest(target_ids)
                or contract.get("body_hash") != canonical_digest(contract["entries"])
                or type(contract.get("corpus_revision")) is not int):
            raise CatalogProjectionUnavailable("catalog_projection_unavailable")
        rows, seen, after, title_bytes = [], set(), None, 0
        def unavailable(reason="catalog_projection_unavailable"):
            raise CatalogProjectionUnavailable(reason, len(rows), title_bytes)
        while True:
            check_model_call_deadline()
            body = {"pit": {"id": pit_holder[0], "keep_alive": "1m"}, "size": 500, "track_total_hits": True,
                "sort": ["_shard_doc"], "_source": ["canonical_text", "corpus_revision", "schema_version", "embedding_version"],
                "query": {"bool": {"filter": [{"terms": {"_id": target_ids}}]}}}
            if after is not None:
                body["search_after"] = after
            try:
                response = self._request("post", "/_search", json=body, timeout=request_timeout(60),
                    params={"allow_partial_search_results": "false"})
            except httpx.HTTPError:
                unavailable()
            pit_holder[0] = response.get("pit_id", pit_holder[0])
            total, hits = response.get("hits", {}).get("total"), response.get("hits", {}).get("hits")
            if (response.get("timed_out") is True or response.get("_shards", {}).get("failed", 0) != 0
                    or not isinstance(total, dict) or total.get("relation") != "eq"
                    or type(total.get("value")) is not int or total["value"] != len(target_ids)
                    or not isinstance(hits, list)):
                unavailable()
            for hit in hits:
                identity, source = hit.get("_id"), hit.get("_source")
                if (type(identity) is not str or identity not in expected or identity in seen
                        or not isinstance(source, dict) or type(source.get("canonical_text")) is not str
                        or not source["canonical_text"].strip() or source.get("schema_version") != SCHEMA_VERSION
                        or type(source.get("corpus_revision")) is not int
                        or source["corpus_revision"] != contract["corpus_revision"]
                        or source.get("embedding_version") != getattr(self.encoder, "version", None)
                        or hashlib.sha256(source["canonical_text"].encode()).hexdigest() != expected[identity]):
                    unavailable()
                seen.add(identity)
                title_bytes += len(source["canonical_text"].encode())
                if title_bytes > CATALOG_INPUT_BYTE_LIMIT:
                    unavailable("catalog_input_capacity")
                rows.append({"canonical_question_id": identity, "canonical_text": source["canonical_text"]})
            if len(seen) == len(target_ids):
                break
            if not hits or not isinstance(hits[-1].get("sort"), list) or not hits[-1]["sort"] or hits[-1]["sort"] == after:
                unavailable()
            after = hits[-1]["sort"]
        rows.sort(key=lambda row: row["canonical_question_id"])
        if catalog_digest(rows) != contract["body_hash"]:
            unavailable()
        return rows

    def _request(self, method: str, path: str, **kwargs) -> dict:
        if method == "delete" and isinstance(self.client, httpx.Client):
            response = self.client.request("DELETE", path, **kwargs)
        else:
            response = getattr(self.client, method)(path, **kwargs)
        response.raise_for_status()
        return response.json()

    def prepare_workset_order(self, query, canonical_ids, *, relevance_scope, target_catalog, on_progress=None):
        """Complete same-PIT projection check and optional soft priority only."""
        scope = validate_relevance_scope(relevance_scope)
        identities = sorted(canonical_ids)
        audit = {"version": "complete_workset_order_v1", "candidate_count": len(identities),
            "complete_membership_preserved": True, "status": "SQL_ORDER", "reason": None}
        if not identities:
            return {"candidate_ids": [], "audit": audit}
        check_model_call_deadline()
        try:
            pit_holder = [self._request("post", f"/{self.alias}/_pit", params={"keep_alive": "1m"})["id"]]
        except (httpx.HTTPError, KeyError):
            return {"candidate_ids": identities, "audit": {**audit, "reason": "catalog_projection_unavailable",
                "projection_complete": False}}
        try:
            try:
                rows = self._complete_catalog(pit_holder, identities, target_catalog)
            except CatalogProjectionUnavailable as error:
                return {"candidate_ids": identities, "audit": {**audit, "reason": str(error),
                    "projection_complete": False}}
        finally:
            try:
                self._request("delete", "/_pit", json={"id": pit_holder[0]})
            except httpx.HTTPError:
                pass  # PIT expiry is bounded; a cleanup failure does not erase SQL membership.
        selector = self.catalog_selector or (CatalogSoftSelector(self.reranker) if CatalogSoftSelector.available(self.reranker) else None)
        if selector is None or scope["retrieval_mode"] != "CANDIDATES":
            return {"candidate_ids": identities, "audit": {**audit, "projection_complete": True,
                "reason": "selector_unavailable"}}
        if on_progress:
            on_progress({"stage": "workset_ordering", "count": len(rows)})
        result = selector.prioritize_complete(query, rows, scope)
        result["audit"]["projection_complete"] = True
        return result

    def hydrate_and_rerank_ids(self, query, canonical_ids, *, relevance_scope, target_catalog,
            candidate_context_loader, on_progress=None):
        """Hydrate exactly one fixed SQL-qualified batch, without ANN/embedding."""
        scope = validate_relevance_scope(relevance_scope)
        require_workset(type(canonical_ids) is list and 1 <= len(canonical_ids) <= WORKSET_BATCH_SIZE
            and len(set(canonical_ids)) == len(canonical_ids) and all(type(identity) is str for identity in canonical_ids)
            and type(target_catalog) is dict and set(target_catalog.get("titles", {})) == set(canonical_ids),
            "INVALID_ENGINEERING_WORKSET_BATCH")
        candidates, failures = {}, {}
        check_model_call_deadline()
        pit = None
        try:
            pit = self._request("post", f"/{self.alias}/_pit", params={"keep_alive": "1m"})["id"]
            response = self._request("post", "/_search", json={"pit": {"id": pit, "keep_alive": "1m"},
                "size": len(canonical_ids), "track_total_hits": True,
                "query": {"ids": {"values": canonical_ids}},
                "_source": ["canonical_text", "corpus_revision", "schema_version", "embedding_version"]},
                timeout=request_timeout(60), params={"allow_partial_search_results": "false"})
            pit = response.get("pit_id", pit)
            if response.get("timed_out") is True or response.get("_shards", {}).get("failed", 0):
                raise ValueError("WORKSET_BATCH_PROJECTION_UNAVAILABLE")
            for hit in response.get("hits", {}).get("hits", []):
                identity, source = hit.get("_id"), hit.get("_source", {})
                require_workset(identity in canonical_ids and identity not in candidates and identity not in failures,
                    "INVALID_ENGINEERING_WORKSET_BATCH_RESULT")
                if (type(source) is not dict or source.get("canonical_text") != target_catalog["titles"][identity]
                        or source.get("corpus_revision") != target_catalog["corpus_revision"]
                        or type(source.get("corpus_revision")) is not int
                        or source.get("schema_version") != target_catalog["index_schema_version"]
                        or source.get("embedding_version") != target_catalog["embedding_version"]):
                    failures[identity] = "检索投影与当前工作集版本或题干不一致，业务范围待核验"
                else:
                    candidates[identity] = {"canonical_question_id": identity,
                        "canonical_text": target_catalog["titles"][identity], "source_context_required": True}
        except (httpx.HTTPError, ValueError, KeyError) as error:
            if isinstance(error, ValueError) and str(error) in {"QUERY_CANCELLED", "QUERY_DEADLINE_EXCEEDED"}:
                raise
            if isinstance(error, ValueError) and str(error) == "INVALID_ENGINEERING_WORKSET_BATCH_RESULT":
                raise
            candidates = {}
            failures = {identity: "本轮来源检索未完成，业务范围待核验" for identity in canonical_ids}
        finally:
            if pit is not None:
                try:
                    self._request("delete", "/_pit", json={"id": pit})
                except httpx.HTTPError:
                    pass
        try:
            contexts = candidate_context_loader(list(candidates)) if candidates else {}
        except (OSError, ValueError) as error:
            if isinstance(error, ValueError) and str(error) in {"QUERY_CANCELLED", "QUERY_DEADLINE_EXCEEDED"}:
                raise
            contexts = {}
        contexts = {identity: context for identity, context in contexts.items()
            if identity in canonical_ids and type(context) is dict} if type(contexts) is dict else {}
        prepared = []
        for identity in canonical_ids:
            row, context = candidates.get(identity), contexts.get(identity)
            if row is None:
                failures.setdefault(identity, "当前题的检索投影缺失，业务范围待核验")
                continue
            if (type(context) is not dict or type(context.get("source_context")) is not list
                    or not any(type(source) is dict and source.get("source_context_status") == "VERIFIED"
                        and type(source.get("original_question")) is str and source["original_question"].strip()
                        for source in context["source_context"])):
                failures[identity] = "当前原问来源缺失或尚未核验，业务范围待核验"
                continue
            prepared.append({**row, **context, "source_context_required": True})
        ready_ids = [row["canonical_question_id"] for row in prepared]
        selected, processed_ids = prepared, list(canonical_ids)
        capacity = {"applied": False, "status": "LEGACY_ADAPTER_NO_CAPACITY_CLAIM"}
        selector = getattr(self.reranker, "select_workset_prefix", None)
        if prepared and callable(selector):
            max_tokens, max_calls, budget_source = _workset_remaining_budget()
            capacity = selector(query, prepared, relevance_scope=scope, max_tokens=max_tokens, max_calls=max_calls)
            check_model_call_deadline()
            selected_ids = _validate_workset_capacity(capacity, ready_ids, scope["scope_hash"], max_tokens, max_calls)
            capacity = {**capacity, "applied": True, "budget_source": budget_source}
            selected = prepared[:len(selected_ids)]
            if selected_ids:
                if len(selected_ids) < len(ready_ids):
                    processed_ids = canonical_ids[:canonical_ids.index(ready_ids[len(selected_ids)])]
            else:
                # Consume the source-fault prefix plus the first oversized
                # source-ready row as Pending, so NEXT cannot loop on it.
                processed_ids = canonical_ids[:canonical_ids.index(ready_ids[0]) + 1]
                failures[ready_ids[0]] = "当前剩余预算不足以完成主排和独立范围核验，业务范围待核验"
        elif not prepared:
            capacity = {"applied": False, "status": "SOURCE_FAULTS_ONLY_NO_MODEL_CALL"}
        require_workset(bool(processed_ids) and processed_ids == canonical_ids[:len(processed_ids)],
            "INVALID_ENGINEERING_WORKSET_BATCH_RESULT")
        audit, returned = None, []
        if selected and self.reranker is not None:
            try:
                if on_progress:
                    on_progress({"stage": "workset_verifying", "count": len(selected)})
                returned = self.reranker.rerank(query, selected, relevance_scope=scope)
                audit = getattr(returned, "audit", None)
            except Exception as error:
                if isinstance(error, ValueError) and str(error) in {"QUERY_CANCELLED", "QUERY_DEADLINE_EXCEEDED"}:
                    raise
                failures.update({row["canonical_question_id"]: "本轮核验未完成，业务范围待核验" for row in selected})
        elif selected:
            failures.update({row["canonical_question_id"]: "核验模型尚不可用，业务范围待核验" for row in selected})
        require_workset(isinstance(returned, list) and all(type(row) is dict
            and type(row.get("canonical_question_id")) is str for row in returned), "INVALID_ENGINEERING_WORKSET_BATCH_RESULT")
        indexed = {row["canonical_question_id"]: row for row in returned}
        require_workset(len(indexed) == len(returned) and set(indexed) <= {row["canonical_question_id"] for row in selected},
            "INVALID_ENGINEERING_WORKSET_BATCH_RESULT")
        audit_candidates = audit.get("candidates", []) if type(audit) is dict else []
        valid_audit = (type(audit_candidates) is list
            and all(type(proof) is dict and type(proof.get("canonical_question_id")) is str
                and proof["canonical_question_id"] in {row["canonical_question_id"] for row in selected} for proof in audit_candidates)
            and len({proof["canonical_question_id"] for proof in audit_candidates}) == len(audit_candidates))
        audit_rows = {proof["canonical_question_id"]: proof for proof in audit_candidates} if valid_audit else {}
        data, outcomes, source_coverage = [], [], {}
        for identity in processed_ids:
            row, proof = indexed.get(identity), audit_rows.get(identity, {})
            coverage = contexts.get(identity, {}).get("source_context_coverage", {})
            qualified_sources = coverage.get("qualified_occurrence_ids", [])
            negative_sources = proof.get("source_verified_negative_occurrence_ids")
            verified_sources = {source.get("occurrence_id") for source in contexts.get(identity, {}).get("source_context", [])
                if type(source) is dict and type(source.get("occurrence_id")) is str
                and source.get("source_context_status") == "VERIFIED" and source.get("original_question_complete") is True
                and type(source.get("original_question")) is str and source["original_question"].strip()}
            complete_negative = (coverage.get("complete") is True and type(qualified_sources) is list
                and all(type(source_id) is str for source_id in qualified_sources)
                and len(qualified_sources) == len(set(qualified_sources))
                and type(coverage.get("qualified_occurrence_count")) is int
                and coverage["qualified_occurrence_count"] == len(qualified_sources)
                and coverage.get("qualified_occurrence_ids_hash") == ids_digest(qualified_sources)
                and set(qualified_sources) == verified_sources and type(negative_sources) is list
                and all(type(source_id) is str for source_id in negative_sources)
                and len(negative_sources) == len(set(negative_sources))
                and set(negative_sources) == set(qualified_sources) and bool(qualified_sources))
            business = proof.get("business_assessment")
            valid_business = (type(business) is dict and business.get("grounded") is True
                and business.get("contract") == BUSINESS_SCOPE_CONTRACT
                and business.get("invalid_evidence_fields") == []
                and proof.get("relevance_scope_hash") == scope["scope_hash"])
            valid_negative = (valid_business and business.get("outcome") == "NEGATIVE"
                and business.get("in_scope") is False and proof.get("decision") in {"SCOPE_REJECTED", "TASK_REJECTED"}
                and independent_negative_adjudication_valid(proof, scope["scope_hash"]))
            valid_positive = (valid_business and business.get("outcome") == "POSITIVE"
                and business.get("in_scope") is True and proof.get("decision") == "ACCEPTED")
            source_coverage[identity] = {**coverage, "verified_negative_occurrence_ids": negative_sources or [],
                "negative_coverage_complete": complete_negative, "negative_proof_contract_valid": valid_negative}
            status = "PENDING"
            if identity not in failures:
                if proof.get("relevance_scope_status") == "EXCLUDED" and valid_negative and complete_negative and row is None:
                    status = "EXCLUDED"
                elif (row is not None and row.get("relevance_scope_status") == "CONFIRMED"
                        and proof.get("relevance_scope_status") == "CONFIRMED"
                        and valid_positive and row.get("relevance_scope_hash") == scope["scope_hash"]):
                    status = "CONFIRMED"
            incomplete_negative = proof.get("relevance_scope_status") == "EXCLUDED" and not complete_negative
            invalid_negative = proof.get("relevance_scope_status") == "EXCLUDED" and not valid_negative
            reason = failures.get(identity) or ("范围核验回执不足或与原问证据不一致，业务范围待核验"
                if invalid_negative else None) or ("尚未覆盖本题全部合格原问，不能按局部负证排除，业务范围待核验"
                if incomplete_negative else None) or (row or {}).get("relevance_scope_reason") or (
                "已由当前原问证据核验为范围外" if status == "EXCLUDED" else
                "已按当前业务范围核验" if status == "CONFIRMED" else "业务范围仍待核验")
            outcomes.append({"canonical_question_id": identity, "status": status, "reason": reason,
                **({"source_verified_negative": True, "relevance_scope_hash": scope["scope_hash"],
                    **{key: proof[key] for key in ("scope_adjudication_contract", "scope_adjudication_status",
                        "scope_verification_status")}} if status == "EXCLUDED" else {})})
            if status != "EXCLUDED":
                data.append({**(row or {}), "canonical_question_id": identity, "relevance_scope_status": status,
                    "relevance_scope_reason": reason, "relevance_scope_hash": scope["scope_hash"],
                    **({"relevance_grade": 0} if status == "PENDING" else {})})
        return {"processed_candidate_ids": processed_ids, "data": data, "outcomes": outcomes, "meta": {"workset_batch_audit": {
            "offered_candidate_ids": list(canonical_ids), "processed_candidate_ids": processed_ids,
            "candidate_ids": processed_ids, "source_ready_ids": ready_ids,
            "selected_source_ready_ids": [row["canonical_question_id"] for row in selected],
            "unprocessed_candidate_ids": canonical_ids[len(processed_ids):], "capacity_selection": capacity,
            "rerank_audit": audit, "projection_or_source_failures": {key: value for key, value in failures.items()
                if key in processed_ids}, "source_context_coverage": source_coverage},
            "reranker_version": getattr(self.reranker, "version", None)}}

    def retrieve(self, query: str, eligible_ids: list[str], pipeline: str, top_k: int,
                 *, relevance_query: str | None = None, lexical_facets: list[str] | None = None,
                 preferred_question_type: str | None = None, preferred_eligible_ids: list[str] | None = None,
                 relevance_scope: dict | None = None, target_eligible_ids: list[str] | None = None,
                 target_eligibility: dict | None = None, target_catalog: dict | None = None,
                 on_progress=None, candidate_context_loader=None) -> dict:
        if pipeline not in {"BM25", "DENSE", "HYBRID", "HYBRID_RERANK"}:
            raise ValueError("unknown retrieval pipeline")
        if pipeline != "BM25" and self.encoder is None:
            raise ValueError("EMBEDDING_NOT_READY")
        if pipeline == "HYBRID_RERANK" and self.reranker is None:
            raise ValueError("RERANKER_NOT_READY")
        scope = validate_relevance_scope(relevance_scope) if relevance_scope is not None else None
        business_target = _business_target_query(scope) if scope and scope["target_unit"] == "BUSINESS_APPLICATION" else None
        if lexical_facets is not None and (not isinstance(lexical_facets, list) or len(lexical_facets) > 3 or
                any(not isinstance(facet, str) or not facet.strip() or len(facet) > 150 for facet in lexical_facets)):
            raise ValueError("invalid lexical facets")
        facets = list(dict.fromkeys(facet.strip() for facet in lexical_facets or [])) if pipeline in {"HYBRID", "HYBRID_RERANK"} else []
        if preferred_question_type is not None:
            preferred_question_type = QuestionType(preferred_question_type).value
        eligible_set = set(eligible_ids)
        if target_eligible_ids is not None and (not business_target or not isinstance(target_eligible_ids, list)
                or any(not isinstance(identity, str) or identity not in eligible_set for identity in target_eligible_ids)
                or len(set(target_eligible_ids)) != len(target_eligible_ids)):
            raise ValueError("invalid target eligibility IDs")
        if target_eligibility is not None and (target_eligible_ids is None or not isinstance(target_eligibility, dict)):
            raise ValueError("invalid target eligibility provenance")
        target_ids = sorted(target_eligible_ids) if target_eligible_ids is not None else eligible_ids
        target_set = set(target_ids)
        catalog_requested = bool(pipeline == "HYBRID_RERANK" and business_target
            and scope["retrieval_mode"] == "CANDIDATES" and target_catalog is not None)
        selector = self.catalog_selector or (CatalogSoftSelector(self.reranker) if CatalogSoftSelector.available(self.reranker) else None)
        catalog_enabled = bool(catalog_requested and selector is not None)
        catalog_rows, catalog_error, catalog_audit = None, None, None
        catalog_enumerated_count = catalog_title_bytes = 0
        if preferred_eligible_ids is not None and (preferred_question_type is None
                or not isinstance(preferred_eligible_ids, list)
                or any(not isinstance(qid, str) or qid not in eligible_set for qid in preferred_eligible_ids)):
            raise ValueError("invalid question type preference IDs")
        preferred_ids = sorted(set(preferred_eligible_ids or [])) if pipeline in {"HYBRID", "HYBRID_RERANK"} else []
        preference = bool(preferred_question_type and preferred_ids)
        if not eligible_ids:
            return {"data": [], "meta": {"pipeline": pipeline,
                **({"relevance_scope": scope, "relevance_scope_hash": scope["scope_hash"],
                    "relevance_scope_applied": True} if scope is not None else {})}}
        # Waiting for the shared model gate can exceed a PIT's lifetime.
        # Keep the ES snapshot only across the main and optional lexical requests.
        vector = business_vector = None
        if pipeline in {"DENSE", "HYBRID", "HYBRID_RERANK"}:
            if on_progress:
                on_progress({"stage":"embedding"})
            vector = (self.encoder.embed_query(query) if hasattr(self.encoder, "embed_query")
                      else self.encoder.embed(query))
            if business_target:
                # The catalog selector replaces this second model request.
                # Its bounded fallback reuses this already-paid base vector.
                business_vector = vector if catalog_enabled else (
                    self.encoder.embed_query(business_target) if hasattr(self.encoder, "embed_query")
                    else self.encoder.embed(business_target))
        if on_progress:
            on_progress({"stage":"retrieving"})
        pit = self._request("post", f"/{self.alias}/_pit", params={"keep_alive": "1m"})["id"]
        try:
            filter_clause = {"terms": {"_id": eligible_ids}}
            target_filters = [filter_clause, {"terms": {"_id": target_ids}}] if target_eligible_ids is not None else [filter_clause]
            target_filter = {"bool": {"filter": target_filters}} if target_eligible_ids is not None else filter_clause
            rankings = []
            facet_rankings = []
            preferred_rankings = []
            construction_ranking = []
            business_rankings = []
            source_texts = {}
            if pipeline in {"BM25", "HYBRID", "HYBRID_RERANK"}:
                body = {
                    "pit": {"id": pit, "keep_alive": "1m"},
                    "size": 100,
                    "query": {"bool": {"filter": [filter_clause], "must": [{
                        "multi_match": {"query": tokenize(query),
                                        "fields": ["search_tokens^2", "variants_tokens"]}
                    }]}},
                }
                hits = self._request("post", "/_search", json=body,
                                     params={"allow_partial_search_results": "false"})["hits"]["hits"]
                source_texts.update({hit["_id"]: hit.get("_source", {}).get("canonical_text", "") for hit in hits})
                rankings.append([(hit["_id"], hit["_score"]) for hit in hits])
            if business_target:
                business_bodies = []
                if pipeline in {"BM25", "HYBRID", "HYBRID_RERANK"}:
                    business_bodies.append({"pit": {"id": pit, "keep_alive": "1m"}, "size": 100,
                        "query": {"bool": {"filter": target_filters, "must": [{"multi_match": {
                            "query": tokenize(business_target), "fields": ["search_tokens^2", "variants_tokens"]}}]}}})
                if business_vector is not None:
                    business_bodies.append({"pit": {"id": pit, "keep_alive": "1m"}, "size": 100,
                        "knn": {"field": "embedding", "query_vector": business_vector, "k": 100,
                                "num_candidates": 500, "filter": target_filter}})
                for body in business_bodies:
                    hits = self._request("post", "/_search", json=body,
                                         params={"allow_partial_search_results": "false"})["hits"]["hits"]
                    if any(hit["_id"] not in target_set for hit in hits):
                        raise ValueError("BUSINESS_SCOPE_RETRIEVAL_FAILED")
                    source_texts.update({hit["_id"]: hit.get("_source", {}).get("canonical_text", "") for hit in hits})
                    business_rankings.append([(hit["_id"], hit["_score"]) for hit in hits])
            for facet in facets:
                body = {
                    "pit": {"id": pit, "keep_alive": "1m"},
                    "size": 50,
                    "query": {"bool": {"filter": [filter_clause], "must": [{
                        "multi_match": {"query": tokenize(facet),
                                        "fields": ["search_tokens^2", "variants_tokens"]}
                    }]}},
                }
                hits = self._request("post", "/_search", json=body,
                                     params={"allow_partial_search_results": "false"})["hits"]["hits"]
                source_texts.update({hit["_id"]: hit.get("_source", {}).get("canonical_text", "") for hit in hits})
                facet_rankings.append([(hit["_id"], hit["_score"]) for hit in hits])
            if pipeline in {"DENSE", "HYBRID", "HYBRID_RERANK"}:
                body = {
                    "pit": {"id": pit, "keep_alive": "1m"},
                    "size": 100,
                    "knn": {"field": "embedding", "query_vector": vector,
                            "k": 100, "num_candidates": 500, "filter": filter_clause},
                }
                hits = self._request("post", "/_search", json=body,
                                     params={"allow_partial_search_results": "false"})["hits"]["hits"]
                source_texts.update({hit["_id"]: hit.get("_source", {}).get("canonical_text", "") for hit in hits})
                rankings.append([(hit["_id"], hit["_score"]) for hit in hits])
            if preference:
                preferred_filter = {"bool": {"filter": [filter_clause, {"terms": {"_id": preferred_ids}}]}}
                for body in (
                    {"pit": {"id": pit, "keep_alive": "1m"}, "size": 100,
                     "query": {"bool": {"filter": preferred_filter["bool"]["filter"], "must": [{
                         "multi_match": {"query": tokenize(query), "fields": ["search_tokens^2", "variants_tokens"]}}]}}},
                    {"pit": {"id": pit, "keep_alive": "1m"}, "size": 100,
                     "knn": {"field": "embedding", "query_vector": vector, "k": 100,
                             "num_candidates": 500, "filter": preferred_filter}},
                ):
                    hits = self._request("post", "/_search", json=body,
                                         params={"allow_partial_search_results": "false"})["hits"]["hits"]
                    if any(hit["_id"] not in preferred_ids for hit in hits):
                        raise ValueError("LEXICAL_FACET_RETRIEVAL_FAILED")
                    source_texts.update({hit["_id"]: hit.get("_source", {}).get("canonical_text", "") for hit in hits})
                    preferred_rankings.append([(hit["_id"], hit["_score"]) for hit in hits])
                if preferred_question_type == "SYSTEM_DESIGN" and facets:
                    # This is an extra soft-type lexical branch, not a change
                    # to eligibility or the query used by the semantic verifier.
                    # Keep the caller's object words in an independent clause.
                    body = {"pit": {"id": pit, "keep_alive": "1m"}, "size": 100,
                        "query": {"bool": {"filter": preferred_filter["bool"]["filter"],
                            "should": [{"multi_match": {"query": tokenize(query),
                                "fields": ["search_tokens^2", "variants_tokens"],
                                "boost": CONSTRUCTION_CONTEXT_BOOST}},
                                {"multi_match": {"query": tokenize(CONSTRUCTION_TASK_QUERY),
                                    "fields": ["search_tokens^2", "variants_tokens"]}}],
                            "minimum_should_match": 1}}}
                    hits = self._request("post", "/_search", json=body,
                                         params={"allow_partial_search_results": "false"})["hits"]["hits"]
                    if any(hit["_id"] not in preferred_ids for hit in hits):
                        raise ValueError("LEXICAL_FACET_RETRIEVAL_FAILED")
                    source_texts.update({hit["_id"]: hit.get("_source", {}).get("canonical_text", "") for hit in hits})
                    construction_ranking = [(hit["_id"], hit["_score"]) for hit in hits]
            if catalog_enabled:
                pit_holder = [pit]
                try:
                    catalog_rows = self._complete_catalog(pit_holder, target_ids, target_catalog)
                    source_texts.update({row["canonical_question_id"]: row["canonical_text"] for row in catalog_rows})
                except (CatalogProjectionUnavailable, httpx.HTTPError) as failure:
                    catalog_error = str(failure) if isinstance(failure, CatalogProjectionUnavailable) else "catalog_projection_unavailable"
                    catalog_enumerated_count = getattr(failure, "enumerated_count", 0)
                    catalog_title_bytes = getattr(failure, "title_utf8_bytes", 0)
                finally:
                    pit = pit_holder[0]
        except httpx.HTTPError as failure:
            if business_target:
                raise ValueError("BUSINESS_SCOPE_RETRIEVAL_FAILED") from failure
            if preference:
                raise ValueError("LEXICAL_FACET_RETRIEVAL_FAILED") from failure
            if facets:
                raise ValueError("LEXICAL_FACET_RETRIEVAL_FAILED") from failure
            raise
        finally:
            try:
                self._request("delete", "/_pit", json={"id": pit})
            except httpx.HTTPError as failure:
                if business_target:
                    raise ValueError("BUSINESS_SCOPE_RETRIEVAL_FAILED") from failure
                if preference:
                    raise ValueError("LEXICAL_FACET_RETRIEVAL_FAILED") from failure
                if facets:
                    raise ValueError("LEXICAL_FACET_RETRIEVAL_FAILED") from failure
                raise
        rerank_status="NOT_REQUESTED"
        rerank_audit = None
        rank_audit = None
        executed=pipeline
        if pipeline in {"BM25", "DENSE"} and not business_target:
            candidate_ids = [canonical_id for canonical_id, _ in rankings[0]]
            data = [{"canonical_question_id": canonical_id, "score": score,
                     "stage_scores": {pipeline.lower(): score}}
                    for canonical_id, score in rankings[0][:top_k]]
        else:
            main = rrf(*rankings)
            preferred_weights = (1.0, 2.0) if len(preferred_rankings) == 2 else tuple(1.0 for _ in preferred_rankings)
            preferred = rrf(*preferred_rankings, weights=preferred_weights)
            preferred_stage_ranks = [{canonical_id: rank for rank, (canonical_id, _) in enumerate(ranking, 1)}
                                     for ranking in preferred_rankings]
            for item in preferred:
                item["stage_ranks"] = {f"stage_{index + 1}": ranking[item["canonical_question_id"]]
                                      for index, ranking in enumerate(preferred_stage_ranks)
                                      if item["canonical_question_id"] in ranking}
            data = (_preferred_candidate_pool(main, preferred, facet_rankings, construction_ranking) if preference else
                    _facet_candidate_pool(main, facet_rankings) if facets else main[:50])
            business = rrf(*business_rankings, weights=(1.0, 2.0) if len(business_rankings) == 2 else None)
            if business_target:
                if catalog_requested:
                    reserved = [row["canonical_question_id"] for row in main[:10]]
                    receipt = {"status": "UNAVAILABLE", "attempted": False, "provider_admitted": False, "applied": False,
                        "reason": catalog_error or "selector_unavailable", "selected_ids": [], "prepared": None,
                        "provider": None, "request_budget": None}
                    if catalog_rows is not None:
                        if on_progress:
                            on_progress({"stage": "catalog_selection", "count": len(catalog_rows)})
                        receipt = selector.select(relevance_query or query, catalog_rows, scope, reserved)
                    if receipt["applied"]:
                        data = _catalog_candidate_pool(main, data, receipt["selected_ids"])
                    else:
                        data = _business_candidate_pool(main, data, business)
                    catalog_audit = {"version": CATALOG_AUDIT_VERSION, "selector_version": CATALOG_SELECTOR_VERSION,
                        "scope_hash": scope["scope_hash"], "requested": True, "available": catalog_enabled,
                        **{key: receipt[key] for key in ("attempted", "provider_admitted", "applied", "status", "reason", "prepared", "provider", "request_budget", "selected_ids")},
                        "reserved_ids": reserved, "catalog": {"expected_count": len(target_ids),
                            "enumerated_count": len(catalog_rows) if catalog_rows is not None else catalog_enumerated_count,
                            "complete": catalog_rows is not None, "ids_hash": target_catalog["ids_hash"],
                            "body_hash": target_catalog["body_hash"], "corpus_revision": target_catalog["corpus_revision"],
                            "index_schema_version": SCHEMA_VERSION,
                            "title_utf8_bytes": sum(len(row["canonical_text"].encode()) for row in catalog_rows) if catalog_rows is not None else catalog_title_bytes},
                        "fallback": {"applied": not receipt["applied"], "mode": None if receipt["applied"] else
                            "base_vector_goal_lexical" if catalog_enabled else "legacy_target_embedding"},
                        "candidate_ids_hash": target_eligibility_digest([row["canonical_question_id"] for row in data])}
                    catalog_audit["audit_hash"] = catalog_audit_digest(catalog_audit)
                else:
                    data = _business_candidate_pool(main, data, business)
                rank_audit = _business_rank_audit(scope, eligible_ids, target_ids, target_eligibility,
                    main, business, rankings, business_rankings, data, pipeline)
            provenance = {row["canonical_question_id"]: {key: row[key] for key in (
                "rrf_score", "preferred_rrf_score", "business_application_rrf_score", "stage_scores", "stage_ranks", "candidate_selection_source") if key in row}
                for row in data} if preference or business_target else {}
            branch_counts = dict(Counter(row["candidate_selection_source"] for row in data)) if preference or business_target else {}
            candidate_count = len(data)
            candidate_ids = [item["canonical_question_id"] for item in data]
            if pipeline == "HYBRID_RERANK":
                if on_progress:
                    on_progress({"stage":"reranking", "count":candidate_count})
                data = [{**item, "canonical_text": source_texts.get(item["canonical_question_id"], "")}
                        for item in data]
                if candidate_context_loader is not None:
                    contexts = candidate_context_loader([item["canonical_question_id"] for item in data])
                    # The loader's presence establishes the source-authority
                    # contract even when a row is missing or malformed. Such
                    # rows must stay insufficient rather than silently falling
                    # back to legacy canonical-only semantic evidence.
                    contexts = contexts if isinstance(contexts, dict) else {}
                    data = [{**item, **(context if isinstance(context := contexts.get(item["canonical_question_id"]), dict) else {}),
                             "source_context_required": True} for item in data]
                try:
                    data = (self.reranker.rerank(relevance_query or query, data, relevance_scope=scope)
                            if scope is not None else self.reranker.rerank(relevance_query or query, data))
                    rerank_audit = getattr(data, "audit", None)
                    accepted_data = data
                    partial = bool(rerank_audit and rerank_audit.get("candidate_verification_status") == "PARTIAL"
                                   and type(rerank_audit.get("invalid_candidate_count")) is int
                                   and rerank_audit["invalid_candidate_count"] > 0)
                    if partial:
                        verified_ids = {item["canonical_question_id"] for item in rerank_audit["candidates"]
                                        if item["decision"] == "ACCEPTED"
                                        and type(item.get("relevance_grade")) is int and item["relevance_grade"] >= 2
                                        and item.get("quote_grounded") is True}
                        accepted_data = [item for item in accepted_data if item["canonical_question_id"] in verified_ids
                                         or scope is not None and item.get("relevance_scope_status") == "PENDING"
                                         and item.get("relevance_scope_hash") == scope["scope_hash"]]
                    data = _select_reranked(accepted_data, facets, top_k)
                    if rerank_audit:
                        selected = {item["canonical_question_id"] for item in data[:top_k]}
                        pending_selected = {item["canonical_question_id"] for item in data[:top_k]
                                            if item.get("relevance_scope_status") == "PENDING"}
                        rerank_audit = {**rerank_audit, "candidates": [
                            {**item, **({"retrieval_provenance": provenance[item["canonical_question_id"]]}
                                       if item["canonical_question_id"] in provenance else {}),
                                **({"relevance_scope_status": "PENDING", "returned_pending": True}
                                   if item["canonical_question_id"] in pending_selected else {}),
                                "decision": item["decision"] if item["canonical_question_id"] in pending_selected else
                                "RETURNED" if item["canonical_question_id"] in selected else
                                "PAGE_CUTOFF" if item["decision"] == "ACCEPTED" else item["decision"]}
                            for item in rerank_audit["candidates"]]}
                    rerank_status="COMPLETED_PARTIAL" if partial else "COMPLETED"
                except RelevanceProviderUnavailable as e:
                    data = []
                    rerank_status, executed = "FAILED", "HYBRID"
                    rerank_audit = {"verification_failure": e.diagnostics}
                except Exception as e:
                    if (scope is not None and catalog_audit is not None and isinstance(e, ValueError) and str(e) in {
                            "QUERY_TOKEN_BUDGET_EXCEEDED", "QUERY_MODEL_BUDGET_EXCEEDED",
                            "MODEL_TOKEN_BUDGET_EXCEEDED", "MODEL_CALL_BUDGET_EXCEEDED"}):
                        data = [{**row, "relevance_grade": 0, "relevance_scope_status": "PENDING",
                            "relevance_scope_hash": scope["scope_hash"],
                            "relevance_scope_reason": "本轮核验预算不足，业务范围待核验"} for row in data]
                        rerank_status = "COMPLETED_PARTIAL"
                        rerank_audit = {"candidate_verification_status": "PARTIAL", "invalid_candidate_count": len(data),
                            "invalid_schema_count": 0, "invalid_evidence_count": 0,
                            "confirmed_candidate_count": 0, "pending_candidate_count": len(data),
                            "unresolved_candidate_ids": [row["canonical_question_id"] for row in data],
                            "verification_failure": {"phase": "RERANK", "error_code": str(e)},
                            "candidates": [{"canonical_question_id": row["canonical_question_id"],
                                "decision": "UNVERIFIED_BUDGET", "relevance_scope_status": "PENDING", "returned_pending": True}
                                for row in data]}
                    elif scope is not None or getattr(self.reranker, "fail_closed", False):
                        if isinstance(e, ValueError):
                            raise
                        raise ValueError("REQUIRED_RELEVANCE_VERIFICATION_FAILED") from e
                    else:
                        from openai import APIError
                        from pydantic import ValidationError
                        if isinstance(e,ValueError) and str(e) in {"QUERY_CANCELLED","QUERY_DEADLINE_EXCEEDED"}: raise
                        budget=isinstance(e,ValueError) and str(e) in {"QUERY_TOKEN_BUDGET_EXCEEDED","QUERY_MODEL_BUDGET_EXCEEDED"}
                        if not budget and not isinstance(e,(httpx.HTTPError,APIError,ValidationError)): raise
                        rerank_status="SKIPPED_BUDGET" if budget else "FAILED"
                        executed="HYBRID"
            data = data[:top_k]
        metadata = {"pipeline": executed,"rerank_status":rerank_status,
                "index_schema_version": SCHEMA_VERSION, "relevance_query": relevance_query or query,
                "reranker_version": self.reranker.version if pipeline == "HYBRID_RERANK" else None,
                "rerank_audit": rerank_audit,
                "candidate_count": candidate_count if pipeline in {"HYBRID", "HYBRID_RERANK"} or business_target else len(rankings[0]),
                "candidate_ids": candidate_ids,
                "embedding_version": self.encoder.version if self.encoder else None}
        if rerank_audit and "candidate_verification_status" in rerank_audit:
            metadata.update(candidate_verification_status=rerank_audit["candidate_verification_status"],
                            invalid_candidate_count=rerank_audit.get("invalid_candidate_count", 0),
                            invalid_evidence_count=rerank_audit.get("invalid_evidence_count", 0),
                            invalid_schema_count=rerank_audit.get("invalid_schema_count", 0))
        if lexical_facets:
            metadata.update(lexical_facets=facets, lexical_facets_ignored=not bool(facets),
                candidate_pool_policy="main20_facet10_round_robin_v1" if facets else "main_only",
                result_selection_policy="top5_missing_facet_once_v1" if facets and rerank_status in {"COMPLETED", "COMPLETED_PARTIAL"} else "grade_then_model_rank_v1")
        if preferred_question_type is not None:
            metadata["question_type_preference"] = {"hint": preferred_question_type, "applied": preference,
                "preferred_eligible_count": len(preferred_ids), "broad_reserved": 10 if facets else 20,
                "fusion_policy": ("lexical1_dense2_rrf_v1" if len(preferred_rankings) == 2 else "equal_weight_rrf_v1") if preference else None,
                "weights": list(preferred_weights) if preference else [],
                "ranking_stages": ["lexical", "dense"][:len(preferred_rankings)] if preference else [],
                "preferred_new_limit": 30, "pool_max": 50,
                "selected_by_branch": branch_counts if preference else {},
                "ignored_reason": None if preference else "empty_preferred_scope" if pipeline in {"HYBRID", "HYBRID_RERANK"} else "pipeline"}
            if preference:
                metadata["candidate_pool_policy"] = "broad10_preferred30_facets_remaining_v2" if facets else "broad20_preferred30_v1"
                if preferred_question_type == "SYSTEM_DESIGN" and facets:
                    metadata["candidate_pool_policy"] = "broad10_preferred30_construction_fill_facets_v1"
                    metadata["construction_task_recall"] = {"query": CONSTRUCTION_TASK_QUERY,
                        "context_query": query, "context_boost": CONSTRUCTION_CONTEXT_BOOST,
                        "scope": "preferred_eligible_ids",
                        "candidate_count": len(construction_ranking),
                        "selected_count": branch_counts.get("construction_task", 0)}
        if scope is not None:
            metadata.update(relevance_scope=scope, relevance_scope_hash=scope["scope_hash"], relevance_scope_applied=True)
        if business_target:
            metadata["candidate_pool_policy"] = BUSINESS_APPLICATION_POOL_POLICY
            metadata["retrieval_rank_audit"] = rank_audit
            if catalog_audit is not None:
                metadata["catalog_selection_audit"] = catalog_audit
                metadata["scope_candidate_counts"] = {"confirmed": sum(row.get("relevance_scope_status") == "CONFIRMED" for row in data),
                    "pending": sum(row.get("relevance_scope_status") == "PENDING" for row in data), "visible": len(data)}
            if preference:
                metadata["question_type_preference"]["broad_reserved"] = 10
            metadata["business_application_recall"] = {
                "query": business_target, "scope_hash": scope["scope_hash"],
                "scope": "target_eligible_ids" if target_eligible_ids is not None else "eligible_ids",
                "target_eligible_count": len(target_ids),
                "ranking_stages": ["lexical", "dense"] if len(business_rankings) == 2 else [pipeline.lower()],
                "fusion_policy": "lexical1_dense2_rrf_v1" if len(business_rankings) == 2 else "equal_weight_rrf_v1",
                "candidate_count": len(business), "reserved_new_limit": BUSINESS_APPLICATION_RESERVED,
                "selected_count": len(catalog_audit["selected_ids"]) if catalog_audit and catalog_audit["applied"] else
                    sum("business_application_rrf" in row["stage_ranks"] for row in provenance.values()),
                "selected_by_branch": branch_counts, "pool_max": 50,
                "target_dense_query_source": "base_query_vector" if catalog_enabled else "business_goal_vector",
                "catalog_selection_applied": bool(catalog_audit and catalog_audit["applied"]),
                "catalog_count": len(catalog_rows) if catalog_rows is not None else None,
            }
        if target_eligible_ids is not None:
            metadata.update(target_eligible_ids_applied=True,
                target_eligibility_applied=target_eligibility.get("applied", True) if target_eligibility else True,
                target_eligible_ids_hash=target_eligibility_digest(target_ids),
                target_eligibility=target_eligibility or {"source": "caller_supplied_target_ids",
                    "business_membership_confirmed": False})
        return {"data": data, "meta": metadata}


def rebuild_index(database, retriever: ElasticsearchRetriever) -> int:
    """Build a complete new physical index and atomically switch its alias."""
    if retriever.encoder is None:
        raise ValueError("EMBEDDING_NOT_READY")
    with database.session() as session:
        state = session.get(CorpusState, 1)
        revision = state.current_revision
        rows = session.execute(
            select(QuestionOccurrence, CanonicalQuestion)
            .select_from(_active_from())
            .where(SourceDocument.active_build_id == DocumentBuild.id,
                   Interview.analytics_eligible.is_(True),
                   DocumentBuild.decision == "INCLUDED")
        )
        documents: dict[str, dict] = {}
        for occurrence, canonical in rows:
            entry = documents.setdefault(canonical.id, {
                "canonical_text": canonical.canonical_text,
                "text_hash": hashlib.sha256(canonical.canonical_text.encode("utf-8")).hexdigest(),
                "variants": set(), "topic_ids": set(), "question_types": set(),
            })
            entry["variants"].add(occurrence.raw_question)
            entry["topic_ids"].add(occurrence.topic_id)
            entry["question_types"].add(occurrence.question_type)
        cached_vectors = {
            cached.text_hash: cached.vector
            for cached in session.scalars(select(EmbeddingCache).where(
                EmbeddingCache.text_hash.in_({document["text_hash"] for document in documents.values()}),
                EmbeddingCache.embedding_version == retriever.encoder.version,
                EmbeddingCache.dimension == retriever.encoder.dimension,
            ))
            if _valid_embedding(cached.vector, retriever.encoder.dimension)
        }
    physical = f"{retriever.alias}_r{revision}_{SCHEMA_VERSION}_{uuid4().hex[:8]}"
    mapping = {"mappings": {"properties": {
        "canonical_text": {"type": "keyword"},
        "variants": {"type": "keyword"},
        "search_tokens": {"type": "text", "analyzer": "whitespace"},
        "variants_tokens": {"type": "text", "analyzer": "whitespace"},
        "topic_ids": {"type": "keyword"},
        "question_types": {"type": "keyword"},
        "embedding": {"type": "dense_vector", "dims": retriever.encoder.dimension,
                      "index": True, "similarity": "cosine"},
        "embedding_version": {"type": "keyword"},
        "schema_version": {"type": "keyword"},
        "corpus_revision": {"type": "integer"},
    }}}
    retriever._request("put", f"/{physical}", json=mapping)
    for canonical_id, document in sorted(documents.items()):
        variants = sorted(document["variants"])
        vector = cached_vectors.get(document["text_hash"])
        if vector is None:
            vector = retriever.encoder.embed(document["canonical_text"])
        if not _valid_embedding(vector, retriever.encoder.dimension):
            raise ValueError("INVALID_INDEX_EMBEDDING")
        payload = {
            "canonical_text": document["canonical_text"],
            "variants": variants,
            "search_tokens": tokenize(document["canonical_text"]),
            "variants_tokens": " ".join(tokenize(variant) for variant in variants),
            "topic_ids": sorted(document["topic_ids"]),
            "question_types": sorted(document["question_types"]),
            "embedding": vector,
            "embedding_version": retriever.encoder.version,
            "schema_version": SCHEMA_VERSION,
            "corpus_revision": revision,
        }
        retriever._request("put", f"/{physical}/_doc/{canonical_id}", json=payload)
    retriever._request("post", f"/{physical}/_refresh")
    count = retriever._request("get", f"/{physical}/_count")["count"]
    if count != len(documents):
        raise ValueError("INDEX_DOCUMENT_COUNT_MISMATCH")
    with database.session() as session:
        if session.get(CorpusState, 1).current_revision != revision:
            raise ValueError("SNAPSHOT_CHANGED")
    try:
        existing = retriever._request("get", f"/_alias/{retriever.alias}")
        old_names = list(existing)
    except httpx.HTTPStatusError as error:
        if error.response.status_code != 404:
            raise
        old_names = []
    actions = [{"remove": {"index": name, "alias": retriever.alias}} for name in old_names]
    actions.append({"add": {"index": physical, "alias": retriever.alias}})
    retriever._request("post", "/_aliases", json={"actions": actions})
    with database.session() as session:
        with session.begin():
            state = session.get(CorpusState, 1)
            if state.current_revision != revision:
                raise ValueError("SNAPSHOT_CHANGED")
            state.indexed_revision = revision
            for task in session.scalars(select(IndexSyncTask).where(IndexSyncTask.corpus_revision <= revision)):
                task.state = "SUCCEEDED"
    return revision
