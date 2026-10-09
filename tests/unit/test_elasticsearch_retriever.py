import pytest

from interview_intelligence.search.elasticsearch import tokenize, ElasticsearchRetriever


def test_tokenizer_preserves_english_terms_and_chinese_words():
    tokens = tokenize("Redis 的 MVCC 与缓存击穿").split()
    assert "redis" in tokens and "mvcc" in tokens
    assert any("缓存" in token for token in tokens)


class FakeClient:
    def __init__(self):
        self.calls = []

    def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if path.endswith("/_pit"):
            return Response({"id": "pit-id"})
        if path == "/_search":
            query = kwargs["json"]
            if "knn" in query:
                return Response({"hits": {"hits": [{"_id": "a", "_score": 0.9}]}})
            return Response({"hits": {"hits": [{"_id": "b", "_score": 1.0}, {"_id": "a", "_score": 0.5}]}})
        raise AssertionError(path)

    def delete(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return Response({"succeeded": True})


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class Encoder:
    dimension = 2
    version = "test:2"

    def embed(self, text):
        return [0.5, 0.5]


def test_hybrid_uses_identical_eligible_filter_and_closes_pit():
    client = FakeClient()
    retriever = ElasticsearchRetriever("http://unused", Encoder(), client=client)
    result = retriever.retrieve("Redis", ["a", "b"], "HYBRID", 2)
    assert [item["canonical_question_id"] for item in result["data"]] == ["a", "b"]
    searches = [options for path, options in client.calls if path == "/_search"]
    queries = [options["json"] for options in searches]
    assert all(options["params"]["allow_partial_search_results"] == "false" for options in searches)
    assert queries[0]["query"]["bool"]["filter"] == [{"terms": {"_id": ["a", "b"]}}]
    assert queries[1]["knn"]["filter"] == {"terms": {"_id": ["a", "b"]}}
    assert client.calls[-1][0] == "/_pit"


def test_dense_requires_encoder():
    retriever = ElasticsearchRetriever("http://unused", None, client=FakeClient())
    with pytest.raises(ValueError, match="EMBEDDING_NOT_READY"):
        retriever.retrieve("Redis", ["a"], "DENSE", 10)


def test_candidate_context_is_loaded_for_actual_rerank_pool_only():
    calls = []
    def load(ids):
        calls.append(ids)
        return {"a": {"source_context": [{"original_question": "代码题：实现缓存"}]}}
    class Reranker:
        version = "source-test"
        def rerank(self, query, candidates):
            a = next(row for row in candidates if row["canonical_question_id"] == "a")
            assert a["source_context"][0]["original_question"].startswith("代码题")
            return candidates
    retriever = ElasticsearchRetriever("http://unused", Encoder(), client=FakeClient(), reranker=Reranker())
    retriever.retrieve("缓存", ["a", "b", "outside"], "HYBRID_RERANK", 1, candidate_context_loader=load)
    assert len(calls) == 1 and set(calls[0]) == {"a", "b"}
    retriever.retrieve("缓存", ["a", "b"], "HYBRID", 1, candidate_context_loader=load)
    assert len(calls) == 1


def test_progress_emits_only_phases_that_are_actually_entered():
    stages=[]
    class CheckedEncoder(Encoder):
        def embed(self, text):
            assert stages[-1]["stage"] == "embedding"
            return super().embed(text)
    class CheckedReranker:
        version="progress-test"
        def rerank(self,query,candidates):
            assert stages[-1] == {"stage":"reranking","count":2}
            return candidates
    retriever=ElasticsearchRetriever("http://unused",CheckedEncoder(),client=FakeClient(),reranker=CheckedReranker())
    retriever.retrieve("Redis",["a","b"],"HYBRID_RERANK",2,on_progress=stages.append)
    assert [step["stage"] for step in stages] == ["embedding","retrieving","reranking"]
    stages.clear()
    retriever.retrieve("Redis",["a","b"],"BM25",2,on_progress=stages.append)
    assert stages == [{"stage":"retrieving"}]


def test_semantic_expansion_does_not_widen_relevance_target():
    class RecordingEncoder(Encoder):
        queries = []

        def embed(self, text):
            self.queries.append(text)
            return super().embed(text)

    class IntentReranker:
        version = "test-intent"
        queries = []

        def rerank(self, query, candidates):
            self.queries.append(query)
            # Only the concrete business design is relevant; general Redis is not.
            return [item for item in candidates if item["canonical_question_id"] == "b"]

    client, encoder, reranker = FakeClient(), RecordingEncoder(), IntentReranker()
    expanded = "秒杀设计 库存扣减 Redis 异步消息"
    original = "高并发秒杀业务系统怎么设计"
    retriever = ElasticsearchRetriever("http://unused", encoder, reranker=reranker, client=client)
    result = retriever.retrieve(expanded, ["a", "b"], "HYBRID_RERANK", 20, relevance_query=original)
    assert encoder.queries == [expanded]
    assert reranker.queries == [original]
    bm25 = next(options["json"] for path, options in client.calls if path == "/_search")
    assert "redis" in bm25["query"]["bool"]["must"][0]["multi_match"]["query"]
    assert result["meta"]["relevance_query"] == original
    assert [item["canonical_question_id"] for item in result["data"]] == ["b"]


def test_rerank_audit_distinguishes_page_cutoff_from_relevance_rejection():
    from interview_intelligence.search.reranker import RerankedCandidates

    class AuditedReranker:
        version = "audit-test"

        def rerank(self, query, candidates):
            return RerankedCandidates(candidates, {"query_object": "Agent", "query_focus": "记忆", "candidates": [
                {"canonical_question_id": "a", "decision": "ACCEPTED"},
                {"canonical_question_id": "b", "decision": "ACCEPTED"},
                {"canonical_question_id": "other", "decision": "BELOW_THRESHOLD"}]})

    retriever = ElasticsearchRetriever("http://unused", Encoder(), reranker=AuditedReranker(), client=FakeClient())
    result = retriever.retrieve("Agent记忆", ["a", "b"], "HYBRID_RERANK", 1)
    audit = result["meta"]["rerank_audit"]["candidates"]
    assert [item["decision"] for item in audit] == ["RETURNED", "PAGE_CUTOFF", "BELOW_THRESHOLD"]


@pytest.mark.parametrize("accepted", [True, False])
def test_partial_rerank_returns_only_verified_rows_and_preserves_invalid_audit(accepted):
    from interview_intelligence.search.reranker import RerankedCandidates

    class PartialReranker:
        version = "partial-test"
        calls = 0

        def rerank(self, query, candidates):
            self.calls += 1
            # Include the invalid row to test the retriever's partial boundary.
            return RerankedCandidates(candidates, {
                "candidate_verification_status": "PARTIAL", "invalid_candidate_count": 1,
                "candidates": [
                    {"canonical_question_id": "a", "decision": "ACCEPTED" if accepted else "BELOW_THRESHOLD",
                     "relevance_grade": 3 if accepted else 0, "quote_grounded": accepted},
                    {"canonical_question_id": "b", "decision": "INVALID_SCHEMA", "relevance_grade": 0}]})

    reranker = PartialReranker()
    client = FakeClient()
    result = ElasticsearchRetriever("http://unused", Encoder(), reranker=reranker, client=client).retrieve(
        "Agent", ["a", "b"], "HYBRID_RERANK", 15, lexical_facets=["Agent memory"])
    assert [row["canonical_question_id"] for row in result["data"]] == (["a"] if accepted else [])
    assert result["meta"]["pipeline"] == "HYBRID_RERANK"
    assert result["meta"]["rerank_status"] == "COMPLETED_PARTIAL"
    assert result["meta"]["candidate_verification_status"] == "PARTIAL"
    assert result["meta"]["invalid_candidate_count"] == 1
    assert result["meta"]["result_selection_policy"] == "top5_missing_facet_once_v1"
    assert [row["decision"] for row in result["meta"]["rerank_audit"]["candidates"]] == [
        "RETURNED" if accepted else "BELOW_THRESHOLD", "INVALID_SCHEMA"]
    assert reranker.calls == 1 and client.calls[-1][0] == "/_pit"


@pytest.mark.parametrize("grade,grounded", [(0, True), (2, False)])
def test_partial_acceptance_cannot_bypass_grade_or_quote_verification(grade, grounded):
    from interview_intelligence.search.reranker import RerankedCandidates

    class InvalidAcceptanceReranker:
        version = "partial-boundary"

        def rerank(self, query, candidates):
            return RerankedCandidates(candidates, {
                "candidate_verification_status": "PARTIAL", "invalid_candidate_count": 1,
                "candidates": [{"canonical_question_id": "a", "decision": "ACCEPTED",
                                "relevance_grade": grade, "quote_grounded": grounded},
                               {"canonical_question_id": "b", "decision": "INVALID_SCHEMA"}]})

    result = ElasticsearchRetriever("http://unused", Encoder(), reranker=InvalidAcceptanceReranker(), client=FakeClient()).retrieve(
        "Agent", ["a", "b"], "HYBRID_RERANK", 15)
    assert result["data"] == [] and result["meta"]["rerank_status"] == "COMPLETED_PARTIAL"


def test_complete_rerank_empty_set_remains_complete():
    from interview_intelligence.search.reranker import RerankedCandidates

    class CompleteReranker:
        version = "complete-test"

        def rerank(self, query, candidates):
            return RerankedCandidates([], {"candidate_verification_status": "COMPLETE", "invalid_candidate_count": 0,
                "candidates": [{"canonical_question_id": row["canonical_question_id"], "decision": "BELOW_THRESHOLD"}
                               for row in candidates]})

    result = ElasticsearchRetriever("http://unused", Encoder(), reranker=CompleteReranker(), client=FakeClient()).retrieve(
        "Agent", ["a", "b"], "HYBRID_RERANK", 15)
    assert result["data"] == [] and result["meta"]["rerank_status"] == "COMPLETED"
    assert result["meta"]["candidate_verification_status"] == "COMPLETE"
    assert result["meta"]["invalid_candidate_count"] == 0


def test_parallel_subtask_selection_covers_accepted_branch_before_redundant_general_rows():
    from interview_intelligence.search.elasticsearch import _select_reranked

    general = [{"canonical_question_id": f"framework-{i}", "relevance_grade": 3, "rerank_rank": i}
               for i in range(1, 21)]
    memory = {"canonical_question_id": "memory", "relevance_grade": 2, "rerank_rank": 21,
              "stage_ranks": {"lexical_facet_1": 8}}
    tools = {"canonical_question_id": "tools", "relevance_grade": 2, "rerank_rank": 22,
             "stage_ranks": {"lexical_facet_2": 2}}
    rows = _select_reranked([*general, memory, tools], ["Agent memory", "Agent工具执行"], 15)
    ids = [row["canonical_question_id"] for row in rows]
    assert ids[:5] == [f"framework-{i}" for i in range(1, 6)]
    assert "memory" in ids and "tools" in ids
    assert len(ids) == len(set(ids)) == 15
    assert "memory" not in [row["canonical_question_id"] for row in _select_reranked([*general, memory], [], 15)]


def test_single_focus_selection_obeys_grade_before_model_order_without_padding():
    from interview_intelligence.search.elasticsearch import _select_reranked

    rows = [{"canonical_question_id": "subtask", "relevance_grade": 2, "rerank_rank": 1},
            {"canonical_question_id": "direct", "relevance_grade": 3, "rerank_rank": 9}]
    assert [row["canonical_question_id"] for row in _select_reranked(rows, [], 20)] == ["direct", "subtask"]


def test_missing_facet_once_preserves_semantic_rank13_instead_of_rotating_all_branches():
    from interview_intelligence.search.elasticsearch import _select_reranked

    semantic = [{"canonical_question_id": f"semantic-{i}", "relevance_grade": 3, "rerank_rank": i}
                for i in range(1, 21)]
    semantic[0]["stage_ranks"] = {"lexical_facet_1": 10}
    semantic[1]["stage_ranks"] = {"lexical_facet_3": 10}
    semantic[12]["stage_ranks"] = {"lexical_facet_2": 8}
    lexical = [{"canonical_question_id": f"branch-{branch}-{rank}", "relevance_grade": 2,
                "rerank_rank": 20 + (branch - 1) * 10 + rank,
                "stage_ranks": {f"lexical_facet_{branch}": rank}}
               for branch in range(1, 4) for rank in range(1, 8)]
    rows = _select_reranked([*lexical, *reversed(semantic)], ["context", "memory", "tools"], 15)
    ids = [row["canonical_question_id"] for row in rows]
    assert ids[:5] == [f"semantic-{i}" for i in range(1, 6)]
    assert ids[5] == "branch-2-1"
    assert ids[6:] == [f"semantic-{i}" for i in range(6, 15)]
    assert "semantic-13" in ids and sum(key.startswith("branch-") for key in ids) == 1
    semantic[2]["stage_ranks"] = {"lexical_facet_2": 10}
    assert _select_reranked([*lexical, *reversed(semantic)], ["context", "memory", "tools"], 15) == semantic[:15]


def test_each_missing_facet_promotes_at_most_one_candidate_before_semantic_fill():
    from interview_intelligence.search.elasticsearch import _select_reranked

    semantic = [{"canonical_question_id": f"semantic-{i}", "relevance_grade": 3, "rerank_rank": i}
                for i in range(1, 21)]
    lexical = [{"canonical_question_id": f"branch-{branch}-{rank}", "relevance_grade": 2,
                "rerank_rank": 20 + (branch - 1) * 10 + rank,
                "stage_ranks": {f"lexical_facet_{branch}": rank}}
               for branch in range(1, 4) for rank in range(1, 11)]
    rows = _select_reranked([*semantic, *lexical], ["context", "memory", "tools"], 15)
    ids = [row["canonical_question_id"] for row in rows]
    assert ids[5:8] == [f"branch-{branch}-1" for branch in range(1, 4)]
    assert ids[8:] == [f"semantic-{i}" for i in range(6, 13)]
    assert [row["canonical_question_id"] for row in _select_reranked([*semantic, *lexical], [], 15)] == [
        f"semantic-{i}" for i in range(1, 16)]


def test_one_promoted_candidate_can_cover_multiple_missing_facets():
    from interview_intelligence.search.elasticsearch import _select_reranked

    semantic = [{"canonical_question_id": f"semantic-{i}", "relevance_grade": 3, "rerank_rank": i}
                for i in range(1, 16)]
    shared = {"canonical_question_id": "shared", "relevance_grade": 2, "rerank_rank": 16,
              "stage_ranks": {"lexical_facet_1": 1, "lexical_facet_2": 10}}
    other = {"canonical_question_id": "other", "relevance_grade": 2, "rerank_rank": 17,
             "stage_ranks": {"lexical_facet_2": 1}}
    ids = [row["canonical_question_id"] for row in _select_reranked(
        [*semantic, shared, other], ["context", "memory"], 15)]
    assert ids[5] == "shared" and "other" not in ids
    assert ids[6:] == [f"semantic-{i}" for i in range(6, 15)]
    for top_k in (0, 1, 5):
        assert _select_reranked([*semantic, shared, other], ["context", "memory"], top_k) == semantic[:top_k]


def test_facet_selection_cannot_pad_with_rejected_retrieval_candidates():
    from interview_intelligence.search.reranker import RerankedCandidates

    class RejectingReranker:
        version = "rejecting"
        calls = 0

        def rerank(self, query, candidates):
            self.calls += 1
            return RerankedCandidates([row for row in candidates if row["canonical_question_id"] == "a"], {
                "candidates": [{"canonical_question_id": "a", "decision": "ACCEPTED"},
                               {"canonical_question_id": "b", "decision": "BELOW_THRESHOLD"}]})

    class RejectedFacetClient(FakeClient):
        def post(self, path, **kwargs):
            if path == "/_search" and kwargs["json"]["size"] == 50:
                self.calls.append((path, kwargs))
                return Response({"hits": {"hits": [{"_id": "b", "_score": 1.0}]}})
            return super().post(path, **kwargs)

    reranker = RejectingReranker()
    result = ElasticsearchRetriever("http://unused", Encoder(), reranker=reranker, client=RejectedFacetClient()).retrieve(
        "Agent", ["a", "b"], "HYBRID_RERANK", 15, lexical_facets=["Agent memory"])
    assert [row["canonical_question_id"] for row in result["data"]] == ["a"]
    assert reranker.calls == 1
    assert result["meta"]["result_selection_policy"] == "top5_missing_facet_once_v1"
    assert [row["decision"] for row in result["meta"]["rerank_audit"]["candidates"]] == [
        "RETURNED", "BELOW_THRESHOLD"]


@pytest.mark.parametrize("pipeline", ["DENSE", "HYBRID_RERANK"])
def test_serial_model_waits_do_not_expire_search_snapshot(pipeline):
    class ExpiringClient(FakeClient):
        clock = 0
        expires = None

        def post(self, path, **kwargs):
            if path.endswith("/_pit"):
                self.expires = self.clock + 60
            elif path == "/_search":
                if self.clock >= self.expires:
                    raise RuntimeError("PIT_EXPIRED")
                self.expires = self.clock + 60
            return super().post(path, **kwargs)

        def delete(self, path, **kwargs):
            if self.clock >= self.expires:
                raise RuntimeError("PIT_EXPIRED")
            self.expires = None
            return super().delete(path, **kwargs)

    client = ExpiringClient()

    class SlowEncoder(Encoder):
        def embed(self, text):
            client.clock += 120  # A serial worker extraction can own the gate this long.
            return super().embed(text)

    class SlowReranker:
        version = "test-reranker"

        def rerank(self, query, candidates):
            client.clock += 120
            return candidates

    retriever = ElasticsearchRetriever("http://unused", SlowEncoder(),
                                       reranker=SlowReranker(), client=client)
    result = retriever.retrieve("Redis", ["a", "b"], pipeline, 2)
    assert result["data"]
    assert client.expires is None


def test_lexical_facets_cover_missing_parallel_subtask_without_displacing_main_top20():
    main = [f"main-{index:03}" for index in range(100)]
    branches = [[f"facet-{branch}-{index:02}" for index in range(50)] for branch in range(3)]
    missing = branches[1][7]  # Outside both main Top100 lists, rank8 in the memory branch.

    class FacetClient(FakeClient):
        def post(self, path, **kwargs):
            if path != "/_search":
                return super().post(path, **kwargs)
            self.calls.append((path, kwargs))
            body = kwargs["json"]
            if "knn" in body or body["size"] == 100:
                ids = main
            else:
                text = body["query"]["bool"]["must"][0]["multi_match"]["query"]
                ids = branches[int(text.split()[-1])]
            return Response({"hits": {"hits": [{"_id": key, "_score": 100 - rank,
                "_source": {"canonical_text": f"Agent subtask {key}"}} for rank, key in enumerate(ids)]}})

    class RecordingEncoder(Encoder):
        def __init__(self): self.calls = []
        def embed(self, text):
            self.calls.append(text)
            return super().embed(text)

    class RecordingReranker:
        version = "recording"
        def __init__(self): self.calls = []
        def rerank(self, query, candidates):
            self.calls.append((query, candidates))
            return [row for row in candidates if row["canonical_question_id"] == missing]

    client, encoder, reranker = FacetClient(), RecordingEncoder(), RecordingReranker()
    retriever = ElasticsearchRetriever("http://unused", encoder, reranker=reranker, client=client)
    eligible = [*main, *(key for branch in branches for key in branch)]
    facets = [f"Agent subtask {index}" for index in range(3)]
    result = retriever.retrieve("Agent harness", eligible, "HYBRID_RERANK", 15,
                                relevance_query="Agent runtime framework", lexical_facets=facets)
    query, pool = reranker.calls[0]
    assert query == "Agent runtime framework" and len(reranker.calls) == 1
    assert len(pool) == len({row["canonical_question_id"] for row in pool}) == 50
    assert [row["canonical_question_id"] for row in pool[:20]] == main[:20]
    assert [row["canonical_question_id"] for row in pool[20:26]] == [
        "facet-0-00", "facet-1-00", "facet-2-00", "facet-0-01", "facet-1-01", "facet-2-01"]
    assert [row["canonical_question_id"] for row in result["data"]] == [missing]
    added = result["data"][0]
    assert added["stage_ranks"] == {"lexical_facet_2": 8}
    assert "rrf_score" not in added
    assert pool[0]["rrf_score"] == pytest.approx(2 / 61)
    assert encoder.calls == ["Agent harness"]
    searches = [options for path, options in client.calls if path == "/_search"]
    assert len(searches) == 5 and sum(options["json"]["size"] == 50 for options in searches) == 3
    for options in searches:
        body = options["json"]
        assert body["pit"]["id"] == "pit-id"
        assert options["params"]["allow_partial_search_results"] == "false"
        assert (body["knn"]["filter"] if "knn" in body else body["query"]["bool"]["filter"][0]) == {
            "terms": {"_id": eligible}}
    assert client.calls[-1] == ("/_pit", {"json": {"id": "pit-id"}})


def test_lexical_facet_pool_skips_duplicates_and_backfills_main_then_remaining_branches():
    from interview_intelligence.search.elasticsearch import _facet_candidate_pool
    from interview_intelligence.search.service import rrf

    main = rrf([(f"m{index:02}", 100 - index) for index in range(70)])
    branch = [("m00", 200), *( (f"b{index:02}", 100 - index) for index in range(50))]
    pool = _facet_candidate_pool(main, [branch])
    assert [row["canonical_question_id"] for row in pool[:20]] == [f"m{index:02}" for index in range(20)]
    assert [row["canonical_question_id"] for row in pool[20:30]] == [f"b{index:02}" for index in range(10)]
    assert [row["canonical_question_id"] for row in pool[30:]] == [f"m{index:02}" for index in range(20, 40)]
    assert pool[0]["stage_ranks"] == {"lexical_facet_1": 1}
    sparse = _facet_candidate_pool(main[:3], [branch])
    assert len(sparse) == len({row["canonical_question_id"] for row in sparse}) == 50
    assert sparse[-1]["canonical_question_id"] == "b46"


@pytest.mark.parametrize("pipeline", ["BM25", "DENSE"])
def test_raw_pipeline_ignores_lexical_facets_without_extra_search(pipeline):
    client = FakeClient()
    retriever = ElasticsearchRetriever("http://unused", Encoder(), client=client)
    result = retriever.retrieve("Redis", ["a", "b"], pipeline, 2, lexical_facets=["memory"])
    assert len([path for path, _ in client.calls if path == "/_search"]) == 1
    assert result["meta"]["lexical_facets_ignored"] is True


def test_empty_facets_preserve_original_result_and_search_calls_exactly():
    first_client, second_client = FakeClient(), FakeClient()
    first = ElasticsearchRetriever("http://unused", Encoder(), client=first_client)
    second = ElasticsearchRetriever("http://unused", Encoder(), client=second_client)
    assert first.retrieve("Redis", ["a", "b"], "HYBRID", 2) == second.retrieve(
        "Redis", ["a", "b"], "HYBRID", 2, lexical_facets=[])
    assert first_client.calls == second_client.calls


def test_failed_lexical_branch_closes_pit_and_cannot_return_partial_candidate_pool():
    import httpx

    class FailedBranch(FakeClient):
        def post(self, path, **kwargs):
            if path == "/_search" and kwargs["json"]["size"] == 50:
                self.calls.append((path, kwargs))
                raise httpx.ReadTimeout("facet search failed")
            return super().post(path, **kwargs)

    client = FailedBranch()
    retriever = ElasticsearchRetriever("http://unused", Encoder(), client=client)
    with pytest.raises(ValueError, match="LEXICAL_FACET_RETRIEVAL_FAILED"):
        retriever.retrieve("Agent", ["a", "b"], "HYBRID", 20, lexical_facets=["memory"])
    assert client.calls[-1] == ("/_pit", {"json": {"id": "pit-id"}})


def test_required_task_verification_failure_cannot_fall_back_to_unverified_hybrid():
    import httpx

    class RequiredVerifier:
        version = "task-verification-test"
        fail_closed = True

        def rerank(self, query, candidates):
            raise httpx.ReadTimeout("task verification unavailable")

    client = FakeClient()
    retriever = ElasticsearchRetriever("http://unused", Encoder(), reranker=RequiredVerifier(), client=client)
    with pytest.raises(ValueError, match="REQUIRED_RELEVANCE_VERIFICATION_FAILED"):
        retriever.retrieve("独立系统设计题", ["a", "b"], "HYBRID_RERANK", 20)
    assert client.calls[-1] == ("/_pit", {"json": {"id": "pit-id"}})


@pytest.mark.parametrize("facets", [[" ", "memory"], ["x" * 151], ["a", "b", "c", "d"]])
def test_invalid_lexical_facets_fail_before_any_model_or_es_request(facets):
    client = FakeClient()
    retriever = ElasticsearchRetriever("http://unused", Encoder(), client=client)
    with pytest.raises(ValueError, match="invalid lexical facets"):
        retriever.retrieve("Agent", ["a"], "HYBRID", 20, lexical_facets=facets)
    assert not client.calls


@pytest.mark.parametrize("facets", [[], ["branch 0", "branch 1", "branch 2"]])
def test_soft_type_pool_preserves_broad_projects_and_same_query_preferred_candidates(facets):
    from interview_intelligence.search.reranker import RerankedCandidates
    main, preferred = [f"broad-{i:03}" for i in range(100)], [f"preferred-{i:03}" for i in range(60)]
    branches = [[f"facet-{index}-{i:03}" for i in range(50)] for index in range(3)]

    class Client(FakeClient):
        def post(self, path, **kwargs):
            if path != "/_search":
                return super().post(path, **kwargs)
            self.calls.append((path, kwargs))
            body = kwargs["json"]
            filters = body.get("knn", {}).get("filter") or body.get("query", {}).get("bool", {}).get("filter")
            is_preferred = isinstance(filters, dict) and "bool" in filters or isinstance(filters, list) and len(filters) == 2
            ids = preferred if is_preferred else branches[int(body["query"]["bool"]["must"][0]["multi_match"]["query"].split()[-1])] if body["size"] == 50 else main
            return Response({"hits": {"hits": [{"_id": qid, "_score": 100 - rank,
                "_source": {"canonical_text": "business PROJECT " + qid}} for rank, qid in enumerate(ids)]}})

    class CountingEncoder(Encoder):
        calls = 0
        def embed(self, query):
            self.calls += 1
            return super().embed(query)

    class Reranker:
        version = "pool-test"
        calls = []
        def rerank(self, query, candidates):
            self.calls.append((query, candidates))
            return RerankedCandidates([{**row, "relevance_grade": 3, "rerank_rank": rank}
                for rank, row in enumerate(candidates, 1)], {
                "candidate_verification_status": "COMPLETE", "invalid_candidate_count": 0,
                "candidates": [{"canonical_question_id": row["canonical_question_id"], "decision": "ACCEPTED"}
                               for row in candidates]})

    client, encoder, reranker = Client(), CountingEncoder(), Reranker()
    eligible = main + preferred + [qid for branch in branches for qid in branch]
    result = ElasticsearchRetriever("http://unused", encoder, reranker=reranker, client=client).retrieve(
        "Agent application design", eligible, "HYBRID_RERANK", 50, relevance_query="original broad scene",
        lexical_facets=facets, preferred_question_type="SYSTEM_DESIGN", preferred_eligible_ids=preferred)
    query, pool = reranker.calls[0]
    assert query == "original broad scene" and len(reranker.calls) == 1 and encoder.calls == 1
    assert len(pool) == len({row["canonical_question_id"] for row in pool}) == 50
    reserved, limit = (10 if facets else 20), 30
    assert [row["canonical_question_id"] for row in pool[:reserved]] == main[:reserved]
    assert [row["canonical_question_id"] for row in pool[reserved:reserved + limit]] == preferred[:limit]
    if facets:
        assert [row["canonical_question_id"] for row in pool[40:46]] == [branches[b][i] for i in range(2) for b in range(3)]
    added = pool[reserved + 7]
    assert added["stage_ranks"] == {"preferred_rrf": 8, "preferred_stage_1": 8, "preferred_stage_2": 8}
    assert added["preferred_rrf_score"] == pytest.approx(3 / 68) and "rrf_score" not in added
    audit = result["meta"]["rerank_audit"]["candidates"][reserved + 7]
    assert audit["retrieval_provenance"]["stage_ranks"] == added["stage_ranks"]
    assert result["meta"]["question_type_preference"]["preferred_new_limit"] == limit
    searches = [options for path, options in client.calls if path == "/_search"]
    assert len(searches) == 4 + len(facets)
    assert all(options["json"]["pit"]["id"] == "pit-id" and options["params"]["allow_partial_search_results"] == "false"
               for options in searches)
    assert searches[-2]["json"]["query"]["bool"]["filter"] == [
        {"terms": {"_id": eligible}}, {"terms": {"_id": sorted(preferred)}}]
    assert searches[-1]["json"]["knn"]["filter"]["bool"]["filter"] == searches[-2]["json"]["query"]["bool"]["filter"]
    assert searches[-1]["json"]["knn"]["query_vector"] == searches[-3]["json"]["knn"]["query_vector"]
    assert searches[-2]["json"]["query"]["bool"]["must"] == searches[0]["json"]["query"]["bool"]["must"]
    assert client.calls[-1][0] == "/_pit"


def test_preferred_pool_deduplicates_overlaps_and_backfills_broad():
    from interview_intelligence.search.elasticsearch import _preferred_candidate_pool
    from interview_intelligence.search.service import rrf
    broad = rrf([(f"m{i:02}", 100 - i) for i in range(70)])
    preferred = rrf([("m00", 200), ("extra", 100)])
    pool = _preferred_candidate_pool(broad, preferred, [[("m00", 99), ("facet", 90)]])
    assert len(pool) == len({row["canonical_question_id"] for row in pool}) == 50
    assert pool[10]["canonical_question_id"] == "extra" and pool[11]["canonical_question_id"] == "facet"
    assert pool[12]["canonical_question_id"] == "m10" and pool[0]["stage_ranks"]["preferred_rrf"] == 1


@pytest.mark.parametrize("pipeline", ["BM25", "DENSE"])
def test_explicit_raw_pipeline_ignores_soft_type_branch(pipeline):
    client = FakeClient()
    result = ElasticsearchRetriever("http://unused", Encoder(), client=client).retrieve(
        "Redis", ["a", "b"], pipeline, 2, preferred_question_type="SCENARIO", preferred_eligible_ids=["a"])
    assert len([path for path, _ in client.calls if path == "/_search"]) == 1
    assert result["meta"]["question_type_preference"]["ignored_reason"] == "pipeline"


def test_empty_preferred_scope_preserves_original_calls_and_pool():
    old, new = FakeClient(), FakeClient()
    first = ElasticsearchRetriever("http://unused", Encoder(), client=old).retrieve("Redis", ["a", "b"], "HYBRID", 2)
    second = ElasticsearchRetriever("http://unused", Encoder(), client=new).retrieve(
        "Redis", ["a", "b"], "HYBRID", 2, preferred_question_type="SCENARIO", preferred_eligible_ids=[])
    assert first["data"] == second["data"] and old.calls == new.calls
    assert second["meta"]["question_type_preference"]["applied"] is False


@pytest.mark.parametrize("stage", ["bm25", "dense", "pit_close"])
def test_preferred_branch_failure_closes_pit_and_never_calls_reranker(stage):
    import httpx
    class FailedPreferred(FakeClient):
        def post(self, path, **kwargs):
            if path == "/_search" and len(kwargs["json"].get("query", {}).get("bool", {}).get("filter", [])) == 2:
                if stage == "bm25":
                    raise httpx.ReadTimeout("preferred branch failed")
                self.calls.append((path, kwargs))
                return Response({"hits": {"hits": [{"_id": "a", "_score": 1.0}]}})
            if (path == "/_search" and isinstance(kwargs["json"].get("knn", {}).get("filter"), dict)
                    and "bool" in kwargs["json"]["knn"]["filter"] and stage == "dense"):
                raise httpx.ReadTimeout("preferred dense failed")
            return super().post(path, **kwargs)
        def delete(self, path, **kwargs):
            response = super().delete(path, **kwargs)
            if stage == "pit_close":
                raise httpx.ReadTimeout("PIT close failed")
            return response
    class Reranker:
        version = "must-not-run"
        def rerank(self, *args): raise AssertionError("partial pool must not reach rerank")
    client = FailedPreferred()
    with pytest.raises(ValueError, match="LEXICAL_FACET_RETRIEVAL_FAILED"):
        ElasticsearchRetriever("http://unused", Encoder(), reranker=Reranker(), client=client).retrieve(
            "Redis", ["a", "b"], "HYBRID_RERANK", 2,
            preferred_question_type="SCENARIO", preferred_eligible_ids=["a"])
    assert client.calls[-1] == ("/_pit", {"json": {"id": "pit-id"}})


def test_preferred_scope_cannot_escape_broad_scope_before_any_es_or_model_call():
    client = FakeClient()
    with pytest.raises(ValueError, match="invalid question type preference IDs"):
        ElasticsearchRetriever("http://unused", Encoder(), client=client).retrieve(
            "Redis", ["a"], "HYBRID", 2, preferred_question_type="SCENARIO", preferred_eligible_ids=["foreign"])
    assert not client.calls
