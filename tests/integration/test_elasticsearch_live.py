import os

import pytest

from interview_intelligence.search.elasticsearch import ElasticsearchRetriever, rebuild_index
from test_analytics import seed_corpus


class Encoder:
    dimension = 2
    version = "test-constant-vector:2"

    def embed(self, text):
        return [0.6, 0.8]


@pytest.mark.skipif(not os.getenv("RUN_ES_LIVE_TEST"), reason="requires local Elasticsearch")
def test_live_es_rebuild_and_all_four_stage_shapes():
    db, fast_id, persistence_id = seed_corpus()
    retriever = ElasticsearchRetriever("http://127.0.0.1:9200", Encoder(), alias="ii_live_test")
    assert rebuild_index(db, retriever) == 0
    for pipeline in ("BM25", "DENSE", "HYBRID"):
        result = retriever.retrieve("Redis持久化", [persistence_id], pipeline, 10)
        assert [item["canonical_question_id"] for item in result["data"]] == [persistence_id]
