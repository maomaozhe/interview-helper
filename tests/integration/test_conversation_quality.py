"""Conversation quality invariants independent of semantic model success."""
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from interview_intelligence.agent.feedback_memory import FeedbackMemory, MemoryUpdate
from interview_intelligence.agent.history import ConversationHistory
from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import AgentConversation, AgentTurn, CorpusState, now_utc
from test_analytics import seed_corpus


def app_fixture(tmp_path, planner, retriever=None, user_id="local"):
    db, question_id, _ = seed_corpus()
    with db.session() as session, session.begin():
        corpus = session.get(CorpusState, 1)
        corpus.current_revision = corpus.indexed_revision = 1
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", snapshot_root=tmp_path / "snapshots",
                        local_user_id=user_id, query_router_enabled=True)
    app = create_app(db, settings, retriever, query_planner=planner)
    return TestClient(app), db, question_id, app.state.query_service


def test_clarification_uses_no_retrieval_and_short_answer_receives_original_goal(tmp_path):
    class Retriever:
        calls = 0
        def retrieve(self, query, eligible, pipeline, top_k):
            self.calls += 1
            return {"data": [{"canonical_question_id": eligible[0]}],
                    "meta": {"pipeline": pipeline, "rerank_status": "COMPLETED"}}
    class Planner:
        def plan(self, run, context):
            if context["message"] == "场景设计题":
                return QuerySpec(action="CLARIFY", clarification="准备哪个方向？",
                                 clarification_options=["Agent 应用设计", "业务系统设计", "线上排障场景", "全部方向"])
            assert context["session"]["pending_clarification"]["original_message"] == "场景设计题"
            assert context["message"] == "Agent 应用设计"
            return QuerySpec(action="SEARCH", search_query="Agent 应用设计")
    retriever = Retriever()
    client, db, _, _ = app_fixture(tmp_path, Planner(), retriever)
    first = client.post("/api/query", json={"message": "场景设计题", "request_id": "ambiguous"})
    assert first.status_code == 200, first.text
    meta = first.json()["meta"]
    assert meta["route"] == "CLARIFY" and len(meta["clarification"]["options"]) == 4
    assert retriever.calls == 0
    second = client.post("/api/query", json={"message": "Agent 应用设计", "request_id": "clarified",
        "conversation_id": meta["conversation_id"], "expected_version": 1})
    assert second.status_code == 200, second.text
    assert retriever.calls == 1 and second.json()["meta"]["relevance_status"] == "VERIFIED"
    history = client.get(f"/api/conversations/{meta['conversation_id']}").json()["data"]
    assert "pending_clarification" not in history["state"]
    assert len(history["turns"]) == 2
    assert history["turns"][0]["result"]["run_id"] == meta["run_id"]
    assert client.post("/api/query", json={"message": "场景设计题", "request_id": "ambiguous"}).json() == first.json()
    assert retriever.calls == 1


def test_explaining_choices_keeps_the_original_clarification_goal(tmp_path):
    choices=["Agent 应用设计","业务系统设计","线上排障场景","全部方向"]
    class Planner:
        def plan(self,run,context):
            return QuerySpec(action="CLARIFY",clarification="选择哪个方向？",clarification_options=choices)
    client,_,_,_=app_fixture(tmp_path,Planner())
    first=client.post("/api/query",json={"message":"系统设计题","request_id":"choices-first"}).json()
    conversation=first["meta"]["conversation_id"]
    second=client.post("/api/query",json={"message":"有哪些方向？","request_id":"choices-explain",
        "conversation_id":conversation,"expected_version":1})
    assert second.status_code == 200,second.text
    history=client.get(f"/api/conversations/{conversation}").json()["data"]
    assert history["state"]["pending_clarification"]["original_message"] == "系统设计题"
    assert second.json()["meta"]["clarification"]["options"] == choices


