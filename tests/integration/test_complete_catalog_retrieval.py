"""Complete catalog nomination is distinct from eligibility and scope proof."""
import copy
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from interview_intelligence.contracts import FilterSpec
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.providers.runtime import RequestLimits, current_limits
from interview_intelligence.relevance_scope import compile_relevance_scope
from interview_intelligence.search.catalog_selector import canonical_digest, catalog_audit_digest
from interview_intelligence.search.elasticsearch import ElasticsearchRetriever, SCHEMA_VERSION
from interview_intelligence.search.service import (
    search_questions, target_catalog_contract, target_eligibility_digest, validate_catalog_selection_audit,
)
from interview_intelligence.domain.models import CanonicalQuestion
from test_engineering_scope_listing import engineering_corpus


class Response:
    def __init__(self, value): self.value = value
    def json(self): return self.value
    def raise_for_status(self): pass


class Encoder:
    version = "test-catalog-vector-v1"
    def __init__(self): self.queries = []
    def embed_query(self, query):
        self.queries.append(query)
        return [0.4, 0.6]


def contract(rows, revision=7):
    entries = [{"canonical_question_id": identity, "text_hash": hashlib.sha256(text.encode()).hexdigest()}
        for identity, text in sorted(rows.items())]
    return {"version": "sql_canonical_catalog_v1", "count": len(entries),
        "ids_hash": target_eligibility_digest(list(rows)), "body_hash": canonical_digest(entries),
        "corpus_revision": revision, "entries": entries}


class CatalogES:
    def __init__(self, rows, broad, *, mutation=None, revision=7):
        self.rows, self.broad, self.mutation, self.revision = rows, broad, mutation, revision
        self.calls = []
    def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if path.endswith("/_pit"):
            return Response({"id": "pit-initial"})
        body = kwargs["json"]
        if body.get("track_total_hits"):
            after = body.get("search_after", [-1])[0]
            identities = sorted(self.rows)[after + 1:after + 251]
            hits = [{"_id": identity, "sort": [after + index + 1], "_source": {
                "canonical_text": self.rows[identity], "corpus_revision": self.revision,
                "schema_version": SCHEMA_VERSION, "embedding_version": Encoder.version}}
                for index, identity in enumerate(identities)]
            result = {"pit_id": "pit-renewed", "timed_out": False, "_shards": {"failed": 0},
                "hits": {"total": {"value": len(self.rows), "relation": "eq"}, "hits": hits}}
            if self.mutation:
                self.mutation(result)
            return Response(result)
        filt = body.get("knn", {}).get("filter")
        filters = filt.get("bool", {}).get("filter", []) if isinstance(filt, dict) else []
        filters = filters or body.get("query", {}).get("bool", {}).get("filter", [])
        narrowed = len(filters) > 1
        identities = sorted(self.rows)[:100] if narrowed else self.broad[:100]
        return Response({"hits": {"hits": [{"_id": identity, "_score": 100 - index,
            "_source": {"canonical_text": self.rows.get(identity, "未审计基础组件")}}
            for index, identity in enumerate(identities)]}})
    def delete(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return Response({"succeeded": True})


class SharedReranker:
    version = "source-proof-fake"
    def __init__(self, tmp_path, choose=None, fail=False):
        self.model, self.stream, self.timeout_seconds = "same-shared-model", False, 180
        self.budget, self.on_call = None, self.record
        self.call_gate = ModelCallGate(tmp_path / "shared.lock", minimum_interval_seconds=0)
        self.calls, self.callbacks, self.pool = [], [], None
        def create(**kwargs):
            self.calls.append(kwargs)
            if fail:
                raise RuntimeError("private-secret-provider-error")
            payload = json.loads(kwargs["messages"][1]["content"])
            aliases = [row["id"] for row in payload["catalog"] if row["id"] not in payload["already_reserved"]]
            picked = choose(aliases) if choose else aliases[-40:]
            return SimpleNamespace(model="revision", usage=None, choices=[SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content=json.dumps({"selected": picked})))])
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    def record(self, entry): self.callbacks.append(entry)
    def rerank(self, query, candidates, *, relevance_scope=None):
        self.pool = copy.deepcopy(candidates)
        # Nomination does not certify the business scope or source evidence.
        return [{**row, "relevance_scope_status": "PENDING", "relevance_scope_hash": relevance_scope["scope_hash"],
            "relevance_scope_reason": "业务范围待核验", "relevance_grade": 0} for row in candidates]


