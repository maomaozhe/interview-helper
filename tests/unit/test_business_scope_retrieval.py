"""Host-authorized business targets change recall without changing eligibility."""

import copy
import hashlib
import json

import httpx
import pytest

from interview_intelligence.contracts import FilterSpec
from interview_intelligence.search.elasticsearch import ElasticsearchRetriever, _business_candidate_pool
from interview_intelligence.relevance_scope import compile_relevance_scope
from interview_intelligence.search.service import (
    retrieval_rank_audit_digest, rrf, search_questions, target_eligibility_digest,
    validate_retrieval_rank_audit,
)


def business_scope(**changes):
    scope = {
        "version": "query_relevance_scope_v1", "target_unit": "BUSINESS_APPLICATION",
        "source_messages": ["列出所有工程系统设计题", "所有工程系统设计题 具体业务系统设计"],
        "target_evidence": [{"message_index": 1, "quote": "具体业务系统设计"}],
        "excluded_domains": [], "exclusion_evidence": [], "independent_only": False,
        "independence_evidence": [], "retrieval_mode": "CANDIDATES",
    }
    scope.update(changes)
    scope["scope_hash"] = hashlib.sha256(json.dumps(scope, ensure_ascii=False, sort_keys=True,
                                                   separators=(",", ":")).encode()).hexdigest()
    return scope


class Response:
    def __init__(self, body): self.body = body
    def raise_for_status(self): pass
    def json(self): return self.body


class Encoder:
    version = "test:2"
    def __init__(self): self.queries = []
    def embed_query(self, query):
        self.queries.append(query)
        return [0.6, 0.4] if "面向用户需求" in query else [0.5, 0.5]


