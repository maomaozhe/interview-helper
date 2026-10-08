"""A historical requery recovers intent while using a fresh, user-bound run."""
from uuid import uuid4
import hashlib

import pytest
from sqlalchemy import func, select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.feedback_memory import MemoryUpdate
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.domain.models import AgentConversation, AgentTurn, ReviewEvent, ToolInvocation
from test_analytics import seed_corpus
from test_conversation_quality import app_fixture


def test_requery_short_answer_restores_goal_scope_feedback_and_original_message(tmp_path):
    class Retriever:
        supports_relevance_query = True

        def __init__(self):
            self.calls = []

        def retrieve(self, query, eligible, pipeline, top_k, *, relevance_query=None):
            self.calls.append((query, relevance_query, pipeline, top_k, eligible))
            return {"data": [{"canonical_question_id": eligible[0]}] if eligible else [],
                    "meta": {"pipeline": pipeline, "rerank_status": "COMPLETED"}}

    class Planner:
        def __init__(self):
            self.contexts = []

        def plan(self, run, context):
            self.contexts.append(context)
            if context["message"] == "场景设计题":
                return QuerySpec(action="CLARIFY", clarification="准备哪个方向？")
            if context["message"] == "业务系统设计":
                return QuerySpec(action="SEARCH", search_query="秒杀设计",
                                 relevance_query="秒杀业务系统设计", filters={"company": "腾讯"})
            if context.get("requery_origin"):
                assert context["message"] == "Agent 应用设计"
                assert context["session"]["pending_clarification"]["original_message"] == "场景设计题"
                assert context["session"]["last_plan"]["relevance_query"] == "Agent 应用架构设计"
                assert "current_page_ids" not in context["session"]
                assert context["default_page_size"] == 7 and context["requested_pipeline"] == "BM25"
                return QuerySpec.model_validate({**context["session"]["last_plan"],
                    **context["retrieval_expansions"]["previous_plan"]})
            return QuerySpec(action="SEARCH", search_query="Agent 智能体 应用 架构 记忆设计",
                             relevance_query="Agent 应用架构设计", page_size=7,
                             filters={"company": "字节", "round": "SECOND"})

    retriever, planner = Retriever(), Planner()
    client, db, _, service = app_fixture(tmp_path, planner, retriever)
    first = client.post("/api/query", json={"message": "场景设计题", "request_id": "ambiguous"}).json()["meta"]
    body = {"message": "Agent 应用设计", "request_id": "agent", "pipeline": "BM25", "page_size": 7,
            "filters": {"company": "字节", "round": "SECOND"},
            "conversation_id": first["conversation_id"], "expected_version": 1}
    original = client.post("/api/query", json=body)
    assert original.status_code == 200, original.text
    source = original.json()["meta"]
    switched = client.post("/api/query", json={"message": "业务系统设计", "request_id": "changed-topic",
        "conversation_id": first["conversation_id"], "expected_version": 2})
    assert switched.status_code == 200, switched.text
    feedback = client.post("/api/feedback", json={"category": "IRRELEVANT", "query": "Agent 应用设计",
        "note": "只看 Agent 记忆设计", "run_id": source["run_id"]})
    assert feedback.status_code == 201, feedback.text
    fid = feedback.json()["data"]["id"]
    service.feedback_memory.update(fid, MemoryUpdate(active=True, expected_version=0))
    refresh_body = {"message": "Agent 应用设计", "request_id": "fresh-agent",
        "requery_of_run_id": source["run_id"], "feedback_ids": [fid],
        "filters": {"company": "腾讯", "round": None},
        "conversation_id": first["conversation_id"], "expected_version": 3}
    refreshed = client.post("/api/query", json=refresh_body)
    assert refreshed.status_code == 200, refreshed.text
    meta = refreshed.json()["meta"]
    assert meta["conversation_version"] == 4 and meta["run_id"] != source["run_id"]
    assert meta["requery_of_run_id"] == source["run_id"]
    assert meta["applied_filters"]["company"] == "腾讯" and meta["applied_filters"]["round"] is None
    assert retriever.calls[-1][:4] == ("Agent 智能体 应用 架构 记忆设计", "Agent 应用架构设计", "BM25", 7)
    context = planner.contexts[-1]
    assert context["explicit_filters"] == {"company": "腾讯", "round": None}
    assert context["query_feedback"][0]["feedback_id"] == fid
    assert context["query_memory"][0]["feedback_id"] == fid
    calls = len(retriever.calls)
    assert client.post("/api/query", json=refresh_body).json() == refreshed.json()
    assert len(retriever.calls) == calls
    with db.session() as session:
        turn = session.get(AgentTurn, meta["run_id"])
        assert turn.message == "Agent 应用设计" and turn.request_id == "fresh-agent"
        assert turn.request_payload["requery_of_run_id"] == source["run_id"]
        assert turn.state_before["last_plan"]["relevance_query"] == "Agent 应用架构设计"
    conflict = client.post("/api/query", json={**refresh_body, "feedback_ids": []})
    assert conflict.status_code == 409
    stale = client.post("/api/query", json={**refresh_body, "request_id": "stale-fresh-agent"})
    assert stale.status_code == 409