def setup_catalog(tmp_path, mutation=None, fail=False):
    rows = {f"q-{index:04}": f"应用交付目标{index}：设计用户操作和业务状态闭环" for index in range(320)}
    broad = [f"outside-{index}" for index in range(10)] + list(rows)[:90]
    client, encoder, ranker = CatalogES(rows, broad, mutation=mutation), Encoder(), SharedReranker(tmp_path, fail=fail)
    retriever = ElasticsearchRetriever("http://unused", encoder, reranker=ranker, client=client)
    scope = compile_relevance_scope(["列出所有工程系统设计题", "具体业务系统设计"], candidate_mode=True)
    return rows, broad, client, encoder, ranker, retriever, scope


def test_all_catalog_titles_same_pit_can_nominate_outside_ann_frontier_and_keep_broad_escape(tmp_path):
    rows, broad, client, encoder, ranker, retriever, scope = setup_catalog(tmp_path)
    expected = contract(rows)
    result = retriever.retrieve("所有工程系统设计题", broad[:10] + list(rows), "HYBRID_RERANK", 50,
        relevance_scope=scope, target_eligible_ids=list(rows), target_catalog=expected,
        candidate_context_loader=lambda ids: {})
    ids = result["meta"]["candidate_ids"]
    assert ids == broad[:10] + list(rows)[-40:] and len(set(ids)) == 50
    assert len(encoder.queries) == len(ranker.calls) == len(ranker.callbacks) == 1
    payload = json.loads(ranker.calls[0]["messages"][1]["content"])
    assert len(payload["catalog"]) == 320 and [item["title"] for item in payload["catalog"]] == list(rows.values())
    audit = result["meta"]["catalog_selection_audit"]
    assert audit["attempted"] and audit["applied"] and audit["catalog"]["complete"]
    assert audit["selected_ids"] == list(rows)[-40:]
    assert all(row["relevance_scope_status"] == "PENDING" for row in result["data"])
    assert all(row["source_context_required"] for row in ranker.pool)
    assert not any("catalog_selection_audit" in row or "catalog" in row for row in ranker.pool)
    assert not any("business_application_rrf" in row["stage_ranks"] for row in ranker.pool[10:])
    validate_catalog_selection_audit(audit, scope, broad[:10] + list(rows), list(rows), expected, ids)
    catalog_requests = [kwargs for path, kwargs in client.calls if path == "/_search" and kwargs["json"].get("track_total_hits")]
    assert len(catalog_requests) == 2
    assert catalog_requests[1]["json"]["pit"]["id"] == "pit-renewed"
    assert client.calls[-1][1]["json"]["id"] == "pit-renewed"


