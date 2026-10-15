"""Durable agent observations: authorization, honest metrics and execution evidence."""
from datetime import timedelta

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.agent.journal import QueryJournal
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import (
    AgentConversation, AgentEvent, AgentTurn, ModelCall, PipelineRun, PipelineTask,
    ToolInvocation, create_database, now_utc,
)
from interview_intelligence.observability import AgentObservability, safe

AUTH = {"Authorization": "Bearer observation-test-admin"}


@pytest.fixture
def panel():
    database = create_database("sqlite:///:memory:")
    app = create_app(database, Settings(admin_access_token="observation-test-admin"))
    return database, TestClient(app)


def seed_turn(database, identifier="run-1", status="SUCCEEDED", user="alice", age=60, duration=10000):
    start = now_utc() - timedelta(seconds=age)
    with database.session() as session, session.begin():
        session.add(AgentConversation(id="conversation-" + identifier, user_id=user))
        session.flush()
        session.add(AgentTurn(id=identifier, conversation_id="conversation-" + identifier,
            user_id=user, request_id="request-" + identifier, message="Redis 高频题有哪些", status=status,
            payload_hash="hash", created_at=start,
            finished_at=start + timedelta(milliseconds=duration) if status != "RUNNING" else None))
    return start


def seed_call(session, **kwargs):
    call = ModelCall(operation_type="QUERY_PLAN", model="test-model", prompt_version="v-test",
        status="SUCCEEDED", latency_ms=400, retry_count=0, usage_source="PROVIDER",
        request_id="same-request", input_tokens=40, output_tokens=20, **kwargs)
    session.add(call)
    return call