def test_requery_next_refreshes_original_first_page_and_honors_current_page_size(tmp_path):
    class Planner:
        def plan(self, run, context):
            if context.get("requery_origin"):
                assert context["message"] == "下一页"
                assert context["session"]["has_next_page"] is False
                assert context["requery_origin"]["target_action"] == "LIST"
                return QuerySpec(action="NEXT")  # Host must also enforce refresh semantics.
            if context["message"] == "下一页":
                return QuerySpec(action="NEXT")
            return QuerySpec(action="LIST", filters={"topic_l1": "Redis"}, page_size=1)

    client, _, _, _ = app_fixture(tmp_path, Planner())
    first = client.post("/api/query", json={"message": "Redis列表", "request_id": "page-one"}).json()
    next_page = client.post("/api/query", json={"message": "下一页", "request_id": "page-two",
        "conversation_id": first["meta"]["conversation_id"], "expected_version": 1})
    assert next_page.status_code == 200, next_page.text
    source = next_page.json()
    assert source["data"][0]["canonical_question_id"] != first["data"][0]["canonical_question_id"]
    refreshed = client.post("/api/query", json={"message": "下一页", "request_id": "refresh-first",
        "requery_of_run_id": source["meta"]["run_id"], "page_size": 2, "pipeline": "HYBRID"})
    assert refreshed.status_code == 200, refreshed.text
    result = refreshed.json()
    assert result["meta"]["planning"]["spec"]["action"] == "LIST"
    assert result["meta"]["planning"]["spec"]["page_size"] == 2
    assert result["meta"]["pagination"]["offset"] == 0 and len(result["data"]) == 2
    assert result["data"][0]["canonical_question_id"] == first["data"][0]["canonical_question_id"]
    assert result["meta"]["conversation_id"] != first["meta"]["conversation_id"]


def test_requery_sources_are_user_bound_completed_and_query_only(tmp_path):
    class Planner:
        calls = 0
        def plan(self, run, context):
            self.calls += 1
            return QuerySpec(action="LIST")

    planner = Planner()
    client, db, _, _ = app_fixture(tmp_path, planner)
    ids = {}
    with db.session() as session, session.begin():
        for label, user, status, action in [("foreign", "other", "SUCCEEDED", "LIST"),
                ("unfinished", "local", "INTERRUPTED", "LIST"),
                ("review", "local", "SUCCEEDED", "RECORD_REVIEW"),
                ("details", "local", "SUCCEEDED", "DETAILS")]:
            conversation = AgentConversation(user_id=user, state={})
            session.add(conversation); session.flush()
            turn = AgentTurn(user_id=user, conversation_id=conversation.id, request_id=label,
                payload_hash="x", message="把当前题目标记为已掌握", status=status,
                response={"intent": action, "planning": {"spec": {"action": action}}})
            session.add(turn); session.flush(); ids[label] = turn.id
    for label, expected in [("missing", 404), ("foreign", 404), ("unfinished", 409),
                            ("review", 400), ("details", 400)]:
        result = client.post("/api/query", json={"message": "重新检索", "request_id": f"retry-{label}",
            "requery_of_run_id": ids.get(label, str(uuid4()))})
        assert result.status_code == expected, result.text
    assert planner.calls == 0
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(AgentTurn)) == 4


