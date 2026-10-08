"""Acceptance scripts reject invalid pagination sources before historical requery."""
from copy import deepcopy
import json
import sys

import httpx
import pytest

from scripts import evaluate_historical_requery as script


def prior_report():
    return {"cases": [{"id": "context-and-topic-reset", "turns": [{}, {
        "request": {"message": "这类场景设计题再找一些"},
        "response": {"meta": {"conversation_id": "old-conversation", "run_id": "old-agent-run"}}}]}]}


def response(action, run, ids=(), *, version=1, offset=0, cursor=None, top_n=None):
    return {"data": [{"canonical_question_id": key} for key in ids], "meta": {
        "conversation_id": "paged-conversation", "conversation_version": version, "run_id": run,
        "applied_filters": {"company": "字节"}, "pagination": {"offset": offset, "next_cursor": cursor},
        "planning": {"spec": {"action": action, "top_n": top_n, "page_size": 2}}}}


class Client:
    def __init__(self, invalid=None):
        changed = response("SEARCH", "changed", version=5)
        changed["meta"]["planning"]["spec"]["relevance_query"] = "秒杀业务系统设计题"
        short = response("SEARCH", "new-agent", version=6)
        short["meta"]["planning"]["spec"]["relevance_query"] = "Agent 上下文记忆设计题"
        short["meta"]["requery_of_run_id"] = "old-agent-run"
        first = response("LIST", "first", ["a", "b"], cursor="page-two")
        second = response("NEXT", "second", ["c", "d"], version=2, offset=2)
        fresh = response("LIST", "fresh", ["a", "b"], version=3)
        fresh["meta"]["requery_of_run_id"] = "second"
        if invalid == "top_n":
            first["meta"]["planning"]["spec"]["top_n"] = 2
        if invalid == "clarify":
            second = response("CLARIFY", "not-next", version=2)
        if invalid == "duplicate_page":
            second["data"] = [{"canonical_question_id": "a"}]
        self.responses = [changed, short, first, second, fresh]
        self.posts = []

    def __enter__(self): return self
    def __exit__(self, *args): pass

    def get(self, path):
        data = {"data": {"version": 4}} if path.startswith("/api/conversations/") else {"data": {"ready": True}}
        return httpx.Response(200, json=data, request=httpx.Request("GET", "http://test" + path))

    def post(self, path, *, json):
        self.posts.append(json)
        return httpx.Response(200, json=self.responses.pop(0), request=httpx.Request("POST", "http://test" + path))


def test_real_acceptance_protocol_pages_before_binding_next_source():
    client = Client()
    result = {"turns": [], "checks": []}
    snapshots = []
    script.evaluate(client, result, lambda: snapshots.append(deepcopy(result)), prior_report())
    assert all(check["passed"] for check in result["checks"])
    first = client.posts[2]
    assert first["message"] == "列出 Redis 高频题" and first["page_size"] == 2
    assert first["filters"] == {"company": "字节"}
    assert client.posts[-1]["requery_of_run_id"] == "second"
    assert client.posts[-1]["expected_version"] == 2
    assert len({body["request_id"] for body in client.posts}) == 5
    assert snapshots[-1]["checks"][-1]["name"] == "historical-next-restarts-original-first-page"


@pytest.mark.parametrize("invalid,count", [("top_n", 3), ("clarify", 4), ("duplicate_page", 4)])
def test_invalid_page_preconditions_stop_before_historical_run_binding(invalid, count):
    client = Client(invalid)
    result = {"turns": [], "checks": []}
    with pytest.raises(AssertionError):
        script.evaluate(client, result, lambda: None, prior_report())
    assert len(client.posts) == count
    assert result["checks"][-1]["passed"] is False
    assert all(body.get("requery_of_run_id") != "not-next" for body in client.posts)


def test_failed_acceptance_persists_reviewable_failure_without_overwriting_previous_attempt(tmp_path, monkeypatch):
    prior = tmp_path / "prior.json"; prior.write_text(json.dumps(prior_report()), encoding="utf-8")
    old = tmp_path / "historical-requery.json"; old.write_text("failed original evidence", encoding="utf-8")
    new = tmp_path / "historical-requery-r2.json"
    monkeypatch.setattr(sys, "argv", ["evaluation", "--output", str(new), "--prior-report", str(prior)])
    monkeypatch.setattr(script.httpx, "Client", lambda **kwargs: Client("clarify"))
    with pytest.raises(AssertionError, match="source-is-real-next-page"):
        script.main()
    saved = json.loads(new.read_text(encoding="utf-8"))
    assert saved["status"] == "FAILED" and saved["passed"] is False
    assert saved["failure"]["message"] == "source-is-real-next-page"
    assert saved["failure"]["last_case"] == "filtered-list-next"
    assert saved["finished_at"] and len(saved["turns"]) == 4
    assert old.read_text(encoding="utf-8") == "failed original evidence"


def test_existing_output_is_never_overwritten(tmp_path, monkeypatch):
    existing = tmp_path / "historical-requery.json"
    existing.write_text("original evidence", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["evaluation", "--output", str(existing)])
    with pytest.raises(SystemExit, match="Refuse to overwrite"):
        script.main()
    assert existing.read_text(encoding="utf-8") == "original evidence"
