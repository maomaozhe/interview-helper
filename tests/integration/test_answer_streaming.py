"""The live Pi gateway persists readable answer deltas before completion."""
import json

import pytest
from fastapi.testclient import TestClient

from interview_intelligence.agent.model_gateway import ModelGateway
from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from test_analytics import seed_corpus


@pytest.mark.parametrize("complete", [True, False])
def test_pi_answer_deltas_are_live_and_replayable(tmp_path, monkeypatch, complete):
    database, _, _ = seed_corpus()
    token = "fixture-internal-token"
    client = TestClient(create_app(database, Settings(
        database_url="sqlite+pysqlite:///:memory:", snapshot_root=tmp_path/"snapshots",
        internal_agent_token=token)))
    service = client.app.state.query_service
    run = service.begin(QueryRequest(message="解释 Redis", request_id="answer-stream"))
    answer = "## Redis\n\n**内存访问**\n\n```python\nprint(1)\n```"

    async def fake_complete(self, run, payload, emit):
        run.model_calls += 1
        fragments = ['{"action":"ANSWER","answer_text":"## Redis\\n\\n**内存',
                     '访问**\\n\\n```python\\nprint(1)\\n```"}']
        for index, fragment in enumerate(fragments):
            await emit({"choices":[{"index":0,"delta":{"tool_calls":[{
                "index":0,"function":{"name":"answer_question" if index == 0 else "",
                                       "arguments":fragment}}]}}]})
            live = service.journal.view(run.id)
            deltas = [e for e in live["events"] if e["type"] == "answer_delta"]
            assert len(deltas) == index + 1
            assert live["status"] == "RUNNING" and live["result"] is None
        if not complete:
            raise ValueError("STREAM_INCOMPLETE")
        return {"choices":[{"message":{"role":"assistant","content":"", "tool_calls":[]},
                            "finish_reason":"tool_calls"}]}

    monkeypatch.setattr(ModelGateway, "complete", fake_complete)
    response = client.post(f"/internal/agent/runs/{run.id}/v1/chat/completions",
        headers={"Authorization":f"Bearer {token}"}, json={"messages":[]})
    assert response.status_code == 200
    assert response.headers["x-accel-buffering"] == "no"
    deltas = [e for e in service.journal.view(run.id)["events"] if e["type"] == "answer_delta"]
    assert "".join(e["data"]["delta"] for e in deltas) == answer
    assert deltas[-1]["data"]["text"] == answer
    assert all(e["data"]["model_call"] == 1 and e["data"]["run_id"] == run.id for e in deltas)
    if complete:
        service.execute(run, "answer_question", QuerySpec(action="ANSWER", answer_text=answer))
        result = service.finish(run, provider="fixture")
        assert result["answer"] == answer
    else:
        assert "STREAM_INCOMPLETE" in response.text
        service.fail(run, ValueError("STREAM_INCOMPLETE"))
        assert service.journal.view(run.id)["result"] is None

    # Public SSE reconnect resumes after the first delta without repeating it.
    replay = client.get(f"/api/runs/{run.id}/events", headers={"Last-Event-ID":str(deltas[0]["sequence"])})
    assert replay.status_code == 200
    frames = [frame for frame in replay.text.split("\n\n") if "event: answer_delta\n" in frame]
    assert len(frames) == 1
    data = json.loads(next(line[6:] for line in frames[0].splitlines() if line.startswith("data: ")))
    assert data == deltas[1]["data"]