def test_requery_cannot_reuse_historical_write_authorization_after_loading_page():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    original = service.begin(QueryRequest(message="列出题目并标记为已掌握", request_id="original-list"))
    service.execute(original, "list_questions", QuerySpec(action="LIST"))
    service.finish(original, provider="fixture")
    refresh = service.begin(QueryRequest(message="列出题目并标记为已掌握", request_id="read-only-refresh",
                                        requery_of_run_id=original.id))
    assert not service.model_context(refresh)["tool_policy"]["write_authorized"]
    result = service.execute(refresh, "list_questions", QuerySpec(action="LIST", final=False))
    assert "record_review" not in result["facts"]["meta"]["harness"]["tool_policy"]["allowed_tools"]
    with pytest.raises(ValueError, match="QUERY_TOOL_NOT_ALLOWED"):
        service.execute(refresh, "record_review", QuerySpec(action="RECORD_REVIEW", review_items=[{
            "canonical_question_id": refresh.state["current_page_ids"][0], "status": "MASTERED", "expected_version": 0}]))
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(ReviewEvent)) == 0
    service.fail(refresh)


def test_interrupted_requery_recovers_restored_context_and_completed_work_without_old_result_replay():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    original = service.begin(QueryRequest(message="Redis列表", request_id="source"))
    service.execute(original, "list_questions", QuerySpec(action="LIST", filters={"topic_l1": "Redis"}, page_size=1))
    service.finish(original, provider="fixture")
    refresh_body = QueryRequest(message="这类题", request_id="resumable", requery_of_run_id=original.id)
    refresh = service.begin(refresh_body)
    assert refresh.parts == [] and refresh.result is None
    service.execute(refresh, "list_questions", QuerySpec(action="LIST", filters={"topic_l1": "Redis"}, page_size=1))
    service.fail(refresh, ValueError("QUERY_INTERRUPTED"))
    resumed = QueryService(db, signing_key="test").begin(refresh_body)
    assert resumed.id == refresh.id and resumed.id != original.id
    assert resumed.state["last_plan"]["filters"]["topic_l1"] == "Redis"
    assert resumed.result["facts"]["meta"]["request_id"] == "resumable"
    assert resumed.tool_calls == 1 and resumed.terminal


def test_requery_original_explicit_ui_filters_remain_host_constraints_but_model_labels_do_not():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    source = service.begin(QueryRequest(message="这类题", request_id="explicit-scope", filters={"company": "字节"}))
    service.execute(source, "list_questions", QuerySpec(action="LIST", filters={"company": "字节", "topic_l1": "Redis"}))
    service.finish(source, provider="fixture")
    refresh = service.begin(QueryRequest(message="这类题", request_id="preserve-scope", requery_of_run_id=source.id))
    assert service.model_context(refresh)["explicit_filters"] == {"company": "字节"}
    assert service.model_context(refresh)["session"]["filters"]["topic_l1"] == "Redis"
    with pytest.raises(ValueError, match="EXPLICIT_FILTER_CONFLICT"):
        service.execute(refresh, "list_questions", QuerySpec(action="LIST"))
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(ToolInvocation).where(ToolInvocation.run_id == refresh.id)) == 0
    service.fail(refresh)
    correction = service.begin(QueryRequest(message="这次不限制公司", request_id="clear-scope",
        requery_of_run_id=source.id, filters={"company": None}))
    assert service.model_context(correction)["explicit_filters"] == {"company": None}
    result = service.execute(correction, "list_questions", QuerySpec(action="LIST"))
    assert result["facts"]["meta"]["applied_filters"]["company"] is None
    service.finish(correction, provider="fixture")


