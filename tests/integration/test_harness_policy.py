"""A forged model plan cannot grant writes or escape the displayed page."""
import pytest
from sqlalchemy import func, select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.domain.models import ReviewEvent
from test_analytics import seed_corpus


def displayed_page():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    run = service.begin(QueryRequest(message="题目列表", request_id="page"))
    service.execute(run, "list_questions", QuerySpec(action="LIST"))
    service.finish(run, provider="fixture")
    return db, service, run.conversation_id, run.state["current_page_ids"]


@pytest.mark.parametrize("message,status,error", [
    ("查看这些题", "MASTERED", "QUERY_TOOL_NOT_ALLOWED"),
    ("不要把这些标记为已掌握", "MASTERED", "QUERY_TOOL_NOT_ALLOWED"),
    ("把这些标记为薄弱", "MASTERED", "QUERY_WRITE_STATUS_CONFLICT"),
])
def test_forced_write_plan_creates_no_business_or_invocation_record(message, status, error):
    db, service, conversation, ids = displayed_page()
    run = service.begin(QueryRequest(message=message, request_id="forged", conversation_id=conversation))
    plan = QuerySpec(action="RECORD_REVIEW", review_items=[{
        "canonical_question_id": ids[0], "status": status, "expected_version": 0}])
    with pytest.raises(ValueError, match=error):
        service.execute(run, "record_review", plan)
    assert run.tool_calls == 0
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(ReviewEvent)) == 0
    service.fail(run)


def test_completed_read_refreshes_policy_for_a_subsequent_authorized_write():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    run = service.begin(QueryRequest(message="列出三题并标记为已掌握", request_id="compound"))
    assert "record_review" not in service.model_context(run)["tool_policy"]["allowed_tools"]
    result = service.execute(run, "list_questions", QuerySpec(action="LIST", page_size=3, final=False))
    assert "record_review" in result["facts"]["meta"]["harness"]["tool_policy"]["allowed_tools"]
    ids = run.state["current_page_ids"]
    service.execute(run, "record_review", QuerySpec(action="RECORD_REVIEW", review_items=[{
        "canonical_question_id": i, "status": "MASTERED", "expected_version": 0} for i in ids]))
    assert run.result["facts"]["meta"]["harness"]["tool_policy"]["allowed_tools"] == []
    service.finish(run, provider="fixture")
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(ReviewEvent)) == len(ids)


def test_nonexistent_next_page_is_clarified_and_never_replayed_as_a_new_list():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    run = service.begin(QueryRequest(message="下一页", request_id="no-page"))
    result = service.execute(run, "list_questions", QuerySpec(action="NEXT"))
    assert result["intent"] == "CLARIFY" and result["facts"]["data"] == []
    service.finish(run, provider="fixture")


@pytest.mark.parametrize("first_empty", [False, True])
def test_compound_write_authority_binds_first_result_even_when_empty(first_empty):
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    run = service.begin(QueryRequest(message="列出三题并标记为已掌握", request_id=f"compound-frozen-{first_empty}"))
    first = service.execute(run, "list_questions", QuerySpec(action="LIST", page_size=1,
        filters={"company": "不存在"} if first_empty else {}, final=False))
    first_ids = [row["canonical_question_id"] for row in first["facts"]["data"]]
    assert tuple(first_ids) == run.write_question_ids and run.write_target_bound
    if first_empty:
        service.execute(run, "list_questions", QuerySpec(action="LIST", page_size=1, final=False))
    else:
        service.execute(run, "list_questions", QuerySpec(action="NEXT", final=False))
    assert run.state["current_page_ids"] and run.state["current_page_ids"] != first_ids
    with pytest.raises(ValueError, match="QUERY_WRITE_SCOPE_CONFLICT"):
        service.execute(run, "record_review", QuerySpec(action="RECORD_REVIEW", review_items=[{
            "canonical_question_id": run.state["current_page_ids"][0], "status": "MASTERED"}]))
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(ReviewEvent)) == 0


def test_compound_write_recovers_the_original_first_result_authorization():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    request = QueryRequest(message="列出三题并标记为已掌握", request_id="compound-recover")
    run = service.begin(request)
    service.execute(run, "list_questions", QuerySpec(action="LIST", page_size=1, final=False))
    bound = run.write_question_ids
    service.fail(run, ValueError("QUERY_INTERRUPTED"))
    restarted = QueryService(db, signing_key="test")
    restored = restarted.begin(request)
    assert restored.write_target_bound and restored.write_question_ids == bound
    restarted.execute(restored, "record_review", QuerySpec(action="RECORD_REVIEW", review_items=[{
        "canonical_question_id": bound[0], "status": "MASTERED", "expected_version": 0}]))
    result = restarted.finish(restored, provider="fixture")
    assert result["facts"]["meta"]["completion"]["status"] == "WRITE_COMMITTED"
