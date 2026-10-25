"""A visible unresolved occurrence must retain facts when citation spans break."""
from copy import deepcopy

import pytest

from interview_intelligence.analytics.detail import get_question_detail, list_occurrences
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import QuestionOccurrence
from test_engineering_scope_listing import engineering_corpus


RAW = "介绍以前项目使用的技术栈"


@pytest.mark.parametrize("spans", [
    None, [], 1, {"quote": RAW}, [None], [{}], [{"quote": 1}], [{"quote": " "}],
    [{"quote": RAW, "start_line": 0}],
    [{"quote": RAW, "start_line": 5, "end_line": 3}],
    [{"quote": RAW, "start_char": 0, "end_char": 1}],
    [{"quote": RAW, "start_char": True, "end_char": len(RAW)}],
    [{"quote": RAW, "revision_id": "other-source"}],
])
def test_invalid_or_missing_source_spans_preserve_detail_and_occurrence_facts(spans):
    db = engineering_corpus()
    with db.session() as session, session.begin():
        session.get(QuestionOccurrence, "o-005").source_spans = deepcopy(spans)
    with db.session() as session:
        detail = get_question_detail(session, "project", FilterSpec(company="字节"))
        occurrences = list_occurrences(session, FilterSpec(company="字节"), canonical_id="project")
    assert detail["occurrence_count"] == detail["interview_count"] == detail["source_document_count"] == 1
    assert detail["variants"] == [RAW]
    assert detail["sources"] == [] and detail["sources_total"] == 0
    assert detail["source_evidence_verified"] is False
    issue = detail["source_issues"][0]
    assert issue["raw_question"] == RAW and issue["occurrence_id"] == "o-005"
    assert issue["revision_id"] == "r-5" and issue["source_path"] == "engineering-5.md"
    assert issue["source_evidence_verified"] is False and issue["reason"]
    assert issue["status"] in {"MISSING_SOURCE_SPANS", "INVALID_SOURCE_SPANS"}
    assert not {"quote", "start_line", "end_line", "source_api_url"} & issue.keys()
    assert occurrences["total"] == 1 and len(occurrences["data"]) == 1
    item = occurrences["data"][0]
    assert item["raw_question"] == RAW and item["sources"] == []
    assert item["source_issues"] == detail["source_issues"]
    assert item["source_evidence_verified"] is False
    assert not {"quote", "start_line", "end_line", "source_api_url"} & item.keys()


def test_one_bad_span_does_not_erase_valid_same_occurrence_citations_or_hide_error():
    db = engineering_corpus()
    with db.session() as session, session.begin():
        row = session.get(QuestionOccurrence, "o-005")
        original = deepcopy(row.source_spans[0])
        row.source_spans = [None, original, {"quote": "伪引用", "revision_id": "wrong-source"}]
    with db.session() as session:
        detail = get_question_detail(session, "project", FilterSpec(company="字节"))
        occurrence = list_occurrences(session, FilterSpec(company="字节"), canonical_id="project")["data"][0]
    assert detail["sources_total"] == len(detail["sources"]) == 1
    assert detail["sources"][0]["quote"] == RAW
    assert detail["sources"][0]["source_api_url"] == "/api/sources/r-5"
    assert len(detail["source_issues"]) == 2
    assert [issue["span_index"] for issue in detail["source_issues"]] == [0, 2]
    assert detail["source_evidence_verified"] is False
    assert occurrence["quote"] == RAW and occurrence["source_api_url"] == "/api/sources/r-5"


def test_valid_existing_reference_dictionary_keeps_its_contract():
    db = engineering_corpus()
    with db.session() as session:
        detail = get_question_detail(session, "project", FilterSpec(company="字节"))
        occurrence = list_occurrences(session, FilterSpec(company="字节"), canonical_id="project")["data"][0]
    expected = {"occurrence_id": "o-005", "interview_id": "i-5", "source_document_id": "s-5",
        "revision_id": "r-5", "raw_file_hash": detail["sources"][0]["raw_file_hash"],
        "source_url": None, "start_line": 1, "end_line": 1, "quote": RAW,
        "source_api_url": "/api/sources/r-5"}
    assert detail["sources"] == [expected]
    assert {key: occurrence[key] for key in expected} == expected
    assert "source_issues" not in detail and "source_evidence_verified" not in detail
    assert "source_issues" not in occurrence


def test_empty_filtered_detail_has_no_fabricated_issue_or_source():
    db = engineering_corpus()
    with db.session() as session:
        detail = get_question_detail(session, "project", FilterSpec(company="不存在"))
        occurrences = list_occurrences(session, FilterSpec(company="不存在"), canonical_id="project")
    assert detail["occurrence_count"] == 0 and detail["sources"] == []
    assert detail["variants"] == [] and "source_issues" not in detail
    assert occurrences["total"] == 0 and occurrences["data"] == []