@pytest.mark.parametrize("explicit_default", [{"pipeline": "HYBRID_RERANK"}, {"page_size": 20}])
def test_requery_presence_changes_cannot_replay_same_request_receipt(explicit_default):
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    source = service.begin(QueryRequest(message="Redis列表", request_id="nondefault-source", pipeline="BM25", page_size=7))
    service.execute(source, "list_questions", QuerySpec(action="LIST", page_size=7))
    service.finish(source, provider="fixture")
    omitted = QueryRequest(message="Redis列表", request_id="omitted-options", requery_of_run_id=source.id)
    refresh = service.begin(omitted)
    assert refresh.request.pipeline == "BM25" and refresh.request.page_size == 7
    service.execute(refresh, "list_questions", QuerySpec(action="LIST", page_size=7))
    service.finish(refresh, provider="fixture")
    assert isinstance(service.begin(omitted), dict)
    changed = QueryRequest.model_validate({"message": "Redis列表", "request_id": "omitted-options",
        "requery_of_run_id": source.id, **explicit_default})
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        service.begin(changed)


def test_non_requery_completed_receipt_preserves_legacy_v9_digest_compatibility():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    body = QueryRequest(message="Redis列表", request_id="legacy-digest")
    run = service.begin(body)
    service.execute(run, "list_questions", QuerySpec(action="LIST"))
    expected = service.finish(run, provider="fixture")
    legacy = hashlib.sha256(body.model_dump_json(exclude={"requery_of_run_id"}).encode()).hexdigest()
    with db.session() as session, session.begin():
        session.get(AgentTurn, run.id).payload_hash = legacy
    assert service.begin(body) == expected


def test_chained_requeries_keep_resolved_ui_scope_and_explicit_null_clearing():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    original = service.begin(QueryRequest(message="Redis列表", request_id="chain-original", filters={"company": "字节"}))
    service.execute(original, "list_questions", QuerySpec(action="LIST", filters={"company": "字节", "topic_l1": "Redis"}))
    service.finish(original, provider="fixture")
    first = service.begin(QueryRequest(message="Redis列表", request_id="chain-first", requery_of_run_id=original.id))
    service.execute(first, "list_questions", QuerySpec(action="LIST", filters={"company": "字节"}))
    service.finish(first, provider="fixture")
    with db.session() as session:
        payload = session.get(AgentTurn, first.id).request_payload
        assert payload["filters"]["company"] is None  # Preserve the actual user input.
        assert payload["resolved_explicit_filters"] == {"company": "字节"}
    second = service.begin(QueryRequest(message="Redis列表", request_id="chain-second", requery_of_run_id=first.id))
    assert service.model_context(second)["explicit_filters"] == {"company": "字节"}
    with pytest.raises(ValueError, match="EXPLICIT_FILTER_CONFLICT"):
        service.execute(second, "list_questions", QuerySpec(action="LIST"))
    service.fail(second)
    cleared = service.begin(QueryRequest(message="Redis列表", request_id="chain-cleared",
        requery_of_run_id=first.id, filters={"company": None}))
    service.execute(cleared, "list_questions", QuerySpec(action="LIST"))
    service.finish(cleared, provider="fixture")
    next_refresh = service.begin(QueryRequest(message="Redis列表", request_id="chain-after-clear", requery_of_run_id=cleared.id))
    assert service.model_context(next_refresh)["explicit_filters"] == {"company": None}
    with pytest.raises(ValueError, match="EXPLICIT_FILTER_CONFLICT"):
        service.execute(next_refresh, "list_questions", QuerySpec(action="LIST", filters={"company": "字节"}))
    service.fail(next_refresh)


