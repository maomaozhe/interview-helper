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


def test_title_only_extraction_is_rejected_and_repaired_from_body():
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    client, completions = make_client([model_payload("# 字节一面"), model_payload()])
    calls = []
    extractor = mod.OpenAICompatibleExtractor(client=client, model="test-model", on_call=calls.append,
                                              sleep=lambda _: None)
    result = extractor.extract(text="# 字节一面\nRedis为什么快？", revision_id="r1")
    assert result.interviews[0].questions[0].raw_question == "Redis为什么快？"
    assert [c["status"] for c in calls] == ["FAILED", "SUCCEEDED"]
    assert "EXTRACTION_TITLE_IS_NOT_A_QUESTION" in completions.calls[1]["messages"][-1]["content"]


def test_publication_contract_failure_is_repaired_inside_provider_and_counted():
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    bad = json.loads(model_payload())
    bad["interviews"].append(bad["interviews"][0])
    client, completions = make_client([json.dumps(bad, ensure_ascii=False), model_payload()])
    calls = []
    extractor = mod.OpenAICompatibleExtractor(client=client, model="test-model", on_call=calls.append,
        sleep=lambda _: None)
    result = extractor.extract(text="# 字节一面\nRedis为什么快？", revision_id="r1")
    assert len(result.interviews) == 1
    assert [c["status"] for c in calls] == ["FAILED", "SUCCEEDED"]
    assert "duplicate local interview ID" in completions.calls[1]["messages"][-1]["content"]


def test_explicit_thinking_policy_changes_request_and_stage_cache_identity():
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    from interview_intelligence.ingestion.extraction_cache import extraction_config_hash
    from interview_intelligence.taxonomy import load_taxonomy
    client, completions = make_client([model_payload()])
    disabled = mod.OpenAICompatibleExtractor(client=client, model="test-model", thinking_mode="disabled")
    disabled.extract(text="# 字节一面\nRedis为什么快？", revision_id="r1")
    assert completions.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    automatic = mod.OpenAICompatibleExtractor(client=client, model="test-model")
    assert "thinking" not in automatic.cache_configuration
    assert extraction_config_hash(disabled, load_taxonomy()) != extraction_config_hash(automatic, load_taxonomy())


def test_closed_topic_id_schema_prevents_invalid_pair_and_records_existing_type_policy():
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    data = json.loads(model_payload())
    q = data["interviews"][0]["questions"][0]
    q.pop("topic_l1"); q.pop("topic_l2")
    q.update(topic_id="ai.agent", question_type="PRINCIPLE", raw_quote="Agent是什么？",
             normalized_question="Agent 是什么？")
    client, completions = make_client([json.dumps(data, ensure_ascii=False)])
    calls = []
    extractor = mod.OpenAICompatibleExtractor(client=client, model="test", topic_ids=True, on_call=calls.append)
    result = extractor.extract(text="# 字节一面\nAgent是什么？", revision_id="r1")
    question = result.interviews[0].questions[0]
    assert (question.topic_l1, question.topic_l2, question.question_type.value) == ("AI", "Agent", "AI")
    schema = completions.calls[0]["response_format"]["json_schema"]["schema"]["$defs"]["DraftQuestion"]
    assert "topic_l1" not in schema["properties"] and "topic_l2" not in schema["properties"]
    assert set(schema["properties"]["topic_id"]["enum"]) == set(extractor.taxonomy.leaves)
    assert calls[0]["type_policy_corrections"][0]["from"] == "PRINCIPLE"
    assert len(calls[0]["provider_configuration_sha256"]) == 64


def test_closed_topic_id_never_accepts_a_fabricated_topic_or_changes_project_type():
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    data = json.loads(model_payload())
    q = data["interviews"][0]["questions"][0]
    q.pop("topic_l1"); q.pop("topic_l2")
    q.update(topic_id="algorithm.not_real")
    client, _ = make_client([json.dumps(data)])
    extractor = mod.OpenAICompatibleExtractor(client=client, model="test", topic_ids=True, max_attempts=1)
    with pytest.raises(ValueError, match="unknown topic ID"):
        extractor.extract(text="# 字节一面\nRedis为什么快？", revision_id="r1")
    q.update(topic_id="ai.agent", question_type="PROJECT")
    draft = extractor.parse_draft(json.dumps(data))
    grounded = extractor._ground(draft, "# 字节一面\nRedis为什么快？", "r1")
    assert grounded.interviews[0].questions[0].question_type.value == "PROJECT"


def test_body_question_repeated_in_title_is_still_eligible_with_correct_quote_index():
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    draft = json.loads(model_payload())
    draft["interviews"][0]["metadata"] = {key: None for key in draft["interviews"][0]["metadata"]}
    draft["interviews"][0]["questions"][0]["quote_index"] = 1
    client, _ = make_client([json.dumps(draft, ensure_ascii=False)])
    extractor = mod.OpenAICompatibleExtractor(client=client, model="test-model", max_attempts=1)
    result = extractor.extract(text="# Redis为什么快？\nRedis为什么快？", revision_id="r1")
    assert result.interviews[0].questions[0].source_spans[0].start_line == 2


def test_validation_retry_explains_the_actual_grounding_error_and_preserves_source():
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    client, completions = make_client([model_payload("原文没有的问题"), model_payload()])
    logs = []
    source = "# 字节一面\nRedis为什么快？"
    extractor = mod.OpenAICompatibleExtractor(client=client, model="test-model", on_call=logs.append,
                                              sleep=lambda _: None)
    assert extractor.extract(text=source, revision_id="r1").interviews[0].questions[0].raw_question == "Redis为什么快？"
    assert "quote not found" in logs[0]["error_detail"]
    messages = completions.calls[1]["messages"]
    assert messages[1]["content"] == source
    assert "quote not found" in messages[2]["content"]
    assert "逐字" in messages[2]["content"]


