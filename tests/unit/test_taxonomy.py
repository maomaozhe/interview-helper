import importlib

import pytest


def test_v1_taxonomy_has_only_declared_topics():
    t = importlib.import_module("interview_intelligence.taxonomy")
    taxonomy = t.load_taxonomy()
    assert taxonomy.version == "v1"
    assert taxonomy.resolve("Redis", "缓存问题") == "redis.cache_problems"
    assert taxonomy.resolve("AI", "RAG") == "ai.rag"
    with pytest.raises(ValueError):
        taxonomy.resolve("Redis", "高级缓存")
    with pytest.raises(ValueError):
        taxonomy.resolve("Java", "RAG")


def test_topic_config_has_unique_ids_and_all_levels():
    t = importlib.import_module("interview_intelligence.taxonomy")
    taxonomy = t.load_taxonomy()
    assert len(taxonomy.l1_names) == 12
    assert len(taxonomy.leaves) >= 60
    assert len(taxonomy.leaves) == len(set(taxonomy.leaves))
