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
