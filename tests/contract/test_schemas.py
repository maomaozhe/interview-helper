import importlib

import pytest
from pydantic import ValidationError


def contracts():
    return importlib.import_module("interview_intelligence.contracts")


def test_filter_rejects_unrecognized_taxonomy_and_limit():
    c = contracts()
    with pytest.raises(ValidationError):
        c.FilterSpec(topic_l1="Redis高级问题")
    with pytest.raises(ValidationError):
        c.StatsRequest(group_by="question", limit=101)


def test_source_span_can_be_verified_against_immutable_text():
    c = contracts()
    original = "开头\nRedis为什么快？\n结束"
    start = original.index("Redis")
    span = c.SourceSpan(
        revision_id="revision-1", start_char=start,
        end_char=start + len("Redis为什么快？"),
        start_line=2, end_line=2, quote="Redis为什么快？", origin="TEXT",
    )
    assert span.matches(original)
    assert not span.matches(original.replace("为什么快", "持久化"))


def test_review_requires_one_question_identifier_and_valid_score():
    c = contracts()
    with pytest.raises(ValidationError):
        c.ReviewItem(status="WEAK")
    with pytest.raises(ValidationError):
        c.ReviewItem(canonical_question_id="q1", raw_question="重复", status="WEAK")
    with pytest.raises(ValidationError):
        c.ReviewItem(canonical_question_id="q1", status="WEAK", score=6)


def test_extraction_schema_forbids_extra_data():
    c = contracts()
    with pytest.raises(ValidationError):
        c.ExtractionResult.model_validate({"schema_version": "v1", "document_kind": "INTERVIEW_REPORT", "interviews": [], "invented": "x"})


def test_extraction_question_can_carry_controlled_classification():
    c = contracts()
    q = c.ExtractedQuestion.model_validate({
        "local_id": "q1", "raw_question": "Redis为什么快？",
        "normalized_question": "Redis为什么快？", "source_spans": [{
            "revision_id": "r1", "start_char": 0, "end_char": 9,
            "start_line": 1, "end_line": 1, "quote": "Redis为什么快？", "origin": "TEXT",
        }],
        "evidence_kind": "INTERVIEW_QUESTION", "confidence": 0.9,
        "topic_l1": "Redis", "topic_l2": "性能优化", "question_type": "PRINCIPLE",
    })
    assert q.topic_l1 == "Redis"
    assert q.question_type == c.QuestionType.PRINCIPLE