@pytest.mark.parametrize("mutation", [
    lambda result: result["hits"]["total"].update(relation="gte"),
    lambda result: result["hits"]["total"].update(value=319),
    lambda result: result.update(timed_out=True),
    lambda result: result["_shards"].update(failed=1),
    lambda result: result["hits"]["hits"][0]["_source"].update(corpus_revision=6),
    lambda result: result["hits"]["hits"][0]["_source"].update(canonical_text="stale canonical rewrite"),
    lambda result: result["hits"]["hits"][0]["_source"].update(schema_version="stale"),
    lambda result: result["hits"]["hits"][0]["_source"].update(embedding_version="other-model"),
    lambda result: result["hits"]["hits"].append(result["hits"]["hits"][0]),
])
def test_incomplete_or_stale_catalog_falls_back_explicitly_without_screen_or_second_embedding(tmp_path, mutation):
    rows, broad, _, encoder, ranker, retriever, scope = setup_catalog(tmp_path, mutation)
    expected = contract(rows)
    result = retriever.retrieve("工程系统设计", broad[:10] + list(rows), "HYBRID_RERANK", 50,
        relevance_scope=scope, target_eligible_ids=list(rows), target_catalog=expected)
    audit = result["meta"]["catalog_selection_audit"]
    assert not ranker.calls and len(encoder.queries) == 1
    assert not audit["attempted"] and not audit["applied"] and not audit["catalog"]["complete"]
    assert audit["reason"] == "catalog_projection_unavailable"
    assert audit["fallback"] == {"applied": True, "mode": "base_vector_goal_lexical"}
    assert result["meta"]["candidate_ids"][:10] == broad[:10]
    validate_catalog_selection_audit(audit, scope, broad[:10] + list(rows), list(rows), expected, result["meta"]["candidate_ids"])


def test_attempted_screen_failure_has_one_receipt_and_no_extra_embedding_or_retry(tmp_path):
    rows, broad, _, encoder, ranker, retriever, scope = setup_catalog(tmp_path, fail=True)
    result = retriever.retrieve("工程系统设计", broad[:10] + list(rows), "HYBRID_RERANK", 50,
        relevance_scope=scope, target_eligible_ids=list(rows), target_catalog=contract(rows))
    audit = result["meta"]["catalog_selection_audit"]
    assert len(encoder.queries) == len(ranker.calls) == len(ranker.callbacks) == 1
    assert audit["attempted"] and not audit["applied"] and audit["selected_ids"] == []
    assert audit["provider"]["status"] == "FAILED" and audit["fallback"]["applied"]
    assert "private-secret" not in json.dumps(audit)


def test_selected_pool_survives_insufficient_primary_budget_as_visible_pending(tmp_path):
    rows, broad, _, encoder, ranker, retriever, scope = setup_catalog(tmp_path)
    def over_budget(*args, **kwargs): raise ValueError("QUERY_TOKEN_BUDGET_EXCEEDED")
    ranker.rerank = over_budget
    result = retriever.retrieve("工程系统设计", broad[:10] + list(rows), "HYBRID_RERANK", 50,
        relevance_scope=scope, target_eligible_ids=list(rows), target_catalog=contract(rows))
    assert len(result["data"]) == 50 and len(encoder.queries) == len(ranker.calls) == 1
    assert all(row["relevance_scope_status"] == "PENDING" and row["relevance_scope_hash"] == scope["scope_hash"]
        and row["relevance_grade"] == 0 for row in result["data"])
    assert result["meta"]["rerank_status"] == "COMPLETED_PARTIAL"
    assert result["meta"]["candidate_verification_status"] == "PARTIAL"
    assert result["meta"]["rerank_audit"]["verification_failure"]["error_code"] == "QUERY_TOKEN_BUDGET_EXCEEDED"


def test_cancel_between_catalog_pages_stops_before_second_request_and_closes_pit(tmp_path):
    limits = RequestLimits(time.monotonic() + 180, max_tokens=96000)
    rows, broad, client, _, ranker, retriever, scope = setup_catalog(tmp_path,
        mutation=lambda result: limits.cancelled.set())
    token = current_limits.set(limits)
    try:
        with pytest.raises(ValueError, match="QUERY_CANCELLED"):
            retriever.retrieve("工程系统设计", broad[:10] + list(rows), "HYBRID_RERANK", 50,
                relevance_scope=scope, target_eligible_ids=list(rows), target_catalog=contract(rows))
    finally:
        current_limits.reset(token)
    catalog_calls = [kwargs for path, kwargs in client.calls if path == "/_search" and kwargs["json"].get("track_total_hits")]
    assert len(catalog_calls) == 1 and not ranker.calls
    assert client.calls[-1][0] == "/_pit"