def test_search_progress_reaches_the_same_durable_run_and_legacy_retrievers_still_work(tmp_path):
    class Retriever:
        supports_progress=True
        def retrieve(self,query,eligible,pipeline,top_k,*,on_progress):
            on_progress({"stage":"embedding"})
            on_progress({"stage":"retrieving"})
            on_progress({"stage":"reranking","count":len(eligible)})
            return {"data":[],"meta":{"pipeline":pipeline,"rerank_status":"COMPLETED"}}
    class Planner:
        def plan(self,run,context):
            return QuerySpec(action="SEARCH",search_query="系统设计")
    client,_,_,_=app_fixture(tmp_path,Planner(),Retriever())
    response=client.post("/api/query",json={"message":"业务系统设计","request_id":"progress-forward"})
    assert response.status_code == 200,response.text
    run_id=response.json()["meta"]["run_id"]
    events=client.get(f"/api/runs/{run_id}").json()["data"]["events"]
    assert [e["data"]["stage"] for e in events if e["type"]=="stage"] == [
        "tool","eligibility","embedding","retrieving","reranking","assembling"]
    assert events[-1]["type"] == "completed"


def test_model_tool_deltas_publish_only_temporary_user_facing_question(tmp_path,monkeypatch):
    from interview_intelligence.agent.model_gateway import ModelGateway
    from interview_intelligence.agent.query_contract import QueryRequest
    settings_token="fixture-internal-token"
    class Planner:
        def plan(self,run,context):
            raise AssertionError("No planner or provider call is expected")
    db,_,_=seed_corpus()
    settings=Settings(database_url="sqlite+pysqlite:///:memory:",snapshot_root=tmp_path/"snapshots",
                      internal_agent_token=settings_token)
    client=TestClient(create_app(db,settings,query_planner=Planner()))
    service=client.app.state.query_service
    run=service.begin(QueryRequest(message="系统设计题",request_id="delta-presentation"))
    async def complete(self,run,payload,emit):
        await emit({"choices":[{"delta":{"tool_calls":[{"index":0,"function":{
            "arguments":'{"action":"CLARIFY","clarification":"你想看哪个方向？'}}]}}]})
        return {"choices":[{"message":{"role":"assistant","content":"", "tool_calls":[]},"finish_reason":"tool_calls"}]}
    monkeypatch.setattr(ModelGateway,"complete",complete)
    response=client.post(f"/internal/agent/runs/{run.id}/v1/chat/completions",headers={"Authorization":f"Bearer {settings_token}"},json={"messages":[]})
    assert response.status_code == 200,response.text
    events=service.journal.view(run.id)["events"]
    question=[e["data"] for e in events if e["type"]=="clarification_delta"]
    assert question == [{"text":"你想看哪个方向？","temporary":True,"call_index":0}]
    assert service.journal.view(run.id)["status"] == "RUNNING"


@pytest.mark.parametrize("pipeline", ["HYBRID_RERANK", "HYBRID"])
def test_failed_relevance_is_not_presented_as_verified_results(tmp_path, pipeline):
    class Retriever:
        def retrieve(self, query, eligible, pipeline, top_k):
            return {"data": [{"canonical_question_id": eligible[0]}],
                    "meta": {"pipeline": "HYBRID", "rerank_status": "FAILED", "candidate_count": 50}}
    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="SEARCH", search_query="Agent 应用设计")
    client, _, _, _ = app_fixture(tmp_path, Planner(), Retriever())
    response = client.post("/api/query", json={"message": "Agent 应用设计", "request_id": pipeline, "pipeline": pipeline})
    assert response.status_code == 200, response.text
    result = response.json()
    if pipeline == "HYBRID_RERANK":
        assert result["data"] == [] and result["meta"]["relevance_status"] == "UNAVAILABLE"
        assert "核验未完成" in result["meta"]["answer"]
    else:
        assert len(result["data"]) == 1 and result["meta"]["relevance_status"] == "UNVERIFIED"


def test_query_service_passes_restored_intent_separately_from_expansion(tmp_path):
    class Retriever:
        supports_relevance_query = True
        calls = []

        def retrieve(self, query, eligible, pipeline, top_k, *, relevance_query=None):
            self.calls.append((query, relevance_query))
            return {"data": [], "meta": {"pipeline": pipeline, "rerank_status": "COMPLETED"}}

    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="SEARCH", search_query="秒杀设计 库存扣减 Redis 异步消息",
                             relevance_query="高并发秒杀业务系统怎么设计")

    retriever = Retriever()
    client, _, _, _ = app_fixture(tmp_path, Planner(), retriever)
    response = client.post("/api/query", json={"message": "这类题再找一些", "request_id": "restored-intent"})
    assert response.status_code == 200, response.text
    assert retriever.calls == [("秒杀设计 库存扣减 Redis 异步消息", "高并发秒杀业务系统怎么设计")]


