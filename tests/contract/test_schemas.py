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


@pytest.mark.parametrize("kind", ["COMPILATION", "TUTORIAL", "OTHER"])
@pytest.mark.parametrize("reason", [None, "", " \n\t"])
def test_excluded_document_requires_an_explanation(kind, reason):
    with pytest.raises(ValidationError, match="EXCLUSION_REASON_REQUIRED"):
        contracts().ExtractionResult(schema_version="v1", document_kind=kind,
                                     exclusion_reason=reason)


def test_exclusion_reason_does_not_force_a_real_interview_to_be_excluded():
    c = contracts()
    assert c.ExtractionResult(schema_version="v1", document_kind="INTERVIEW_REPORT").exclusion_reason is None
    result = c.ExtractionResult(schema_version="v1", document_kind="COMPILATION",
                               exclusion_reason="跨场次题目汇总，无法定位实际面试。")
    assert result.document_kind == "COMPILATION"


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


@pytest.mark.parametrize("quote", ["反问", "反问？", "反问环节：", "20.反问"])
def test_bare_candidate_heading_is_ineligible_even_with_grounded_source(quote):
    c = contracts()
    value = {"local_id": "q1", "raw_question": quote, "normalized_question": "你有什么想问的？",
        "source_spans": [{"revision_id": "r1", "start_char": 0, "end_char": len(quote),
                          "start_line": 1, "end_line": 1, "quote": quote, "origin": "TEXT"}],
        "evidence_kind": "INTERVIEW_QUESTION", "confidence": 0.95}
    with pytest.raises(ValidationError, match="NON_QUESTION_HEADING"):
        c.ExtractedQuestion.model_validate(value)
    # Excluded evidence remains representable for inspection.
    value["evidence_kind"] = "CANDIDATE_QUESTION"
    assert c.ExtractedQuestion.model_validate(value).raw_question == quote