def test_sql_same_occurrence_catalog_contract_and_service_audit_validation(tmp_path):
    db = engineering_corpus()
    from interview_intelligence.search.service import eligible_canonical_ids, engineering_target_eligibility
    scope = compile_relevance_scope(["具体业务系统设计"], candidate_mode=True)
    filters = FilterSpec(company="字节", round="FIRST")
    with db.session() as session:
        eligible = eligible_canonical_ids(session, filters)
        target, prior = engineering_target_eligibility(session, filters, eligible)
        expected = target_catalog_contract(session, target, prior)
        titles = {identity: session.get(CanonicalQuestion, identity).canonical_text for identity in target}
    client = CatalogES(titles, eligible, revision=prior["corpus_revision"])
    ranker = SharedReranker(tmp_path)
    retriever = ElasticsearchRetriever("http://unused", Encoder(), reranker=ranker, client=client)
    with db.session() as session:
        result = search_questions(session, retriever, "具体业务系统设计", filters,
            pipeline="HYBRID_RERANK", top_k=50, relevance_scope=scope)
    audit = result["meta"]["catalog_selection_audit"]
    assert set(target) == {"orders", "agent"} and expected["count"] == 2
    assert audit["catalog"]["body_hash"] == expected["body_hash"]
    assert audit["catalog"]["expected_count"] == 2 and audit["applied"]
    assert not ranker.calls  # all catalog IDs already reserved by broad recall
    assert all(row["relevance_scope_status"] == "PENDING" for row in result["data"])


def test_host_recomputes_full_prepared_wire_hash_even_if_backend_resigns_audit(tmp_path):
    db = engineering_corpus()
    scope = compile_relevance_scope(["具体业务系统设计"], candidate_mode=True)
    with db.session() as session:
        titles = {row.id: row.canonical_text for row in session.query(CanonicalQuestion).all()}
    # All five are used by the same occurrence scope's fallback/target union.
    class Tampered(ElasticsearchRetriever):
        def retrieve(self, *args, **kwargs):
            client.rows = {identity: titles[identity] for identity in kwargs["target_eligible_ids"]}
            result = super().retrieve(*args, **kwargs)
            audit = result["meta"]["catalog_selection_audit"]
            assert audit["prepared"] is not None
            audit["prepared"]["prepared_hash"] = "0" * 64
            audit["audit_hash"] = catalog_audit_digest(audit)
            return result
    client = CatalogES({}, list(titles), revision=1)
    with db.session() as session:
        from interview_intelligence.domain.models import CorpusState
        client.revision = session.get(CorpusState, 1).current_revision
    retriever = Tampered("http://unused", Encoder(), client=client, reranker=SharedReranker(tmp_path))
    with db.session() as session, pytest.raises(ValueError, match="RELEVANCE_SCOPE_NOT_APPLIED"):
        search_questions(session, retriever, "具体业务系统设计", FilterSpec(),
            pipeline="HYBRID_RERANK", top_k=50, relevance_scope=scope)


def test_catalog_audit_tampered_hashes_or_ids_rejected_after_resigning(tmp_path):
    rows, broad, _, _, _, retriever, scope = setup_catalog(tmp_path)
    expected = contract(rows)
    result = retriever.retrieve("工程系统设计", broad[:10] + list(rows), "HYBRID_RERANK", 50,
        relevance_scope=scope, target_eligible_ids=list(rows), target_catalog=expected)
    original = result["meta"]["catalog_selection_audit"]
    for mutate in [lambda audit: audit["catalog"].update(body_hash="0" * 64),
            lambda audit: audit["selected_ids"].append("outside-1"),
            lambda audit: audit.update(scope_hash="0" * 64),
            lambda audit: audit.update(attempted=False),
            lambda audit: audit["catalog"].update(complete=False)]:
        audit = copy.deepcopy(original)
        mutate(audit)
        audit["audit_hash"] = catalog_audit_digest(audit)
        with pytest.raises(ValueError, match="RELEVANCE_SCOPE_NOT_APPLIED"):
            validate_catalog_selection_audit(audit, scope, broad[:10] + list(rows), list(rows), expected, result["meta"]["candidate_ids"])