@pytest.mark.parametrize("action,tool", [("LIST", "list_questions"), ("STATS", "get_question_stats")])
def test_normal_next_preserves_original_ui_scope_for_host_bound_historical_requery(action, tool):
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    first = service.begin(QueryRequest(message="Redis高频题", request_id="paged-scope", filters={"company": "字节"}))
    service.execute(first, tool, QuerySpec(action=action, filters={"company": "字节", "topic_l1": "Redis"}, page_size=1))
    service.finish(first, provider="fixture")
    assert first.state["list_explicit_filters"] == {"company": "字节"}
    next_page = service.begin(QueryRequest(message="下一页", request_id="next-without-ui-filters",
        conversation_id=first.conversation_id, expected_version=1))
    service.execute(next_page, "list_questions", QuerySpec(action="NEXT"))
    service.finish(next_page, provider="fixture")
    assert next_page.state["list_explicit_filters"] == {"company": "字节"}
    with db.session() as session:
        assert session.get(AgentTurn, next_page.id).request_payload["filters"]["company"] is None
    refresh = service.begin(QueryRequest(message="下一页", request_id="bound-next-refresh", requery_of_run_id=next_page.id))
    assert service.model_context(refresh)["explicit_filters"] == {"company": "字节"}
    with pytest.raises(ValueError, match="EXPLICIT_FILTER_CONFLICT"):
        service.execute(refresh, "list_questions", QuerySpec(action="LIST"))
    service.fail(refresh)
    cleared = service.begin(QueryRequest(message="下一页", request_id="cleared-next-refresh",
        requery_of_run_id=next_page.id, filters={"company": None}))
    assert service.model_context(cleared)["explicit_filters"] == {"company": None}
    result = service.execute(cleared, tool, QuerySpec(action=action))
    assert result["facts"]["meta"]["applied_filters"]["company"] is None
    service.finish(cleared, provider="fixture")


def test_new_lists_replace_ui_scope_snapshot_and_search_clears_it():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    first = service.begin(QueryRequest(message="字节题", request_id="snapshot-original", filters={"company": "字节"}))
    service.execute(first, "list_questions", QuerySpec(action="LIST", filters={"company": "字节"}))
    service.finish(first, provider="fixture")
    replacement = service.begin(QueryRequest(message="全部Redis题", request_id="snapshot-replacement",
        conversation_id=first.conversation_id))
    service.execute(replacement, "list_questions", QuerySpec(action="LIST", filters={"topic_l1": "Redis"}))
    service.finish(replacement, provider="fixture")
    assert replacement.state["list_explicit_filters"] == {}

    class Retriever:
        def retrieve(self, query, eligible, pipeline, top_k):
            return {"data": [], "meta": {"pipeline": pipeline, "rerank_status": "COMPLETED"}}

    from interview_intelligence.domain.models import CorpusState
    with db.session() as session, session.begin():
        corpus = session.get(CorpusState, 1)
        corpus.current_revision = corpus.indexed_revision = 1
    service.retriever = Retriever()
    searched = service.begin(QueryRequest(message="Agent设计题", request_id="snapshot-search",
        conversation_id=first.conversation_id))
    service.execute(searched, "search_questions", QuerySpec(action="SEARCH", search_query="Agent设计题"))
    service.finish(searched, provider="fixture")
    assert "list_explicit_filters" not in searched.state


def test_legacy_next_without_ui_snapshot_does_not_invent_provenance():
    db, _, _ = seed_corpus()
    service = QueryService(db, signing_key="test")
    first = service.begin(QueryRequest(message="Redis题", request_id="legacy-list"))
    service.execute(first, "list_questions", QuerySpec(action="LIST", filters={"topic_l1": "Redis"}, page_size=1))
    service.finish(first, provider="fixture")
    with db.session() as session, session.begin():
        conversation = session.get(AgentConversation, first.conversation_id)
        state = dict(conversation.state); state.pop("list_explicit_filters", None); conversation.state = state
    next_page = service.begin(QueryRequest(message="下一页", request_id="legacy-next", conversation_id=first.conversation_id))
    service.execute(next_page, "list_questions", QuerySpec(action="NEXT"))
    service.finish(next_page, provider="fixture")
    assert "list_explicit_filters" not in next_page.state
    refresh = service.begin(QueryRequest(message="下一页", request_id="legacy-next-refresh", requery_of_run_id=next_page.id))
    assert service.model_context(refresh)["explicit_filters"] == {}
    assert refresh.state["filters"]["topic_l1"] == "Redis"
    service.fail(refresh)
