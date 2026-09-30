import json
from types import SimpleNamespace

import pytest

from interview_intelligence.search.reranker import LLMReranker


class Client:
    def __init__(self, ids):
        self.ids = ids
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"ordered_ids": self.ids})
        ))])


def test_reranker_must_return_exact_candidate_set():
    candidates = [{"canonical_question_id": "a", "canonical_text": "Redis 过期"},
                  {"canonical_question_id": "b", "canonical_text": "缓存击穿"}]
    reranker = LLMReranker(client=Client(["b", "a"]), model="test")
    assert [item["canonical_question_id"] for item in reranker.rerank("击穿", candidates)] == ["b", "a"]
    with pytest.raises(ValueError, match="candidate set"):
        LLMReranker(client=Client(["b"]), model="test").rerank("击穿", candidates)
