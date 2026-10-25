"""The compact Pi wire stays explicit and never replaces host capabilities."""
import asyncio
from copy import deepcopy
import json
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select

from interview_intelligence.agent.model_gateway import ModelGateway
from interview_intelligence.agent.query_contract import (
    QUERY_MODEL_SCHEMA_VERSION,
    QUERY_PI_TOOL_WIRE_VERSION,
    QueryRequest,
    QuerySpec,
    query_model_schema,
)
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import AgentEvent, AgentTurn, create_database
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.providers.runtime import RequestLimits


def settings_for(tmp_path, prompt="query_agent_v16"):
    return Settings(database_url="sqlite+pysqlite:///:memory:", snapshot_root=tmp_path / "snapshots",
        query_router_enabled=True, pi_agent_enabled=True, pi_agent_url="http://pi-fixture.invalid",
        internal_agent_token="synthetic-internal", query_prompt_version=prompt,
        query_max_model_calls=4, query_max_total_model_calls=6,
        query_max_tokens=96000, query_deadline_seconds=180)


@pytest.mark.parametrize("prompt,wire", [("query_agent_v16", QUERY_PI_TOOL_WIRE_VERSION), ("query_agent_v13", None)])
def test_public_api_uses_explicit_wire_with_complete_schema_and_preserves_host_answer_evidence(monkeypatch, tmp_path, prompt, wire):
    database = create_database("sqlite+pysqlite:///:memory:")
    settings = settings_for(tmp_path, prompt)
    app = create_app(database, settings)
    captured = []

    class FakePiClient:
        def __init__(self, **options):
            assert options == {"timeout": 180, "trust_env": False}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, headers, json):
            assert url == "http://pi-fixture.invalid/run"
            assert headers == {"Authorization": "Bearer synthetic-internal"}
            captured.append(deepcopy(json))
            service = app.state.query_service
            run = service.runs[json["run_id"]]
            assert run.model_wire_version == wire
            goal = {"objective": run.request.message, "response_mode": "SYNTHESIS"}
            count = await asyncio.to_thread(service.execute, run, "get_question_count",
                QuerySpec(action="COUNT", user_goal=goal, filters=run.request.filters, final=True))
            assert count["facts"]["meta"]["completion"]["terminal"] is False
            await asyncio.to_thread(service.execute, run, "answer_question", QuerySpec(action="ANSWER",
                user_goal=goal, filters=run.request.filters, answer_basis="CORPUS", evidence_tool_ordinals=[1],
                answer_text="The complete selected scope contains zero questions; this is an exact SQL scope count."))
            return httpx.Response(200, json={"completed": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", FakePiClient)
    request = {"message": "Analyze the selected question count and its scope", "request_id": "compact-wire",
               "filters": {"company": "FixtureCompany"}}
    response = TestClient(app).post("/api/query", json=request)
    assert response.status_code == 200, response.text
    assert len(captured) == 1
    payload = captured[0]
    if wire is None:
        assert "tool_wire_version" not in payload, "legacy callers keep the original domain tool menu"
    else:
        assert payload["tool_wire_version"] == "query_step_v1"
    schema = payload["schema"]
    assert schema == query_model_schema(require_user_goal=wire is not None)
    assert set(schema["properties"]) == set(QuerySpec.model_fields)
    assert set(schema["$defs"]["FilterSpec"]["required"]) == set(FilterSpec.model_fields)
    assert schema["additionalProperties"] is False and schema["allOf"]
    assert {"evidence_tool_ordinals", "user_goal", "semantic_task", "review_items"} <= schema["properties"].keys()
    assert ("user_goal" in schema["required"]) is (wire is not None)
    assert payload["max_model_calls"] == 4
    assert 0 < payload["timeout_ms"] <= 180000
    assert payload["context"]["explicit_filters"]["company"] == "FixtureCompany"
    assert payload["context"]["budget"]["remaining_shared_calls"] == 6
    body = response.json()
    assert body["meta"]["planning"]["spec"]["action"] == "ANSWER"
    assert body["meta"]["evidence_tool_ordinals"] == [1]
    assert body["meta"]["completion"]["terminal"] is True
    assert body["meta"]["user_goal"]["objective"] == request["message"]
    assert body["meta"]["applied_filters"]["company"] == "FixtureCompany"
    assert body["meta"]["answer_evidence"][0]["ordinal"] == 1
    with database.session() as session:
        recorded = list(session.scalars(select(AgentEvent).where(AgentEvent.kind == "model_wire")))
        turn = session.scalar(select(AgentTurn).where(AgentTurn.request_id == request["request_id"]))
        assert turn.response["facts"]["components"][0]["intent"] == "COUNT"
        assert turn.response["facts"]["components"][0]["facts"]["meta"]["counts"]["canonical_questions"] == 0
    assert len(recorded) == 1
    assert recorded[0].payload == {"version": wire or "domain_tools_v1", "runtime": "pi",
        "query_schema": QUERY_MODEL_SCHEMA_VERSION, "prompt_version": prompt}


def test_structured_sql_operations_never_start_pi_or_emit_a_model_wire_event(monkeypatch, tmp_path):
    database = create_database("sqlite+pysqlite:///:memory:")
    app = create_app(database, settings_for(tmp_path))

    def forbidden_client(**kwargs):
        raise AssertionError("structured UI SQL must not need a model or Pi request")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden_client)
    response = TestClient(app).post("/api/questions/list", json={"list_request": {}, "request_id": "structured-no-wire"})
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["planning"]["provider"] == "structured_sql"
    with database.session() as session:
        assert not list(session.scalars(select(AgentEvent).where(AgentEvent.kind == "model_wire")))


def test_query_step_is_not_a_new_host_tool_capability(tmp_path):
    database = create_database("sqlite+pysqlite:///:memory:")
    app = create_app(database, settings_for(tmp_path))
    service = app.state.query_service
    run = service.begin(QueryRequest(message="Count the selected scope", request_id="no-host-query-step"))
    plan = QuerySpec(action="COUNT", user_goal={"objective": run.request.message, "response_mode": "DIRECT_RESULT"})
    response = TestClient(app).post(f"/internal/agent/runs/{run.id}/tools/query_step",
        headers={"Authorization": "Bearer synthetic-internal"}, json=plan.model_dump(mode="json"))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_TOOL_PLAN"
    assert run.tool_calls == 0 and run.user_goal == {} and run.result is None


def test_gateway_forwards_one_compact_schema_without_expanding_tools_or_actions(monkeypatch, tmp_path):
    original_client = httpx.AsyncClient
    schema = query_model_schema(require_user_goal=True)
    schema["properties"]["action"]["enum"] = ["COUNT", "ANSWER"]
    tool = {"type": "function", "function": {"name": "query_step", "description": "One checked query step.", "parameters": schema}}
    captured = []

    def upstream(request):
        body = json.loads(request.content)
        captured.append(body)
        return httpx.Response(200, json={"model": "fixture-model", "usage": {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, "choices": [{
                "finish_reason": "tool_calls", "message": {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "fixture-call", "type": "function", "function": {"name": "query_step", "arguments": "{}"}}]}}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(upstream), **kwargs))
    settings = SimpleNamespace(model_api_key="synthetic-key", model_base_url="https://ark.cn-fixture.invalid/v3",
        query_model="fixture-model", judge_model="fixture-model", query_max_model_calls=4)
    run = SimpleNamespace(id="fixture-run", request=SimpleNamespace(request_id="fixture-request"), model_calls=0,
        limits=RequestLimits(deadline=time.monotonic() + 10, max_tokens=96000), call_timings={})
    gateway = ModelGateway(settings, ModelCallGate(tmp_path / "model.lock", minimum_interval_seconds=0))
    asyncio.run(gateway.complete(run, {"messages": [{"role": "user", "content": "Count this scope"}],
        "tools": [deepcopy(tool)], "tool_choice": "required"}))
    assert len(captured) == 1
    body = captured[0]
    assert body["tools"] == [tool], "the gateway cannot add domain menus, expand schemas, or add actions"
    assert body["tools"][0]["function"]["parameters"]["properties"]["action"]["enum"] == ["COUNT", "ANSWER"]
    assert body["model"] == "fixture-model" and body["max_tokens"] == 4096
    assert body["thinking"] == {"type": "disabled"}
    assert body["tool_choice"] == "required"
    assert run.model_calls == run.limits.calls == 1
    serialized = json.dumps(body["tools"], separators=(",", ":"))
    assert serialized.count('"title":"FilterSpec"') == 1
    assert serialized.count('"name":"query_step"') == 1


@pytest.mark.parametrize("trusted,action,projected", [(True, "ANSWER", True), (False, "ANSWER", False), (True, "SEARCH", False)])
def test_real_internal_stream_projects_only_trusted_compact_answers(monkeypatch, tmp_path, trusted, action, projected):
    database = create_database("sqlite+pysqlite:///:memory:")
    app = create_app(database, settings_for(tmp_path))
    service = app.state.query_service
    run = service.begin(QueryRequest(message="Explain the evidence", request_id="compact-stream"))
    if trusted:
        run.model_wire_version = QUERY_PI_TOOL_WIRE_VERSION
    arguments = json.dumps({"action": action, "answer_text": "**Evidence**\nwith scope limits"})

    async def complete(self, active_run, payload, emit=None):
        assert active_run is run and emit is not None
        await emit({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0,
            "id": "wrapped-answer", "type": "function", "function": {"name": "query_step", "arguments": arguments}}]},
            "finish_reason": None}]})
        return {"choices": [{"index": 0, "message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "wrapped-answer", "type": "function", "function": {"name": "query_step", "arguments": arguments}}]},
            "finish_reason": "tool_calls"}]}

    monkeypatch.setattr(ModelGateway, "complete", complete)
    response = TestClient(app).post(f"/internal/agent/runs/{run.id}/v1/chat/completions",
        headers={"Authorization": "Bearer synthetic-internal"}, json={"messages": [], "stream": True})
    assert response.status_code == 200 and "[DONE]" in response.text
    with database.session() as session:
        answers = list(session.scalars(select(AgentEvent).where(AgentEvent.run_id == run.id, AgentEvent.kind == "answer_delta")))
        completed = list(session.scalars(select(AgentEvent).where(AgentEvent.run_id == run.id, AgentEvent.kind == "completed")))
    assert bool(answers) is projected
    if projected:
        assert answers[0].payload["text"] == "**Evidence**\nwith scope limits"
        assert answers[0].payload["temporary"] is True
    assert not completed and run.result is None and not run.terminal
