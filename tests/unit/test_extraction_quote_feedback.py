"""Regression for a real repeated-OCR failure: three retries fixed one quote each."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from interview_intelligence.extraction.provider import OpenAICompatibleExtractor, QuoteGroundingError


QUOTES = ["索引优化有哪些", "我不想走联合查询 但又不想把计算压力放在mysql 有什么办法", "可以把个联合查询的结果放在缓存里面嘛"]


class Client:
    def __init__(self, payloads):
        self.payloads = list(payloads); self.calls = []
        self.chat = SimpleNamespace(completions=self)
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(content=json.dumps(self.payloads.pop(0), ensure_ascii=False)))],
            usage=SimpleNamespace(prompt_tokens=80, completion_tokens=40), model="fixture")


def question(key, quote, index):
    return {"local_id": key, "raw_quote": quote, "quote_index": index, "normalized_question": quote,
            "topic_l1": "数据库", "topic_l2": "SQL优化", "question_type": "KNOWLEDGE",
            "evidence_kind": "INTERVIEW_QUESTION", "confidence": .9}


def session(key, questions):
    return {"local_id": key, "session_kind": "SPECIFIC_ROUND", "metadata": {
        name: None for name in ("company_raw", "position_raw", "round_raw", "interview_date_raw", "publish_date_raw")},
        "questions": questions, "followups": []}


def payload(sessions):
    return {"document_kind": "INTERVIEW_REPORT", "exclusion_reason": None, "interviews": sessions}


def model(client, **kwargs):
    return OpenAICompatibleExtractor(client=client, model="fixture", sleep=lambda _: None, **kwargs)


def test_one_feedback_includes_all_repeated_quotes_from_the_real_failure_shape():
    source = "# 某次面试\n### 图片2\n" + "\n".join(QUOTES) + "\n### 图片3\n" + "\n".join(QUOTES)
    bad = payload([session("s1", [question(f"q{i}", text, None) for i, text in enumerate(QUOTES)])])
    fixed = deepcopy(bad)
    for q in fixed["interviews"][0]["questions"]: q["quote_index"] = 0
    client = Client([bad, fixed]); logs = []
    result = model(client, on_call=logs.append).extract(text=source, revision_id="immutable")
    assert len(client.calls) == 2 and len(result.interviews[0].questions) == 3
    assert len(logs[0]["quote_grounding_issues"]) == 3
    correction = client.calls[1]["messages"][-1]["content"]
    previous = client.calls[1]["messages"][-2]
    assert previous["role"] == "assistant"
    assert json.loads(previous["content"]) == bad
    assert all(text in correction for text in QUOTES)
    assert all(issue["occurrence_count"] == 2 for issue in logs[0]["quote_grounding_issues"])
    assert client.calls[0]["messages"][1]["content"] == client.calls[1]["messages"][1]["content"] == source
    assert logs[1]["quote_grounding_issues"] == []


def test_missing_quote_out_of_range_and_ambiguous_quote_are_returned_together():
    source = "# 面试\n真实问题甲\n真实问题甲\n真实问题乙"
    draft = payload([session("s1", [question("a", "真实问题甲", None),
                                     question("b", "真实问题乙", 9), question("c", "不存在的问题", None)])])
    client = Client([draft]); logs = []
    with pytest.raises(QuoteGroundingError) as caught:
        model(client, max_attempts=1, on_call=logs.append).extract(text=source, revision_id="immutable")
    errors = caught.value.issues
    assert len(errors) == 3
    assert {error["message"] for error in errors} == {"ambiguous repeated quote without quote_index",
        "quote_index exceeds source occurrences", "quote not found in immutable source"}
    assert errors[1]["occurrence_count"] == 1 and errors[2]["occurrences"] == []
    assert all(error["path"].startswith("interviews[s1].questions[") for error in errors)


def test_different_rounds_keep_independently_chosen_occurrences_without_guessing():
    source = "# 两次面试\n第一轮\n相同的具体问题\n第二轮\n相同的具体问题"
    draft = payload([session("s1", [question("q1", "相同的具体问题", 0)]),
                     session("s2", [question("q1", "相同的具体问题", 1)])])
    result = model(Client([draft])).extract(text=source, revision_id="immutable")
    assert [s.questions[0].source_spans[0].start_line for s in result.interviews] == [3, 5]
    unresolved = deepcopy(draft)
    for s in unresolved["interviews"]: s["questions"][0]["quote_index"] = None
    client = Client([unresolved] * 3); logs = []
    with pytest.raises(QuoteGroundingError):
        model(client, on_call=logs.append).extract(text=source, revision_id="immutable")
    assert len(client.calls) == 3 and all(len(log["quote_grounding_issues"]) == 2 for log in logs)
    assert all(call["status"] == "FAILED" for call in logs)


def test_all_metadata_and_followup_missing_quotes_join_the_same_preflight():
    draft = payload([session("s1", [question("q1", "真实的问题", None)])])
    draft["interviews"][0]["metadata"]["company_raw"] = "原文不存在的公司"
    draft["interviews"][0]["followups"] = [{"source_local_id":"q1", "target_local_id":"q1",
        "evidence_quote":"原文不存在的追问", "confidence":.9}]
    with pytest.raises(QuoteGroundingError) as caught:
        model(Client([draft]), max_attempts=1).extract(text="# 面试\n真实的问题", revision_id="immutable")
    assert len(caught.value.issues) == 2
    assert any("metadata.company_raw" in issue["path"] for issue in caught.value.issues)
    assert any("followups[0].evidence_quote" in issue["path"] for issue in caught.value.issues)


def test_empty_metadata_does_not_hide_other_quote_errors_until_another_retry():
    draft = payload([session("s1", [question("q1", "原文没有这题", None)])])
    draft["interviews"][0]["metadata"]["company_raw"] = ""
    with pytest.raises(QuoteGroundingError) as caught:
        model(Client([draft]), max_attempts=1).extract(text="# 面试记录", revision_id="immutable")
    assert len(caught.value.issues) == 2
    assert all(issue["occurrence_count"] == 0 for issue in caught.value.issues)


def test_full_feedback_is_not_cut_to_the_telemetry_error_detail_limit():
    quote = "一段需要完整定位的原文" * 30
    source = "# 面试\n" + quote + "\n" + quote
    bad = payload([session("s1", [question(f"q{i}", quote, None) for i in range(6)])])
    fixed = deepcopy(bad)
    for q in fixed["interviews"][0]["questions"]: q["quote_index"] = 0
    client = Client([bad, fixed]); logs = []
    model(client, on_call=logs.append).extract(text=source, revision_id="immutable")
    assert len(logs[0]["error_detail"]) <= 1000
    assert "questions[q5].raw_quote" in client.calls[1]["messages"][-1]["content"]
    assert len(logs[0]["quote_grounding_issues"]) == 6
    assert model(Client([])).cache_configuration["publication_validation"] == "quote_grounding_transcript_feedback_v2"