def test_candidate_question_heading_cannot_be_published_as_interviewer_question():
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    client, completions = make_client([model_payload("反问"), model_payload()])
    logs = []
    extractor = mod.OpenAICompatibleExtractor(client=client, model="test-model", on_call=logs.append,
                                              sleep=lambda _: None)
    result = extractor.extract(text="# 字节一面\nRedis为什么快？\n20.反问", revision_id="r1")
    assert result.interviews[0].questions[0].raw_question == "Redis为什么快？"
    assert len(completions.calls) == 2
    assert "NON_QUESTION_HEADING" in logs[0]["error_detail"]
    assert "NON_QUESTION_HEADING" in completions.calls[1]["messages"][2]["content"]


def test_extractor_repairs_a_missing_exclusion_reason_before_acceptance():
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    missing = {"document_kind":"COMPILATION", "exclusion_reason":None, "interviews":[]}
    corrected = {**missing, "exclusion_reason":"按主题汇总多场面试题，没有具体场次。"}
    client, completions = make_client([json.dumps(missing), json.dumps(corrected, ensure_ascii=False)])
    logs = []
    extractor = mod.OpenAICompatibleExtractor(client=client, model="test-model", on_call=logs.append,
                                              sleep=lambda _: None)
    result = extractor.extract(text="我把多场面试题按主题汇总。", revision_id="r1")
    assert result.exclusion_reason == corrected["exclusion_reason"]
    assert [call["status"] for call in logs] == ["FAILED", "SUCCEEDED"]
    assert "EXCLUSION_REASON_REQUIRED" in completions.calls[1]["messages"][2]["content"]


@pytest.mark.parametrize("wrong_type", ["KNOWLEDGE", "PRINCIPLE"])
def test_extractor_repairs_ai_knowledge_task_priority_before_acceptance(wrong_type):
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    quote = "了解skill吗，说一下你对skill的认识"
    draft = json.loads(model_payload(quote))
    question = draft["interviews"][0]["questions"][0]
    question.update(normalized_question="请说一下你对 Agent skill 的认识。",
                    topic_l1="AI", topic_l2="Agent", question_type=wrong_type)
    wrong = json.dumps(draft, ensure_ascii=False)
    question["question_type"] = "AI"
    client, completions = make_client([wrong, json.dumps(draft, ensure_ascii=False)])
    logs = []
    extractor = mod.OpenAICompatibleExtractor(client=client, model="test-model", on_call=logs.append,
                                              sleep=lambda _: None)
    result = extractor.extract(text="# 字节一面\n" + quote, revision_id="r1")
    actual = result.interviews[0].questions[0]
    assert actual.question_type.value == "AI"
    assert actual.raw_question == quote
    assert [call["status"] for call in logs] == ["FAILED", "SUCCEEDED"]
    assert "AI_KNOWLEDGE_TYPE_PRIORITY" in completions.calls[1]["messages"][2]["content"]


@pytest.mark.parametrize("task_type", ["AI", "PROJECT", "SYSTEM_DESIGN", "SCENARIO", "ALGORITHM"])
def test_ai_topic_preserves_the_specific_interview_task_type(task_type):
    mod = importlib.import_module("interview_intelligence.extraction.provider")
    quote = "介绍你做的Agent系统"
    draft = json.loads(model_payload(quote))
    draft["interviews"][0]["questions"][0].update(
        normalized_question=quote, topic_l1="AI", topic_l2="Agent", question_type=task_type)
    client, completions = make_client([json.dumps(draft, ensure_ascii=False)])
    extractor = mod.OpenAICompatibleExtractor(client=client, model="test-model")
    result = extractor.extract(text="# 字节一面\n" + quote, revision_id="r1")
    assert result.interviews[0].questions[0].question_type.value == task_type
    assert len(completions.calls) == 1


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


def test_explicit_output_capacity_keeps_grounding_and_invalidates_incompatible_cache():
    from interview_intelligence.extraction.provider import OpenAICompatibleExtractor
    from interview_intelligence.ingestion.extraction_cache import extraction_config_hash
    from interview_intelligence.taxonomy import load_taxonomy
    client, completions = make_client([model_payload()])
    large = OpenAICompatibleExtractor(client=client, model="test-model", max_tokens=32768)
    result = large.extract(text="# 字节一面\nRedis为什么快？", revision_id="revision-1")
    assert result.interviews[0].questions[0].source_spans[0].matches("# 字节一面\nRedis为什么快？")
    assert completions.calls[0]["max_tokens"] == 32768
    automatic = OpenAICompatibleExtractor(client=client, model="test-model", max_tokens=None)
    assert "max_tokens" not in automatic.cache_configuration
    assert extraction_config_hash(large, load_taxonomy()) != extraction_config_hash(automatic, load_taxonomy())


@pytest.mark.parametrize("capacity", [0, -1])
def test_invalid_output_capacity_fails_before_any_request(capacity):
    from interview_intelligence.extraction.provider import OpenAICompatibleExtractor
    client, completions = make_client([])
    with pytest.raises(ValueError, match="output token limit must be positive"):
        OpenAICompatibleExtractor(client=client, model="test-model", max_tokens=capacity)
    assert completions.calls == []
