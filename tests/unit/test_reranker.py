import json
from types import SimpleNamespace

import pytest

from interview_intelligence.search.reranker import LLMReranker


class Client:
    def __init__(self, ids, grades=None):
        self.ids = ids
        self.grades = grades or {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"rankings": [{"candidate_id": item, "relevance_grade": self.grades.get(item, 3)} for item in self.ids]})
        ))])


def test_reranker_must_return_exact_candidate_set():
    candidates = [{"canonical_question_id": "a", "canonical_text": "Redis 过期"},
                  {"canonical_question_id": "b", "canonical_text": "缓存击穿"}]
    reranker = LLMReranker(client=Client(["c1", "c0"]), model="test")
    assert [item["canonical_question_id"] for item in reranker.rerank("击穿", candidates)] == ["b", "a"]
    with pytest.raises(ValueError, match="candidate set"):
        LLMReranker(client=Client(["c1"]), model="test").rerank("击穿", candidates)


def test_reranker_does_not_present_unrelated_or_only_broadly_related_questions():
    candidates = [{"canonical_question_id": "a", "canonical_text": "OOM 排查"},
                  {"canonical_question_id": "b", "canonical_text": "JVM 类加载"},
                  {"canonical_question_id": "c", "canonical_text": "Redis 数据类型"}]
    reranker = LLMReranker(client=Client(["c0", "c1", "c2"], {"c0": 3, "c1": 1, "c2": 0}), model="test")
    rows = reranker.rerank("OOM 问法", candidates)
    assert [row["canonical_question_id"] for row in rows] == ["a"]
    assert rows[0]["relevance_grade"] == 3
    assert LLMReranker(client=Client(["c0"], {"c0": 0}), model="test").rerank("OOM", candidates[:1]) == []


@pytest.mark.parametrize("ids", [["c0", "c0"], ["c0", "unknown"]])
def test_reranker_rejects_duplicate_or_invented_ids(ids):
    candidates = [{"canonical_question_id": "a"}, {"canonical_question_id": "b"}]
    with pytest.raises(ValueError, match="candidate set"):
        LLMReranker(client=Client(ids), model="test").rerank("OOM", candidates)


@pytest.mark.parametrize("finish_reason", ["stop", None, "length"])
def test_streamed_reranker_requires_complete_output_and_keeps_usage(finish_reason):
    payload = json.dumps({"rankings": [{"candidate_id": "c0", "relevance_grade": 3}]})
    chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=part),
                 finish_reason=None)], usage=None, model="resolved-test")
              for part in [payload[:20], payload[20:]]]
    if finish_reason:
        chunks.append(SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None),
            finish_reason=finish_reason)], usage=None, model="resolved-test"))
    chunks.append(SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20),
                                  model="resolved-test"))
    class Stream:
        closed = False
        def __iter__(self):
            return iter(chunks)
        def close(self):
            self.closed = True
    response = Stream()
    requests, logs = [], []
    def create(**kwargs):
        requests.append(kwargs)
        return response
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    reranker = LLMReranker(client=client, model="test", stream=True, on_call=logs.append)
    if finish_reason == "stop":
        assert reranker.rerank("OOM", [{"canonical_question_id": "a"}])[0]["canonical_question_id"] == "a"
        assert logs[0]["input_tokens"] == 50
        assert logs[0]["model_revision"] == "resolved-test"
    else:
        with pytest.raises(ValueError, match="STREAM_INCOMPLETE|MODEL_OUTPUT_TRUNCATED"):
            reranker.rerank("OOM", [{"canonical_question_id": "a"}])
        assert logs[0]["status"] == "FAILED"
    assert requests[0]["stream"] is True
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert response.closed