def get(client, path):
    response = client.get("/api/admin/agent/" + path, headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()["data"]


@pytest.mark.parametrize("path", ["summary", "runs", "runs/QUERY/run-1", "runs/INGEST/run-1"])
def test_observations_require_separate_admin_authentication(panel, path):
    database, client = panel
    seed_turn(database)
    assert client.get("/api/admin/agent/" + path).status_code == 401
    assert client.get("/api/admin/agent/" + path, headers={"Authorization": "Bearer internal"}).status_code == 401


def test_failure_denominators_exclude_cancelled_running_queued_and_review(panel):
    database, client = panel
    for index, status in enumerate(["SUCCEEDED", "SUCCEEDED", "FAILED", "INTERRUPTED", "CANCELLED", "RUNNING"]):
        seed_turn(database, f"run-{index}", status)
    summary = get(client, "summary")
    assert summary["total"] == 6 and summary["finished"] == 4
    assert summary["failed"] == 2 and summary["failure_rate"] == .5
    assert summary["cancelled"] == 1 and summary["running"] == 1
    assert summary["p95_ms"] == 10000 and summary["duration_samples"] == 5
    empty = get(client, "summary?kind=INGEST")
    assert empty["failure_rate"] is None and empty["p95_ms"] is None


def test_time_window_search_literal_characters_and_stable_pagination(panel):
    database, client = panel
    for index in range(4):
        seed_turn(database, f"run-{index}", age=index + 5)
    seed_turn(database, "old", age=90000)
    assert get(client, "runs?page_size=2")["total"] == 4
    first = get(client, "runs?page_size=2&page=1")["items"]
    second = get(client, "runs?page_size=2&page=2")["items"]
    assert {row["id"] for row in first}.isdisjoint({row["id"] for row in second})
    assert get(client, "runs?window=all")["total"] == 5
    assert get(client, "runs?q=%25")["total"] == 0
    assert get(client, "runs?q=run-2")["items"][0]["id"] == "run-2"
    assert get(client, "runs?status=FAILED")["total"] == 0


def test_tool_failures_denials_and_unknowns_are_separate(panel):
    database, client = panel
    seed_turn(database)
    with database.session() as session, session.begin():
        for ordinal, status in enumerate(["COMPLETED", "FAILED", "CANCELLED", "STARTED", "INTERRUPTED"], 1):
            session.add(ToolInvocation(run_id="run-1", ordinal=ordinal, tool_name="search_questions",
                payload_hash="hash", arguments={}, result={}, status=status))
        session.add(AgentEvent(run_id="run-1", sequence=1, kind="tool_error",
            payload={"tool": "record_review", "action_id": None, "error_code": "QUERY_TOOL_NOT_ALLOWED"}))
    summary = get(client, "summary")
    assert summary["tool_calls"] == 6
    assert summary["tool_failure_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert next(item for item in summary["tools"] if item["name"] == "search_questions")["unknown"] == 2
    details = get(client, "runs/QUERY/run-1")
    assert len(details["tools"]) == 6
    assert details["tools"][3]["status"] == "UNKNOWN"
    assert details["tools"][3]["duration_ms"] is None


def test_model_correlation_does_not_mix_accounts_with_identical_request_ids(panel):
    database, client = panel
    seed_turn(database, "alice-run", user="alice")
    seed_turn(database, "bob-old-run", user="bob", age=90000)
    with database.session() as session, session.begin():
        seed_call(session, query_run_id="alice-run")
        seed_call(session, query_run_id="bob-old-run")
        seed_call(session)  # Unattributed legacy call must not be guessed into a task.
    assert get(client, "summary")["model_calls"] == 1
    details = get(client, "runs/QUERY/alice-run")
    assert len(details["models"]) == 1
    assert details["models"][0]["input_tokens"] == 40


def test_event_cursor_and_historical_tool_timing(panel):
    database, client = panel
    start = seed_turn(database)
    with database.session() as session, session.begin():
        session.get(AgentTurn, "run-1").event_sequence = 3
        session.add(ToolInvocation(id="tool-1", run_id="run-1", ordinal=1, tool_name="list_questions",
            payload_hash="hash", arguments={"action": "LIST"}, result={"answer": "完成"}, status="COMPLETED"))
        for sequence, kind, payload, seconds in [
            (1, "accepted", {}, 0),
            (2, "stage", {"stage": "tool", "tool": "list_questions", "action_id": "tool-1"}, 1),
            (3, "tool_result", {"tool": "list_questions"}, 2),
        ]:
            session.add(AgentEvent(run_id="run-1", sequence=sequence, kind=kind, payload=payload,
                created_at=start + timedelta(seconds=seconds)))
    first = get(client, "runs/QUERY/run-1?event_limit=2")
    assert [event["sequence"] for event in first["events"]] == [1, 2]
    assert first["has_more"] and first["next_after"] == 2
    second = get(client, "runs/QUERY/run-1?event_limit=2&after=2")
    assert [event["sequence"] for event in second["events"]] == [3]
    assert not second["has_more"]
    assert second["tools"][0]["duration_ms"] == 1000


def test_reads_do_not_recover_or_mutate_expired_runs(panel):
    database, client = panel
    seed_turn(database, status="RUNNING")
    get(client, "summary")
    get(client, "runs/QUERY/run-1")
    with database.session() as session:
        assert session.get(AgentTurn, "run-1").status == "RUNNING"
        assert not list(session.scalars(select(AgentEvent)))


def test_pipeline_details_and_union_metrics(panel):
    database, client = panel
    seed_turn(database)
    with database.session() as session, session.begin():
        session.add(PipelineRun(id="pipeline-1", pipeline_version="v1", extractor_version="v1",
            taxonomy_version="v1", embedding_version="v1", status="PARTIAL_FAILURE", failed_documents=1,
            config_snapshot={"api_key": "private-key", "paths": ["example.md"]}))
        session.flush()
        session.add(PipelineTask(run_id="pipeline-1", stage="EXTRACT", task_key="task-1",
            state="FAILED", error_code="EXTRACTION_FAILED", error_detail="provider error"))
        seed_call(session, run_id="pipeline-1")
    assert get(client, "summary")["failure_rate"] == .5
    assert get(client, "runs?kind=INGEST")["total"] == 1
    details = get(client, "runs/INGEST/pipeline-1")
    assert details["events"][0]["type"] == "EXTRACT"
    assert details["config"]["api_key"] == "[已隐藏]"
    assert details["result"]["failed_documents"] == 1


def test_failed_execution_and_denied_tool_are_durable(panel):
    database, client = panel
    service = QueryService(database, signing_key="test-signing")
    run = service.begin(QueryRequest(request_id="failed-tool", message="查询题目"))
    with pytest.raises(ValueError, match="QUERY_TOOL_NOT_ALLOWED"):
        service.execute(run, "get_review_state", QuerySpec(action="REVIEW_STATE", question_ids=["unseen-question"]))
    service.fail(run, ValueError("QUERY_TOOL_NOT_ALLOWED"))
    details = get(client, f"runs/QUERY/{run.id}")
    assert details["status"] == "FAILED"
    assert details["tools"][0]["result"]["error_code"] == "QUERY_TOOL_NOT_ALLOWED"
    assert get(client, "summary")["tool_failure_rate"] == 1


def test_provider_failure_marks_started_invocation_failed(panel, monkeypatch):
    database, client = panel

    def failing_list(*args, **kwargs):
        raise ValueError("TEST_RETRIEVER_FAILED")

    monkeypatch.setattr("interview_intelligence.agent.query_service.list_questions", failing_list)
    service = QueryService(database, signing_key="test-signing")
    run = service.begin(QueryRequest(request_id="started-tool", message="查 Redis"))
    with pytest.raises(ValueError, match="TEST_RETRIEVER_FAILED"):
        service.execute(run, "list_questions", QuerySpec(action="LIST"))
    with database.session() as session:
        invocation = session.scalar(select(ToolInvocation).where(ToolInvocation.run_id == run.id))
        assert invocation.status == "FAILED"
    details = get(client, f"runs/QUERY/{run.id}")
    assert len(details["tools"]) == 1
    assert details["tools"][0]["status"] == "FAILED"


def test_pi_schema_failure_is_recorded_before_host_execution_and_not_duplicated(panel):
    database, client = panel
    seed_turn(database)
    journal = QueryJournal(database, "alice")
    journal.event("run-1", "pi_event", {"type": "tool_execution_start", "toolName": "list_questions",
        "toolCallId": "call-1", "args": {"action": "LIST"}})
    journal.event("run-1", "pi_event", {"type": "tool_execution_end", "toolName": "list_questions",
        "toolCallId": "call-1", "isError": True, "error": "INVALID_PARAMETERS"})
    journal.event("run-1", "pi_event", {"type": "tool_execution_end", "toolName": "list_questions",
        "toolCallId": "call-1", "isError": True, "error": "INVALID_PARAMETERS"})
    details = get(client, "runs/QUERY/run-1")
    assert len(details["tools"]) == 1
    assert details["tools"][0]["result"]["error_code"] == "INVALID_PARAMETERS"
    assert get(client, "runs")["items"][0]["tool_calls"] == 1


def test_redaction_and_bounded_previews():
    result = safe({"api_key": "secret-value", "authorization": "Bearer private",
        "input_tokens": 100, "content": "Bearer abcdef sk-secret12345678",
        "nested": {"password": "password-value", "next_cursor": "signed-cursor", "thinking": "private-reasoning"}})
    assert result["input_tokens"] == 100
    assert "secret-value" not in str(result) and "private-reasoning" not in str(result)
    assert "abcdef" not in result["content"]
    assert safe(list(range(90)))[-1] == "[另外 40 项未展开]"
    assert "内容已截断" in safe("x" * 14000)


@pytest.mark.parametrize("path", ["summary?window=invalid", "runs?page=0", "runs?page_size=101",
    "runs/QUERY/missing?after=-1", "runs/QUERY/missing?event_limit=201"])
def test_invalid_paging_is_rejected(panel, path):
    _, client = panel
    assert client.get("/api/admin/agent/" + path, headers=AUTH).status_code == 422


def test_missing_run_returns_404(panel):
    _, client = panel
    assert client.get("/api/admin/agent/runs/QUERY/missing", headers=AUTH).status_code == 404
