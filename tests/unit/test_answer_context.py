"""Answer context retains useful evidence within the model context budget."""
import json

from interview_intelligence.agent.harness import ContextCompiler, ToolPolicy


def compile_context(*, state=None, parts=()):
    return ContextCompiler().compile(message="解释这些结果", today="2026-10-08", explicit_filters={},
        preferences={}, state=state or {}, default_page_size=20, requested_pipeline="HYBRID", parts=parts)


def test_initial_policy_allows_counts_and_general_answers_without_a_displayed_page():
    policy = ToolPolicy.compile("Redis 为什么快", {})
    policy.require("get_question_count", "COUNT")
    policy.require("answer_question", "ANSWER")
    assert "record_review" not in policy.actions
    assert ToolPolicy.compile("解释一下", {}, terminal=True).actions == {}


def test_previous_answer_is_bounded_and_never_exposes_opaque_list_cursor():
    state = {"last_response": {"message": "问" * 2000, "intent": "ANSWER", "answer": "答" * 12000},
        "list_request": {"cursor": "PRIVATE_SIGNED_CURSOR", "page_size": 20},
        "current_page_ids": ["q1"]}
    context = compile_context(state=state)
    previous = context["session"]["last_response"]
    assert previous["message"] == "问" * 800
    assert previous["answer"] == "答" * 1800
    assert previous["intent"] == "ANSWER"
    assert "PRIVATE_SIGNED_CURSOR" not in json.dumps(context)
    assert state["last_response"]["answer"] == "答" * 12000


def test_tool_evidence_keeps_source_identities_and_totals_while_bounding_text():
    row = {"canonical_question_id": "q1", "canonical_text": "题" * 2000,
        "occurrence_count": 500, "interview_count": 400,
        "sources": [{"revision_id": f"revision-{index}", "start_line": 10, "end_line": 12,
            "quote": "证" * 2000, "raw_file_hash": "UNNEEDED_LARGE_METADATA"} for index in range(20)]}
    part = {"intent": "DETAILS", "answer": "读" * 3000,
        "facts": {"data": [row], "meta": {"route": "SQL", "corpus_revision": 5,
            "counts": {"canonical_questions": 1000}, "private": "UNRELATED_META"}}}
    context = compile_context(parts=[part])
    evidence = context["completed_tools"][0]
    assert evidence["answer"] == "读" * 1800
    assert evidence["data"][0]["canonical_question_id"] == "q1"
    assert evidence["data"][0]["occurrence_count"] == 500
    assert evidence["data"][0]["canonical_text"] == "题" * 400
    assert len(evidence["data"][0]["sources"]) == 2
    assert evidence["data"][0]["sources"][0] == {
        "revision_id": "revision-0", "start_line": 10, "end_line": 12, "quote": "证" * 160,
    }
    assert evidence["meta"]["counts"]["canonical_questions"] == 1000
    assert "UNRELATED_META" not in json.dumps(context)


def test_large_valid_context_compacts_excerpts_and_retains_all_identities_filters_and_totals():
    identities = [f"00000000-0000-0000-0000-{index:012d}" for index in range(100)]
    filters = {"company": "腾讯", "topic_l1": "Redis", "round": "SECOND"}
    counts = {"canonical_questions": 2452, "occurrences": 2771, "interviews": 185,
              "source_documents": 172, "known_companies": 31}

    def large_row(index):
        return {"canonical_question_id": identities[index], "canonical_text": "大题目" * 2000,
            "occurrence_count": 100 + index, "interview_count": 20,
            "sources": [{"revision_id": f"revision-{index}-{source}", "start_line": 11, "end_line": 13,
                "quote": "引" * 2000} for source in range(2)]}

    state = {"current_page_ids": identities, "filters": filters,
        "current_page_summary": [large_row(index) for index in range(10)],
        "last_response": {"message": "解释前一题", "intent": "ANSWER", "answer": "答" * 12000},
        "list_request": {"cursor": "PRIVATE_PAGE_CURSOR", "page_size": 100},
        "last_count": {"counts": counts, "filters": filters, "count_scope": "filtered_corpus"}}
    parts = [{"intent": "DETAILS", "answer": "返回五道题目的详情。",
        "facts": {"data": [large_row(index) for index in range(5)],
            "meta": {"route": "SQL", "counts": counts, "applied_filters": filters}}} for _ in range(3)]

    context = compile_context(state=state, parts=parts)
    encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert len(encoded) <= 24000
    assert context["context_contract"]["evidence_compacted"] is True
    assert context["session"]["current_page_ids"] == identities
    assert context["session"]["filters"] == filters
    assert context["session"]["last_count"]["counts"] == counts
    assert len(context["completed_tools"]) == 3
    assert all(tool["meta"]["counts"] == counts for tool in context["completed_tools"])
    assert all(tool["meta"]["applied_filters"] == filters for tool in context["completed_tools"])
    assert len(state["current_page_summary"]) == 10
    assert all(len(part["facts"]["data"]) == 5 for part in parts)
    assert state["last_response"]["answer"] == "答" * 12000


def test_completed_tool_pagination_exposes_availability_without_opaque_cursor():
    pagination = {"total": 57, "result_total": 40, "returned": 20, "offset": 0,
        "page_size": 20, "top_n": 40, "next_cursor": "PRIVATE_TOOL_CURSOR"}
    part = {"intent": "LIST", "answer": "返回 20 道题。",
        "facts": {"data": [], "meta": {"route": "SQL", "pagination": pagination}}}
    context = compile_context(state={"list_request": {"cursor": "PRIVATE_SESSION_CURSOR", "page_size": 20}}, parts=[part])
    visible = context["completed_tools"][0]["meta"]["pagination"]
    assert "next_cursor" not in visible
    assert visible == {key: value for key, value in pagination.items() if key != "next_cursor"} | {"has_next_page": True}
    assert context["session"]["has_next_page"] is True
    assert "PRIVATE_TOOL_CURSOR" not in json.dumps(context)
    assert "PRIVATE_SESSION_CURSOR" not in json.dumps(context)
    assert pagination["next_cursor"] == "PRIVATE_TOOL_CURSOR"
