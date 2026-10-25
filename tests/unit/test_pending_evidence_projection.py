"""Pending semantic rows remain distinct in bounded subsequent model context."""
from copy import deepcopy
import json

from interview_intelligence.agent.harness import ContextCompiler


def semantic_rows():
    return [{"canonical_question_id": "confirmed", "canonical_text": "confirmed question",
             "occurrence_count": 3, "semantic_verification": "CONFIRMED",
             "confirmed_occurrence_count": 3, "pending_occurrence_count": 1},
            {"canonical_question_id": "pending", "canonical_text": "pending question",
             "occurrence_count": 0, "semantic_verification": "PENDING",
             "confirmed_occurrence_count": 0, "pending_occurrence_count": 2}]


def semantic_meta():
    return {"route": "SQL", "semantic_scope": {"task": "ENGINEERING_DESIGN",
                "complete": False, "accepted": 3, "pending": 3, "unresolved": 2, "missing": 1},
            "semantic_counts": {"confirmed_canonical_questions": 1,
                "pending_only_canonical_questions": 1, "pending_canonical_questions": 2,
                "selected_canonical_questions": 2, "confirmed_occurrences": 3,
                "pending_occurrences": 3, "selected_occurrences": 6},
            "counts": {"canonical_questions": 1, "occurrences": 3},
            "pending_counts": {"canonical_questions": 2, "occurrences": 3},
            "pagination": {"total": 2, "returned": 2, "page_size": 20,
                "next_cursor": "PRIVATE_SIGNED_CURSOR"}, "private_path": "PRIVATE_SOURCE_PATH"}


def test_host_completed_tools_and_page_state_preserve_pending_primitives_without_full_sources():
    rows = semantic_rows()
    for row in rows:
        row["pending_sources"] = [{"raw_question_text": "PRIVATE_PENDING_BODY" * 2000,
                                   "reason": "source needs review", "source_path": "PRIVATE_SOURCE_PATH"}]
        row["source_text"] = "PRIVATE_SOURCE_BODY" * 2000
    meta = semantic_meta()
    state = {"current_page_ids": [row["canonical_question_id"] for row in rows],
             "current_page_summary": rows, "list_request": {"cursor": "PRIVATE_STATE_CURSOR", "page_size": 20}}
    part = {"intent": "LIST", "answer": "One confirmed question; one pending-only question.",
            "facts": {"data": rows, "meta": meta}}
    original = deepcopy((state, part))

    context = ContextCompiler().compile(message="解释这些结果", today="2026-10-16",
        explicit_filters={}, preferences={}, state=state, parts=[part], default_page_size=20,
        requested_pipeline="HYBRID_RERANK")

    assert context["session"]["current_page_summary"] == semantic_rows()
    evidence = context["completed_tools"][0]
    assert evidence["data"] == semantic_rows()
    assert evidence["meta"]["semantic_scope"] == meta["semantic_scope"]
    assert evidence["meta"]["semantic_counts"] == meta["semantic_counts"]
    assert evidence["meta"]["pending_counts"] == meta["pending_counts"]
    assert evidence["meta"]["counts"] == {"canonical_questions": 1, "occurrences": 3}
    assert evidence["meta"]["pagination"]["has_next_page"] is True
    assert context["session"]["has_next_page"] is True
    encoded = json.dumps(context)
    for marker in ("PRIVATE_SIGNED_CURSOR", "PRIVATE_STATE_CURSOR", "PRIVATE_PENDING_BODY",
                   "PRIVATE_SOURCE_BODY", "PRIVATE_SOURCE_PATH", "pending_sources"):
        assert marker not in encoded
    assert (state, part) == original


def test_page_state_without_semantic_fields_keeps_the_historical_projection():
    state = {"current_page_ids": ["legacy"], "current_page_summary": [{
        "canonical_question_id": "legacy", "canonical_text": "x" * 250,
        "occurrence_count": 0, "private_path": "OMIT"}]}
    assert ContextCompiler.state_projection(state)["current_page_summary"] == [{
        "canonical_question_id": "legacy", "canonical_text": "x" * 200, "occurrence_count": 0}]
