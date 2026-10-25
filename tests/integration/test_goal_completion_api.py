"""Response goals survive the public API, multi-turn scope and failure paths."""
from copy import deepcopy

from fastapi.testclient import TestClient
from sqlalchemy import select
import pytest

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import AgentTurn, AgentEvent, ToolInvocation, ReviewEvent
from test_analytics import seed_corpus


def test_public_multi_turn_goal_uses_three_reads_then_answers_and_retains_original_paging(tmp_path):
    database, _, _ = seed_corpus()
    contexts, limits = [], []

    class Planner:
        def plan(self, run, context):
            contexts.append(deepcopy(context))
            limits.append(run.limits)
            goal = {"objective": run.request.message, "response_mode": "SYNTHESIS"}
            scope = {"company": "字节", "topic_l1": "Redis"}
            if run.request.message == "列出字节 Redis 题，每页一道":
                return QuerySpec(action="LIST", filters=scope, page_size=1,
                    user_goal={**goal, "response_mode": "DIRECT_RESULT"})
            if run.request.message == "下一页":
                return QuerySpec(action="NEXT", user_goal={**goal, "response_mode": "DIRECT_RESULT"})
            plans = [
                QuerySpec(action="COUNT", filters=scope, user_goal=goal),
                QuerySpec(action="STATS", filters=scope, group_by="company", user_goal=goal),
                QuerySpec(action="LIST", filters=scope, page_size=1, user_goal=goal),
                QuerySpec(action="ANSWER", filters=scope, user_goal=goal, answer_basis="CORPUS",
                    evidence_tool_ordinals=[1, 2, 3],
                    answer_text="当前范围是2道去重题、2次提问；这个计数覆盖筛选范围，单页只展示1道。"),
            ]
            return plans[len(context["completed_tools"])]

    app = create_app(database, Settings(database_url="sqlite+pysqlite:///:memory:",
        snapshot_root=tmp_path / "snapshots"), query_planner=Planner())
    client = TestClient(app)
    first = client.post("/api/query", json={"message": "列出字节 Redis 题，每页一道", "request_id": "goal-api-list"})
    assert first.status_code == 200, first.text
    page = first.json()
    original_ids = [row["canonical_question_id"] for row in page["data"]]
    request = {"message": "核查当前列表的数量和覆盖范围，再解释统计口径", "request_id": "goal-api-analysis",
        "conversation_id": page["meta"]["conversation_id"], "expected_version": page["meta"]["conversation_version"]}
    response = client.post("/api/query", json=request)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["completion"]["status"] == "COMPLETE"
    assert body["meta"]["user_goal"]["objective"] == request["message"]
    assert body["meta"]["planning"]["spec"]["action"] == "ANSWER"
    assert [part["intent"] for part in contexts[-1]["completed_tools"]] == ["COUNT", "STATS", "LIST"]
    assert [part["ordinal"] for part in contexts[-1]["completed_tools"]] == [1, 2, 3]
    assert all(part["meta"]["applied_filters"]["company"] == "字节"
               for part in contexts[-1]["completed_tools"])
    assert len({id(value) for value in limits[1:5]}) == 1
    assert client.post("/api/query", json=request).json() == body
    with database.session() as session:
        saved = session.scalar(select(AgentTurn).where(AgentTurn.request_id == request["request_id"]))
        assert [part["intent"] for part in saved.response["facts"]["components"]] == ["COUNT", "STATS", "LIST"]
    next_page = client.post("/api/query", json={"message": "下一页", "request_id": "goal-api-next",
        "conversation_id": page["meta"]["conversation_id"], "expected_version": body["meta"]["conversation_version"]})
    assert next_page.status_code == 200, next_page.text
    assert next_page.json()["meta"]["applied_filters"]["company"] == "字节"
    assert next_page.json()["meta"]["pagination"]["offset"] == 1
    assert set(original_ids).isdisjoint(row["canonical_question_id"] for row in next_page.json()["data"])


def test_exhausted_tool_budget_keeps_evidence_but_does_not_commit_a_successful_answer(tmp_path):
    database, _, _ = seed_corpus()

    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="COUNT", user_goal={"objective": "分析题库覆盖", "response_mode": "SYNTHESIS"})

    app = create_app(database, Settings(database_url="sqlite+pysqlite:///:memory:",
        snapshot_root=tmp_path / "snapshots"), query_planner=Planner())
    response = TestClient(app).post("/api/query", json={"message": "分析题库覆盖", "request_id": "goal-budget"})
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "QUERY_TOOL_BUDGET_EXCEEDED"
    with database.session() as session:
        turn = session.scalar(select(AgentTurn).where(AgentTurn.request_id == "goal-budget"))
        assert turn.status == "FAILED" and turn.response["facts"]["meta"]["completion"]["terminal"] is False
        assert len(list(session.scalars(select(ToolInvocation).where(
            ToolInvocation.run_id == turn.id, ToolInvocation.status == "COMPLETED")))) == 8
        assert not list(session.scalars(select(AgentEvent).where(
            AgentEvent.run_id == turn.id, AgentEvent.kind == "completed")))


def test_modern_model_callback_cannot_omit_a_user_goal(tmp_path):
    database, _, _ = seed_corpus()
    app = create_app(database, Settings(database_url="sqlite+pysqlite:///:memory:",
        snapshot_root=tmp_path / "snapshots", internal_agent_token="synthetic-internal"))
    run = app.state.query_service.begin(QueryRequest(message="分析题库数量", request_id="missing-modern-goal"))
    response = TestClient(app).post(f"/internal/agent/runs/{run.id}/tools/get_question_count",
        headers={"Authorization": "Bearer synthetic-internal"}, json=QuerySpec(action="COUNT").model_dump(mode="json"))
    assert response.status_code == 400 and response.json()["error"]["code"] == "QUERY_PLAN_INCOMPLETE"
    assert run.tool_calls == 0 and run.result is None


def test_new_reads_cannot_expand_review_write_authority_from_the_request_start_page(tmp_path):
    database, _, _ = seed_corpus()
    app = create_app(database, Settings(database_url="sqlite+pysqlite:///:memory:", snapshot_root=tmp_path / "snapshots"))
    service = app.state.query_service
    first = service.begin(QueryRequest(message="列出题目", request_id="write-scope-first"))
    service.execute(first, "list_questions", QuerySpec(action="LIST", page_size=1))
    service.finish(first, provider="fixture")
    initial_page = list(first.state["current_page_ids"])
    run = service.begin(QueryRequest(message="把当前页标记为已掌握", request_id="write-scope-next",
        conversation_id=first.conversation_id, expected_version=1))
    service.execute(run, "list_questions", QuerySpec(action="NEXT", final=False,
        user_goal={"objective": "保存本次请求当前页的复习状态", "response_mode": "SYNTHESIS"}))
    assert run.state["current_page_ids"] != initial_page
    with pytest.raises(ValueError, match="QUERY_WRITE_SCOPE_CONFLICT"):
        service.execute(run, "record_review", QuerySpec(action="RECORD_REVIEW",
            review_items=[{"canonical_question_id": run.state["current_page_ids"][0], "status": "MASTERED"}]))
    with database.session() as session:
        assert not list(session.scalars(select(ReviewEvent)))