@pytest.mark.parametrize("supports_facets", [True, False])
def test_query_service_forwards_optional_lexical_facets_without_breaking_old_retrievers(tmp_path, supports_facets):
    facets = ["Agent 上下文维护", "Agent memory 记忆管理", "Agent 工具执行恢复 权限"]

    class FacetRetriever:
        supports_lexical_facets = True
        calls = []
        def retrieve(self, query, eligible, pipeline, top_k, *, lexical_facets=None):
            self.calls.append(lexical_facets)
            return {"data": [], "meta": {"pipeline": pipeline, "rerank_status": "COMPLETED"}}

    class LegacyRetriever:
        calls = []
        def retrieve(self, query, eligible, pipeline, top_k):
            self.calls.append("legacy")
            return {"data": [], "meta": {"pipeline": pipeline, "rerank_status": "COMPLETED"}}

    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="SEARCH", search_query="Agent harness 设计", lexical_facets=facets)

    retriever = FacetRetriever() if supports_facets else LegacyRetriever()
    client, _, _, _ = app_fixture(tmp_path, Planner(), retriever)
    result = client.post("/api/query", json={"message": "harness设计题", "request_id": "facets"})
    assert result.status_code == 200, result.text
    assert retriever.calls == ([facets] if supports_facets else ["legacy"])


def test_failed_lexical_facets_are_retryable_and_do_not_degrade_to_raw_results(tmp_path):
    class Retriever:
        supports_lexical_facets = True
        calls = []
        def retrieve(self, query, eligible, pipeline, top_k, *, lexical_facets=None):
            self.calls.append(pipeline)
            raise ValueError("LEXICAL_FACET_RETRIEVAL_FAILED")

    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="SEARCH", search_query="Agent harness", lexical_facets=["Agent memory 设计"])

    retriever = Retriever()
    client, _, _, _ = app_fixture(tmp_path, Planner(), retriever)
    result = client.post("/api/query", json={"message": "harness设计题", "request_id": "failed-facets"})
    assert result.status_code == 503, result.text
    assert result.json()["error"]["retryable"] is True
    assert result.json()["error"]["code"] == "LEXICAL_FACET_RETRIEVAL_FAILED"
    assert retriever.calls == ["HYBRID_RERANK"]


def test_feedback_binds_full_authoritative_receipt_memory_and_new_requery(tmp_path):
    class Planner:
        contexts = []
        def plan(self, run, context):
            self.contexts.append(context)
            return QuerySpec(action="LIST")
    planner = Planner()
    client, _, _, service = app_fixture(tmp_path, planner)
    original = client.post("/api/query", json={"message": "Agent设计题", "request_id": "original"}).json()
    meta = original["meta"]
    feedback = client.post("/api/feedback", json={"category": "IRRELEVANT", "query": "Agent设计题",
        "note": "只找Agent的上下文记忆设计，不要普通缓存设计", "run_id": meta["run_id"],
        "context": {"answer": "forged client answer", "results": []}})
    assert feedback.status_code == 201, feedback.text
    record = feedback.json()["data"]
    assert record["conversation_id"] == meta["conversation_id"]
    receipt = record["context"]["receipt"]
    assert receipt["result"]["answer"] == original["meta"]["answer"]
    assert receipt["result"]["facts"]["data"] == original["data"]
    assert record["user_id"] == "local"
    fid = record["id"]
    assert service.feedback_memory.matching("Agent设计题") == []
    replay = client.post("/api/query", json={"message": "Agent设计题", "request_id": "corrected",
        "conversation_id": meta["conversation_id"], "expected_version": 1, "feedback_ids": [fid]})
    assert replay.status_code == 200, replay.text
    assert planner.contexts[-1]["query_feedback"][0]["feedback_id"] == fid
    assert planner.contexts[-1]["query_memory"] == []
    active = client.patch(f"/api/query-memory/{fid}", json={"active": True, "expected_version": 0})
    assert active.status_code == 200, active.text
    assert active.json()["data"]["version"] == 1
    assert len(service.feedback_memory.matching("agent 设计题？")) == 1
    assert service.feedback_memory.matching("普通缓存设计") == []
    assert not service.preferences.defaults()
    assert client.patch(f"/api/query-memory/{fid}", json={"active": False, "expected_version": 0}).status_code == 409
    assert client.patch(f"/api/query-memory/{fid}", json={"active": False, "expected_version": 1}).status_code == 200
    assert service.feedback_memory.matching("Agent设计题") == []
    exported = client.get("/api/query-memory/export").json()["data"]["samples"]
    assert len(exported) == 1
    assert exported[0]["original_receipt"]["run_id"] == meta["run_id"]
    assert exported[0]["replays"][0]["request_id"] == "corrected"
    assert client.post("/api/feedback", json={"category": "IRRELEVANT", "note": "wrong run", "run_id": str(uuid4())}).status_code == 404


