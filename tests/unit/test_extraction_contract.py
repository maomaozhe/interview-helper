import importlib
import json
from types import SimpleNamespace

import pytest


class FakeCompletions:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=payload))],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20),
        )


def make_client(payloads):
    completions = FakeCompletions(payloads)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def model_payload(quote="Redis为什么快？"):
    return json.dumps({
        "document_kind": "INTERVIEW_REPORT", "exclusion_reason": None,
        "interviews": [{
            "local_id": "s1", "session_kind": "SINGLE",
            "metadata": {"company_raw": "字节", "position_raw": None, "round_raw": "一面", "interview_date_raw": None, "publish_date_raw": None},
            "questions": [{
                "local_id": "q1", "raw_quote": quote, "quote_index": None,
                "normalized_question": "Redis 为什么具有较高性能？",
                "topic_l1": "Redis", "topic_l2": "性能优化",
                "question_type": "PRINCIPLE", "evidence_kind": "INTERVIEW_QUESTION",
                "confidence": 0.95,
            }],
            "followups": [],
        }],
    }, ensure_ascii=False)


def test_structured_extractor_constructs_verified_source_span():
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    client, completions = make_client([model_payload()])
    calls = []
    extractor = mod.OpenAICompatibleExtractor(client=client, model="test-model", on_call=calls.append)
    result = extractor.extract(text="# 字节一面\nRedis为什么快？", revision_id="revision-1")
    q = result.interviews[0].questions[0]
    assert q.raw_question == "Redis为什么快？"
    assert q.normalized_question == "Redis 为什么具有较高性能？"
    assert q.source_spans[0].start_line == 2
    assert q.source_spans[0].matches("# 字节一面\nRedis为什么快？")
    assert q.topic_l1 == "Redis"
    assert completions.calls[0]["response_format"]["type"] == "json_schema"
    assert completions.calls[0]["response_format"]["json_schema"]["strict"] is True
    assert calls[0]["status"] == "SUCCEEDED"


def test_extractor_retries_invalid_result_but_never_accepts_fabricated_quote():
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    client, completions = make_client([model_payload("原文没有的问题") for _ in range(3)])
    calls = []
    extractor = mod.OpenAICompatibleExtractor(client=client, model="test-model", on_call=calls.append, sleep=lambda _: None)
    with pytest.raises(ValueError, match="quote"):
        extractor.extract(text="# 字节一面\nRedis为什么快？", revision_id="revision-1")
    assert len(completions.calls) == 3
    assert [item["retry_count"] for item in calls] == [0, 1, 2]
    assert all(item["status"] == "FAILED" for item in calls)


def test_repeated_quote_without_position_is_ambiguous():
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    client, _ = make_client([model_payload() for _ in range(3)])
    extractor = mod.OpenAICompatibleExtractor(client=client, model="test-model", sleep=lambda _: None)
    with pytest.raises(ValueError, match="ambiguous"):
        extractor.extract(text="字节一面\nRedis为什么快？\nRedis为什么快？", revision_id="revision-1")


@pytest.mark.parametrize("finish_reason", ["stop", None, "length"])
def test_streamed_extraction_only_publishes_complete_grounded_json(finish_reason):
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    payload = model_payload()
    chunks = [SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=part), finish_reason=None)], usage=None, model="resolved-test")
        for part in [payload[:100], payload[100:]]]
    if finish_reason:
        chunks.append(SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content=None), finish_reason=finish_reason)], usage=None, model="resolved-test"))
    chunks.append(SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20),
                                  model="resolved-test"))

    class Stream:
        closed = False
        def __iter__(self):
            return iter(chunks)
        def close(self):
            self.closed = True

    stream = Stream()
    captured = []
    def create(**kwargs):
        captured.append(kwargs)
        return stream
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    logs = []
    extractor = mod.OpenAICompatibleExtractor(client=client, model="test-model", stream=True,
                                              max_attempts=1, on_call=logs.append)
    if finish_reason == "stop":
        result = extractor.extract(text="# 字节一面\nRedis为什么快？", revision_id="revision-1")
        assert result.interviews[0].questions[0].source_spans[0].matches("# 字节一面\nRedis为什么快？")
        assert logs[0]["input_tokens"] == 50
        assert logs[0]["model_revision"] == "resolved-test"
    else:
        with pytest.raises(ValueError, match="STREAM_INCOMPLETE|MODEL_OUTPUT_TRUNCATED"):
            extractor.extract(text="# 字节一面\nRedis为什么快？", revision_id="revision-1")
        assert logs[0]["status"] == "FAILED"
    assert captured[0]["stream"] is True
    assert captured[0]["stream_options"] == {"include_usage": True}
    assert stream.closed