class BusinessRecallClient:
    def __init__(self, broad, business):
        self.calls, self.broad, self.business = [], broad, business
    def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if path.endswith("/_pit"):
            return Response({"id": "same-pit"})
        body = kwargs["json"]
        target = (body["knn"]["query_vector"] == [0.6, 0.4] if "knn" in body else
                  "面向" in body["query"]["bool"]["must"][0]["multi_match"]["query"])
        ids = self.business if target else self.broad
        return Response({"hits": {"hits": [{"_id": identity, "_score": 100 - rank,
            "_source": {"canonical_text": "应用或基础组件 " + identity}}
            for rank, identity in enumerate(ids)]}})
    def delete(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return Response({"succeeded": True})


class Reranker:
    version = "scope-test"
    def __init__(self): self.calls = []
    def rerank(self, query, candidates, *, relevance_scope=None):
        self.calls.append((query, copy.deepcopy(candidates), copy.deepcopy(relevance_scope)))
        return [{**row, "relevance_grade": 3, "rerank_rank": rank}
                for rank, row in enumerate(candidates, 1)]


def test_business_target_reserves_recall_for_applications_including_ai_under_same_eligibility():
    broad = [f"generic-component-{index:03}" for index in range(100)]
    # These are arbitrary fixture identities, not a title/ID allowlist. The
    # search backend supplies target-semantic hits from any question type.
    business = ["ai-application", "business-feature", *[f"application-{index:03}" for index in range(58)]]
    eligible = broad + business
    client, encoder, reranker = BusinessRecallClient(broad, business), Encoder(), Reranker()
    scope, contexts = business_scope(), []
    def load(ids):
        contexts.append(ids)
        return {identity: {"question_type": "PROJECT", "source_context": []} for identity in ids}
    result = ElasticsearchRetriever("http://unused", encoder, client=client, reranker=reranker).retrieve(
        "高并发 Cache Redis 设计", eligible, "HYBRID_RERANK", 50,
        relevance_query="所有工程系统设计题 具体业务系统设计", relevance_scope=scope,
        candidate_context_loader=load)
    query, pool, received_scope = reranker.calls[0]
    ids = [row["canonical_question_id"] for row in pool]
    assert len(pool) == len(set(ids)) == 50
    assert ids[:10] == broad[:10] and ids[10:50] == business[:40]
    assert "ai-application" in ids and "business-feature" in ids
    assert all(row["question_type"] == "PROJECT" for row in pool)
    assert received_scope == scope and query == "所有工程系统设计题 具体业务系统设计"
    assert contexts == [ids] and len(encoder.queries) == 2
    assert "具体业务系统设计" in encoder.queries[1]
    assert all(term not in encoder.queries[1] for term in ["传统", "排除AI", "排除 AI"])
    searches = [options for path, options in client.calls if path == "/_search"]
    assert len(searches) == 4
    for options in searches:
        body = options["json"]
        assert body["pit"]["id"] == "same-pit"
        assert options["params"]["allow_partial_search_results"] == "false"
        if "knn" in body:
            assert body["knn"]["filter"] == {"terms": {"_id": eligible}}
        else:
            assert body["query"]["bool"]["filter"] == [{"terms": {"_id": eligible}}]
            assert "must_not" not in body["query"]["bool"]
    assert client.calls[-1][0] == "/_pit"
    metadata = result["meta"]
    assert metadata["relevance_scope_hash"] == scope["scope_hash"]
    assert metadata["relevance_scope"] == scope and metadata["relevance_scope_applied"] is True
    assert metadata["business_application_recall"]["selected_count"] == 40
    assert metadata["business_application_recall"]["candidate_count"] == 60
    assert metadata["business_application_recall"]["reserved_new_limit"] == 40
    assert metadata["candidate_pool_policy"] == "broad10_business40_remaining_v2"
    ai_row = pool[10]
    assert ai_row["stage_ranks"]["business_application_rrf"] == 1
    assert ai_row["stage_scores"]["business_application_stage_1"] == 100
    assert ai_row["stage_scores"]["business_application_stage_2"] == 100


def test_scope_pool_retains_overlap_evidence_and_round_robins_preference_and_facets():
    main = rrf([(f"b-{index:03}", 100 - index) for index in range(70)])
    preferred = [{"canonical_question_id": f"p-{index:03}", "stage_scores": {"preferred_stage_1": 50 - index},
                  "stage_ranks": {"preferred_rrf": index + 1}, "candidate_selection_source": "preferred"}
                 for index in range(30)]
    facets = [{"canonical_question_id": f"f-{index:03}", "stage_scores": {"lexical_facet_1": 50 - index},
               "stage_ranks": {"lexical_facet_1": index + 1}, "candidate_selection_source": "facet"}
              for index in range(10)]
    business = rrf([("b-000", 200), *[(f"app-{index:03}", 100 - index) for index in range(30)]])
    pool = _business_candidate_pool(main, [*main[:10], *preferred, *facets], business)
    assert len(pool) == len({row["canonical_question_id"] for row in pool}) == 50
    assert pool[0]["stage_ranks"]["broad_rrf"] == 1
    assert pool[0]["stage_ranks"]["business_application_rrf"] == 1
    assert [row["canonical_question_id"] for row in pool[40:44]] == ["p-000", "f-000", "p-001", "f-001"]
    assert sum(row["candidate_selection_source"] == "business_application" for row in pool) == 30


def test_business_branch_remains_open_to_project_and_ai_candidates_with_soft_type_preference():
    broad = [f"component-{index:03}" for index in range(100)]
    preferred = [f"system-design-tag-{index:03}" for index in range(40)]
    applications = [f"project-application-{index:03}" for index in range(30)]
    class PreferredClient(BusinessRecallClient):
        def post(self, path, **kwargs):
            if path == "/_search":
                body = kwargs["json"]
                filters = body.get("knn", {}).get("filter") or body.get("query", {}).get("bool", {}).get("filter")
                if isinstance(filters, dict) and "bool" in filters or isinstance(filters, list) and len(filters) == 2:
                    self.calls.append((path, kwargs))
                    return Response({"hits": {"hits": [{"_id": identity, "_score": 100 - rank}
                        for rank, identity in enumerate(preferred)]}})
            return super().post(path, **kwargs)
    client, reranker = PreferredClient(broad, applications), Reranker()
    result = ElasticsearchRetriever("http://unused", Encoder(), client=client, reranker=reranker).retrieve(
        "组件和系统设计", broad + preferred + applications, "HYBRID_RERANK", 50,
        relevance_scope=business_scope(), preferred_question_type="SYSTEM_DESIGN", preferred_eligible_ids=preferred)
    assert set(applications).issubset(result["meta"]["candidate_ids"])
    assert set(preferred[:10]).issubset(result["meta"]["candidate_ids"])
    assert result["meta"]["question_type_preference"]["applied"] is True
    assert result["meta"]["candidate_pool_policy"] == "broad10_business40_remaining_v2"
    for path, options in client.calls:
        if path == "/_search":
            body = options["json"]
            if "面向" in json.dumps(body, ensure_ascii=False) or body.get("knn", {}).get("query_vector") == [0.6, 0.4]:
                filters = body.get("knn", {}).get("filter") or body["query"]["bool"]["filter"]
                assert filters == {"terms": {"_id": broad + preferred + applications}} or filters == [
                    {"terms": {"_id": broad + preferred + applications}}]


@pytest.mark.parametrize("pipeline", ["BM25", "DENSE"])
def test_single_stage_pipelines_also_apply_business_recall_without_broadening_eligibility(pipeline):
    broad, business = [f"b-{index:03}" for index in range(60)], [f"app-{index:03}" for index in range(30)]
    client, encoder = BusinessRecallClient(broad, business), Encoder()
    result = ElasticsearchRetriever("http://unused", encoder, client=client).retrieve(
        "Cache design", broad + business, pipeline, 50, relevance_scope=business_scope())
    assert result["meta"]["candidate_count"] == len(result["data"]) == 50
    assert set(business).issubset(result["meta"]["candidate_ids"])
    assert result["meta"]["relevance_scope_applied"] is True


def test_target_branch_failure_does_not_silently_return_the_old_generic_pool():
    class FailedClient(BusinessRecallClient):
        def post(self, path, **kwargs):
            if path == "/_search" and "面向" in json.dumps(kwargs["json"], ensure_ascii=False):
                raise httpx.ReadTimeout("target branch unavailable")
            return super().post(path, **kwargs)
    client, reranker = FailedClient(["component"], ["application"]), Reranker()
    with pytest.raises(ValueError, match="BUSINESS_SCOPE_RETRIEVAL_FAILED"):
        ElasticsearchRetriever("http://unused", Encoder(), client=client, reranker=reranker).retrieve(
            "Cache design", ["component", "application"], "HYBRID_RERANK", 50,
            relevance_scope=business_scope())
    assert client.calls[-1][0] == "/_pit" and not reranker.calls


def test_unsupported_scope_backend_is_rejected_before_sql_or_retrieval(monkeypatch):
    def unexpected(*args): raise AssertionError("unsupported scope must fail before any query")
    monkeypatch.setattr("interview_intelligence.search.service.eligible_canonical_ids", unexpected)
    with pytest.raises(ValueError, match="RELEVANCE_SCOPE_UNSUPPORTED"):
        search_questions(None, object(), "具体业务系统设计", FilterSpec(), relevance_scope=business_scope())


@pytest.mark.parametrize("acknowledge", [True, False])
def test_search_service_requires_backend_scope_acknowledgement_and_keeps_eligible_set(monkeypatch, acknowledge):
    scope = business_scope()
    monkeypatch.setattr("interview_intelligence.search.service.eligible_canonical_ids", lambda *args: ["a", "b"])
    monkeypatch.setattr("interview_intelligence.search.service.get_question_detail", lambda *args: {
        "canonical_text": "业务应用", "topic_id": "AI", "question_type": "PROJECT",
        "variants": [], "occurrence_count": 1, "sources": []})
    class Retriever:
        supports_relevance_scope = True
        def retrieve(self, query, eligible, pipeline, top_k, *, relevance_scope):
            assert eligible == ["a", "b"] and relevance_scope == scope
            return {"data": [{"canonical_question_id": "a", "relevance_scope_status": "PENDING",
                "relevance_scope_reason": "业务范围证据暂未完成", "relevance_scope_hash": scope["scope_hash"]}], "meta": {
                "candidate_ids": ["a", "b"], **({"relevance_scope_hash": relevance_scope["scope_hash"],
                "relevance_scope_applied": True} if acknowledge else {})}}
    if acknowledge:
        result = search_questions(None, Retriever(), "具体业务系统设计", FilterSpec(), relevance_scope=scope)
        assert result["meta"]["eligible_count"] == 2 and result["data"][0]["topic_id"] == "AI"
        assert result["data"][0]["relevance_scope_status"] == "PENDING"
        assert result["data"][0]["relevance_scope_reason"] == "业务范围证据暂未完成"
        assert result["data"][0]["relevance_scope_hash"] == scope["scope_hash"]
        assert result["meta"]["target_eligibility"]["source"] == "backend_unsupported_wide_fallback"
        assert result["meta"]["target_eligibility"]["requested"] is True
        assert result["meta"]["target_eligibility"]["available"] is result["meta"]["target_eligibility"]["applied"] is False
    else:
        with pytest.raises(ValueError, match="RELEVANCE_SCOPE_NOT_APPLIED"):
            search_questions(None, Retriever(), "具体业务系统设计", FilterSpec(), relevance_scope=scope)


@pytest.mark.parametrize("acknowledgement", [{}, {"target_eligible_ids_applied": False},
    {"target_eligible_ids_applied": True, "target_eligible_ids_hash": "wrong"}])
def test_advertised_target_backend_cannot_silently_ignore_or_change_branch_eligibility(monkeypatch, acknowledgement):
    from interview_intelligence.search.service import target_eligibility_digest
    scope = business_scope()
    target_hash = target_eligibility_digest(["application"])
    monkeypatch.setattr("interview_intelligence.search.service.eligible_canonical_ids", lambda *args: ["application", "knowledge"])
    monkeypatch.setattr("interview_intelligence.search.service.engineering_target_eligibility", lambda *args: (
        ["application"], {"target_eligible_ids_hash": target_hash, "applied": True}))
    class Retriever:
        supports_relevance_scope = supports_target_eligibility = True
        def retrieve(self, query, eligible, pipeline, top_k, **options):
            assert eligible == ["application", "knowledge"]
            assert options["target_eligible_ids"] == ["application"]
            return {"data": [], "meta": {"relevance_scope_hash": scope["scope_hash"],
                "relevance_scope_applied": True, **acknowledgement}}
    with pytest.raises(ValueError, match="RELEVANCE_SCOPE_NOT_APPLIED"):
        search_questions(None, Retriever(), "具体业务系统设计", FilterSpec(), relevance_scope=scope)


def test_partial_verification_keeps_host_scope_pending_rows_and_their_retrieval_audit():
    from interview_intelligence.search.reranker import RerankedCandidates
    scope = business_scope()
    class PartialReranker(Reranker):
        def rerank(self, query, candidates, *, relevance_scope=None):
            assert relevance_scope == scope
            return RerankedCandidates([{**row, "relevance_grade": 0, "rerank_rank": index,
                "relevance_scope_status": "PENDING", "relevance_scope_reason": "业务目标证明尚待核验",
                "relevance_scope_hash": scope["scope_hash"]} for index, row in enumerate(candidates, 1)], {
                "candidate_verification_status": "PARTIAL", "invalid_candidate_count": len(candidates),
                "candidates": [{"canonical_question_id": row["canonical_question_id"], "decision": "INVALID_BUSINESS_EVIDENCE",
                                "relevance_grade": 0, "quote_grounded": False} for row in candidates]})
    result = ElasticsearchRetriever("http://unused", Encoder(), client=BusinessRecallClient(["generic"], ["application"]),
        reranker=PartialReranker()).retrieve("系统设计", ["generic", "application"], "HYBRID_RERANK", 50,
                                            relevance_scope=scope)
    assert {row["canonical_question_id"] for row in result["data"]} == {"generic", "application"}
    assert all(row["relevance_scope_status"] == "PENDING" for row in result["data"])
    assert result["meta"]["rerank_status"] == "COMPLETED_PARTIAL"
    assert all("retrieval_provenance" in row for row in result["meta"]["rerank_audit"]["candidates"])
    assert all(row["decision"] == "INVALID_BUSINESS_EVIDENCE" and row["returned_pending"] is True
               for row in result["meta"]["rerank_audit"]["candidates"])


def test_soft_audit_target_branch_escapes_generic_knowledge_flood_without_extra_embeddings_or_broad_filter():
    broad = [f"knowledge-{index:03}" for index in range(100)]
    applications = ["ai-business-application", *[f"business-feature-{index:03}" for index in range(44)]]
    class FilteredClient(BusinessRecallClient):
        def post(self, path, **kwargs):
            result = super().post(path, **kwargs)
            if path == "/_search":
                body = kwargs["json"]
                filters = body.get("knn", {}).get("filter") or body.get("query", {}).get("bool", {}).get("filter")
                clauses = filters.get("bool", {}).get("filter", []) if isinstance(filters, dict) else filters
                if len(clauses) == 2:
                    allowed = set(clauses[1]["terms"]["_id"])
                    result.body["hits"]["hits"] = [hit for hit in result.body["hits"]["hits"] if hit["_id"] in allowed]
            return result
    client = FilteredClient(broad, [*broad[:70], *applications])
    encoder, reranker = Encoder(), Reranker()
    eligible = broad + applications
    result = ElasticsearchRetriever("http://unused", encoder, client=client, reranker=reranker).retrieve(
        "系统设计", eligible, "HYBRID_RERANK", 50, relevance_scope=business_scope(),
        target_eligible_ids=applications, target_eligibility={"source": "current_occurrence_engineering_audit",
            "version": "engineering_recall_prior_v1", "business_membership_confirmed": False})
    pool = reranker.calls[0][1]
    assert len(pool) == len({row["canonical_question_id"] for row in pool}) == 50
    assert [row["canonical_question_id"] for row in pool[10:50]] == applications[:40]
    assert all(row["candidate_selection_source"] == "business_application" for row in pool[10:50])
    assert [row["canonical_question_id"] for row in pool[:10]] == broad[:10]
    assert len(encoder.queries) == 2
    searches = [options["json"] for path, options in client.calls if path == "/_search"]
    assert searches[0]["query"]["bool"]["filter"] == [{"terms": {"_id": eligible}}]
    assert searches[-1]["knn"]["filter"] == {"terms": {"_id": eligible}}
    assert searches[1]["query"]["bool"]["filter"][1] == {"terms": {"_id": sorted(applications)}}
    assert searches[2]["knn"]["filter"]["bool"]["filter"] == searches[1]["query"]["bool"]["filter"]
    assert result["meta"]["business_application_recall"]["scope"] == "target_eligible_ids"
    assert result["meta"]["target_eligibility_applied"] is True
    assert result["meta"]["target_eligibility"]["business_membership_confirmed"] is False


def test_target_branch_must_not_return_an_id_that_is_only_broad_eligible():
    client = BusinessRecallClient(["knowledge"], ["knowledge", "application"])
    reranker = Reranker()
    with pytest.raises(ValueError, match="BUSINESS_SCOPE_RETRIEVAL_FAILED"):
        ElasticsearchRetriever("http://unused", Encoder(), client=client, reranker=reranker).retrieve(
            "系统设计", ["knowledge", "application"], "HYBRID_RERANK", 50,
            relevance_scope=business_scope(), target_eligible_ids=["application"])
    assert not reranker.calls and client.calls[-1][0] == "/_pit"


def test_empty_reviewed_target_does_not_empty_or_promote_the_broad_candidates():
    broad = [f"knowledge-{index:03}" for index in range(55)]
    result = ElasticsearchRetriever("http://unused", Encoder(), client=BusinessRecallClient(broad, [])).retrieve(
        "系统设计", broad, "HYBRID", 50, relevance_scope=business_scope(), target_eligible_ids=[],
        target_eligibility={"source": "current_occurrence_engineering_audit", "applied": True,
            "business_membership_confirmed": False})
    assert len(result["data"]) == 50
    assert set(result["meta"]["candidate_ids"]).issubset(broad)
    assert result["meta"]["business_application_recall"]["candidate_count"] == 0
    assert result["meta"]["target_eligible_ids_applied"] is True
    assert result["meta"]["target_eligibility"]["business_membership_confirmed"] is False


@pytest.mark.parametrize("identities", [["outside"], ["application", "application"], [True], "application"])
def test_target_ids_contract_is_validated_before_any_embedding_or_es_request(identities):
    client, encoder = BusinessRecallClient(["application"], ["application"]), Encoder()
    with pytest.raises(ValueError, match="invalid target eligibility IDs"):
        ElasticsearchRetriever("http://unused", encoder, client=client).retrieve(
            "系统设计", ["application"], "HYBRID", 50, relevance_scope=business_scope(), target_eligible_ids=identities)
    assert not encoder.queries and not client.calls


def test_target_quota_counts_new_candidates_and_preserves_broad_escape_from_audit_false_negatives():
    # No title/type semantics are encoded here: an old false-negative prior
    # cannot remove this canonical from the original broad eligibility set.
    broad = [f"b-{index:03}" for index in range(70)]
    target = [*broad[:3], *[f"target-{index:03}" for index in range(60)]]
    eligible, reranker = sorted(set(broad + target)), Reranker()
    result = ElasticsearchRetriever("http://unused", Encoder(), client=BusinessRecallClient(broad, target),
        reranker=reranker).retrieve("系统设计", eligible, "HYBRID_RERANK", 50,
            relevance_scope=business_scope(), target_eligible_ids=target)
    assert result["meta"]["candidate_ids"] == [*broad[:10], *target[3:43]]
    assert result["meta"]["retrieval_rank_audit"]["summary"] == {
        "broad_ranked": 70, "target_ranked": 63, "pool_count": 50,
        "target_ranked_selected": 43, "target_ranked_outside_pool": 20}
    assert broad[9] not in target
    assert broad[9] in [row["canonical_question_id"] for row in reranker.calls[0][1]]
    assert all(row["candidate_selection_source"] == "broad_reserved" for row in reranker.calls[0][1][:10])


@pytest.mark.parametrize("messages", [None, ["只看独立工程系统设计题"], ["工程系统设计题，排除AI"]])
def test_only_user_authorized_business_target_activates_extra_branch_and_private_rank_capture(messages):
    scope = compile_relevance_scope(messages) if messages else None
    if scope:
        assert scope["target_unit"] == "ANY_ENGINEERING"
    encoder, client, reranker = Encoder(), BusinessRecallClient(["broad"], ["target"]), Reranker()
    result = ElasticsearchRetriever("http://unused", encoder, client=client, reranker=reranker).retrieve(
        "模型软改写具体业务系统设计", ["broad", "target"], "HYBRID_RERANK", 50, relevance_scope=scope)
    assert len(encoder.queries) == 1
    assert len([path for path, _ in client.calls if path == "/_search"]) == 2
    assert "retrieval_rank_audit" not in result["meta"]
    assert "business_application_recall" not in result["meta"]
    assert reranker.calls[0][2] == scope


def test_private_rank_audit_records_all_bounded_hits_and_unselected_ids_without_text():
    broad, target = [f"b-{index:03}" for index in range(200)], [f"t-{index:03}" for index in range(200)]
    class SplitClient(BusinessRecallClient):
        def post(self, path, **kwargs):
            response = super().post(path, **kwargs)
            if path == "/_search":
                body = kwargs["json"]
                is_dense = "knn" in body
                is_target = (body["knn"]["query_vector"] == [0.6, 0.4] if is_dense else
                    "面向" in body["query"]["bool"]["must"][0]["multi_match"]["query"])
                identities = target if is_target else broad
                response.body["hits"]["hits"] = [{"_id": identity, "_score": 100 - rank,
                    "_source": {"canonical_text": "private raw question text"}} for rank, identity in
                    enumerate(identities[100:] if is_dense else identities[:100])]
            return response
    scope, encoder = business_scope(), Encoder()
    result = ElasticsearchRetriever("http://unused", encoder, client=SplitClient(broad, target)).retrieve(
        "系统设计", broad + target, "HYBRID", 50, relevance_scope=scope, target_eligible_ids=target,
        target_eligibility={"source": "current_occurrence_engineering_audit", "semantic_version": "v1",
            "version": "engineering_recall_prior_v1", "semantic_scope_hash": "a" * 64,
            "corpus_revision": 1, "task_annotation_revision": 2})
    audit = result["meta"]["retrieval_rank_audit"]
    assert len(encoder.queries) == 2
    assert audit["version"] == "bounded_retrieval_rank_audit_v1"
    assert audit["exhaustive"] is False and audit["ranking_scope"] == "bounded_retrieval_hits"
    assert audit["summary"] == {"broad_ranked": 200, "target_ranked": 200, "pool_count": 50,
        "target_ranked_selected": 40, "target_ranked_outside_pool": 160}
    assert audit["scope_hash"] == scope["scope_hash"]
    assert audit["broad_eligible_ids_hash"] == target_eligibility_digest(broad + target)
    assert audit["target_eligible_ids_hash"] == target_eligibility_digest(target)
    assert audit["audit_hash"] == retrieval_rank_audit_digest(audit)
    assert all(len(audit["rankings"][branch]) == 200 for branch in ("broad", "target"))
    assert {row["canonical_question_id"] for row in audit["rankings"]["target"] if not row["selected"]} == set(target) - set(result["meta"]["candidate_ids"])
    assert audit["target_prior"]["business_membership_confirmed"] is False
    serialized = json.dumps(audit, ensure_ascii=False)
    assert all(term not in serialized for term in ("source_messages", "private raw question text", "canonical_text", "具体业务系统设计"))


@pytest.mark.parametrize("tamper", ["outside_broad", "outside_target", "duplicate_stage_rank", "raw_text", "wrong_hash", "over_bound", "wrong_summary", "false_business_proof"])
def test_rank_audit_rejects_forged_ineligible_or_unbounded_diagnostics_even_with_new_digest(monkeypatch, tamper):
    broad, target, scope = [f"b-{index:03}" for index in range(60)], [f"t-{index:03}" for index in range(60)], business_scope()
    eligible = broad + target
    prior = {"source": "current_occurrence_engineering_audit", "version": "engineering_recall_prior_v1",
        "applied": True, "target_eligible_ids_hash": target_eligibility_digest(target)}
    result = ElasticsearchRetriever("http://unused", Encoder(), client=BusinessRecallClient(broad, target)).retrieve(
        "系统设计", eligible, "HYBRID", 50, relevance_scope=scope, target_eligible_ids=target, target_eligibility=prior)
    audit = result["meta"]["retrieval_rank_audit"]
    if tamper == "outside_broad": audit["rankings"]["broad"][-1]["canonical_question_id"] = "outside-original-eligibility"
    elif tamper == "outside_target": audit["rankings"]["target"][-1]["canonical_question_id"] = broad[-1]
    elif tamper == "duplicate_stage_rank": audit["rankings"]["target"][-1]["stage_ranks"] = dict(audit["rankings"]["target"][-2]["stage_ranks"])
    elif tamper == "raw_text": audit["rankings"]["target"][-1]["canonical_text"] = "private question"
    elif tamper == "over_bound": audit["rankings"]["target"] *= 4
    elif tamper == "wrong_summary": audit["summary"]["target_ranked_outside_pool"] -= 1
    elif tamper == "false_business_proof": audit["target_prior"]["business_membership_confirmed"] = True
    audit["audit_hash"] = "wrong" if tamper == "wrong_hash" else retrieval_rank_audit_digest(audit)
    with pytest.raises(ValueError, match="RELEVANCE_SCOPE_NOT_APPLIED"):
        validate_retrieval_rank_audit(audit, scope, eligible, target, result["meta"]["candidate_ids"])
    monkeypatch.setattr("interview_intelligence.search.service.eligible_canonical_ids", lambda *args: eligible)
    monkeypatch.setattr("interview_intelligence.search.service.engineering_target_eligibility", lambda *args: (target, prior))
    def unexpected_hydration(*args): raise AssertionError("invalid private audit must fail before hydration")
    monkeypatch.setattr("interview_intelligence.search.service.get_question_detail", unexpected_hydration)
    class Retriever:
        supports_relevance_scope = supports_target_eligibility = True
        def retrieve(self, *args, **kwargs): return result
    with pytest.raises(ValueError, match="RELEVANCE_SCOPE_NOT_APPLIED"):
        search_questions(None, Retriever(), "系统设计", FilterSpec(), relevance_scope=scope)


@pytest.mark.parametrize("contexts", [{}, None, {"application": None}, {"application": {"source_context_required": False}},
    {"application": {"source_context_version": "candidate_source_context_v3", "source_context": []}}])
def test_loader_missing_or_invalid_context_cannot_downgrade_source_authority_to_legacy_canonical(contexts):
    ranker = Reranker()
    ElasticsearchRetriever("http://unused", Encoder(), client=BusinessRecallClient([], ["application"]),
        reranker=ranker).retrieve("业务应用设计", ["application"], "HYBRID_RERANK", 50,
            relevance_scope=business_scope(), candidate_context_loader=lambda ids: copy.deepcopy(contexts))
    row = ranker.calls[0][1][0]
    assert row["canonical_question_id"] == "application"
    assert row["source_context_required"] is True
    assert "source_context" not in row or row["source_context"] == []
    assert not row.get("source_context_status")  # Never manufacture a verification receipt.


def test_genuine_legacy_caller_without_source_loader_retains_existing_canonical_contract():
    ranker = Reranker()
    ElasticsearchRetriever("http://unused", Encoder(), client=BusinessRecallClient([], ["application"]),
        reranker=ranker).retrieve("业务应用设计", ["application"], "HYBRID_RERANK", 50, relevance_scope=business_scope())
    assert "source_context_required" not in ranker.calls[0][1][0]