def test_history_keyset_paging_scope_search_and_foreign_user(tmp_path):
    db, _, _ = seed_corpus()
    when = now_utc()
    ids = []
    with db.session() as session, session.begin():
        for index in range(3):
            c = AgentConversation(user_id="local", state={}, created_at=when + timedelta(seconds=index))
            session.add(c); session.flush(); ids.append(c.id)
            for i in range(23 if index == 0 else 1):
                session.add(AgentTurn(user_id="local", conversation_id=c.id, request_id=str(uuid4()),
                    payload_hash="x", message=f"设计题 {index} {i}", status="SUCCEEDED", response={},
                    created_at=when + timedelta(seconds=i)))
        foreign = AgentConversation(user_id="other", state={}); session.add(foreign); session.flush()
        foreign_id = foreign.id
    history = ConversationHistory(db, "local", "test")
    page = history.conversations(limit=2)
    assert len(page["items"]) == 2 and page["next_cursor"]
    remaining = history.conversations(cursor=page["next_cursor"], limit=2)
    assert [i["conversation_id"] for i in page["items"] + remaining["items"]] == ids[::-1]
    assert remaining["next_cursor"] is None
    assert len(history.conversations("设计题 1")["items"]) == 1
    assert history.conversations("%_")["items"] == []
    with pytest.raises(ValueError, match="INVALID_CURSOR_SCOPE"):
        history.conversations("other query", cursor=page["next_cursor"])
    with pytest.raises(ValueError, match="INVALID_CURSOR_SCOPE"):
        ConversationHistory(db, "other", "test").conversations(cursor=page["next_cursor"])
    first = history.turns(ids[0], limit=20)
    earlier = history.turns(ids[0], first["next_cursor"], limit=20)
    assert len(first["turns"]) == 20 and len(earlier["turns"]) == 3
    assert len({t["run_id"] for t in first["turns"] + earlier["turns"]}) == 23
    with pytest.raises(ValueError, match="INVALID_CURSOR_SCOPE"):
        history.turns(ids[1], first["next_cursor"])
    with pytest.raises(KeyError, match="CONVERSATION_NOT_FOUND"):
        history.turns(foreign_id)


def test_feedback_and_memory_are_user_bound_and_feedback_paths_cannot_escape(tmp_path):
    db, _, _ = seed_corpus()
    root = tmp_path / "feedback"; root.mkdir()
    fid = str(uuid4())
    (root / f"{fid}.json").write_text(json.dumps({"id": fid, "created_at": "2026-10-08", "user_id": "alice",
        "query": "Agent设计题", "category": "IRRELEVANT", "note": "只看记忆设计"}), encoding="utf-8")
    alice = FeedbackMemory(db, "alice", root); bob = FeedbackMemory(db, "bob", root)
    assert len(alice.list()) == 1 and not bob.list()
    alice.update(fid, MemoryUpdate(active=True, expected_version=0))
    assert len(alice.matching("Agent设计题")) == 1 and not bob.matching("Agent设计题")
    with pytest.raises(KeyError, match="FEEDBACK_NOT_FOUND"):
        bob.selected([fid])
    with pytest.raises(KeyError, match="FEEDBACK_NOT_FOUND"):
        alice.feedback("../../.env")
