"""Bounded semantic scope through host state, paging, restore and requery.

The retriever here is a contract double; real semantic labels/model quality use
the independently frozen source oracle, not the synthetic corpus in this test.
The engineering-ALL four-turn wording now has a complete-workset contract and
is covered without a finite-pool substitute in test_complete_business_workset.
"""
from copy import deepcopy
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.domain.models import AgentTurn, CorpusState
from interview_intelligence.relevance_scope import validate_relevance_scope
from test_complete_engineering_query import fixture

ENGINEERING_ALL_MESSAGE = "列出所有工程系统设计题"
MESSAGES = ("系统设计题", "所有具体业务系统设计题",
    "所有具体业务系统设计题", "按题意查找候选")


@pytest.fixture
def catalog_receipt_at_persistence_boundary(monkeypatch):
    """Exercise private metadata after retrieval validation, independently of its schema.

    Real catalog identity/provider contracts have their own retriever tests. This
    fixture supplies an opaque sentinel only where the validated result enters
    persistent paging, so a fake catalog cannot bypass the service validator.
    """
    import interview_intelligence.agent.query_service as module
    original = module.store_candidates

    def capture(run, result, plan, **options):
        result["meta"]["catalog_selection_audit"] = {"boundary_sentinel": "private-catalog-audit"}
        return original(run, result, plan, **options)

    monkeypatch.setattr(module, "store_candidates", capture)


class ScopedRetriever:
    supports_relevance_query = True
    supports_relevance_scope = True

    def __init__(self):
        self.calls = []

    def retrieve(self, query, eligible, pipeline, top_k, **options):
        scope = options.get("relevance_scope")
        if scope is not None:
            scope = validate_relevance_scope(scope)
        self.calls.append({"query": query, "eligible": eligible, "scope": scope,
            "relevance_query": options.get("relevance_query"), "top_k": top_k})
        ids = eligible[:31] if scope else eligible[:top_k]
        rows = [{"canonical_question_id": identity,
            **({"relevance_scope_status": "PENDING", "relevance_scope_reason": "原问不能确定业务目标",
                "relevance_scope_hash": scope["scope_hash"]} if scope and index >= 24 else {})}
            for index, identity in enumerate(ids)]
        rank_meta = {}
        if scope and scope["target_unit"] == "BUSINESS_APPLICATION":
            # This double claims only a wide, bounded ranking. Reuse the audit
            # encoder so the host/pagination fixture does not forge an obsolete
            # schema; independent retriever tests validate its semantics/hash.
            from interview_intelligence.search.elasticsearch import _business_candidate_pool, _business_rank_audit
            from interview_intelligence.search.service import rrf
            ranking = [(identity, float(len(eligible) - index)) for index, identity in enumerate(eligible[:50])]
            rankings = [ranking, ranking]
            fused = rrf(*rankings)
            pool = _business_candidate_pool(fused, fused, fused)
            rank_meta["retrieval_rank_audit"] = _business_rank_audit(
                scope, eligible, eligible, None, fused, fused, rankings, rankings, pool, pipeline)
        return {"data": rows, "meta": {"pipeline": pipeline, "candidate_count": min(50, len(eligible)),
            "candidate_ids": eligible[:50], "rerank_status": "COMPLETED_PARTIAL" if scope else "COMPLETED",
            "candidate_verification_status": "PARTIAL" if scope else "COMPLETE",
            "invalid_candidate_count": 7 if scope else 0,
            **({"relevance_scope_hash": scope["scope_hash"], "relevance_scope_applied": True}
               if scope else {}),
            **rank_meta}}


def send(service, message, previous=None, *, plan=None, **options):
    if previous:
        options.setdefault("conversation_id", previous["conversation_id"])
        options.setdefault("expected_version", previous["conversation_version"])
    request = QueryRequest(message=message, request_id=str(uuid4()), **options)
    run = service.begin(request)
    proposal = plan or service.complete_collection_plan(run)
    assert proposal is not None, message
    tool = "search_questions" if proposal.action == "SEARCH" else "list_questions"
    service.execute(run, tool, proposal)
    return run, service.finish(run, provider="contract-double")


def chain(tmp_path):
    db, service = fixture(tmp_path, extra=40)
    with db.session() as session, session.begin():
        corpus = session.get(CorpusState, 1)
        corpus.current_revision = corpus.indexed_revision = 1
    service.collection_selection.threshold = 3
    retriever = ScopedRetriever()
    service.retriever = retriever
    _, generic = send(service, MESSAGES[0], plan=QuerySpec(action="SEARCH", search_query=MESSAGES[0]))
    _, preview = send(service, MESSAGES[1], generic, page_size=5)
    assert preview["intent"] == "CLARIFY"
    assert "collection_selection" not in preview["facts"]["meta"]
    narrowed, clarification = send(service, MESSAGES[2], preview, page_size=5)
    assert clarification["intent"] == "CLARIFY"
    assert narrowed.state["relevance_scope"]["target_unit"] == "BUSINESS_APPLICATION"
    assert "collection_selection" not in narrowed.state
    return db, service, retriever, clarification


def test_unqualified_business_scope_and_durable_bounded_paging_preserve_pending(tmp_path, catalog_receipt_at_persistence_boundary):
    db, service, retriever, clarification = chain(tmp_path)
    first_run, first = send(service, MESSAGES[3], clarification, page_size=5)
    scope = first["facts"]["meta"]["relevance_scope"]
    assert scope["target_unit"] == "BUSINESS_APPLICATION" and scope["excluded_domains"] == []
    assert scope["independent_only"] is False and scope["retrieval_mode"] == "CANDIDATES"
    assert "candidate_workset_snapshot" not in first_run.state
    assert "engineering_collection_authorization" not in first_run.state
    assert MESSAGES[3] not in scope["source_messages"]
    assert MESSAGES[3] not in retriever.calls[-1]["relevance_query"]
    assert retriever.calls[-1]["query"] == "\n".join(scope["source_messages"])
    assert "用户此前原话" not in retriever.calls[-1]["query"]
    assert "纠正优先" not in retriever.calls[-1]["query"]
    assert retriever.calls[-1]["top_k"] == 50
    assert first["facts"]["meta"]["scope_candidate_counts"] == {"confirmed": 24, "pending": 7, "visible": 31}
    assert any("已保留为待核验" in warning for warning in first["warnings"])
    assert not any("已排除" in warning for warning in first["warnings"])
    assert first["facts"]["meta"]["retrieval_rank_audit"]["version"] == "bounded_retrieval_rank_audit_v1"
    assert "retrieval_rank_audit" not in first_run.state["semantic_candidate_pages"]["meta"]
    assert first["facts"]["meta"]["catalog_selection_audit"]["boundary_sentinel"] == "private-catalog-audit"
    assert "catalog_selection_audit" not in first_run.state["semantic_candidate_pages"]["meta"]
    # A new host process has no in-memory run/result cache to help it.
    restored = QueryService(db, retriever, signing_key=service.signing_key,
        today=service.today, feedback_root=tmp_path / "restored-feedback")
    rows, runs, current = [], [], first
    while True:
        rows.extend(current["facts"]["data"])
        runs.append(current["run_id"])
        assert current["facts"]["meta"]["relevance_scope_hash"] == scope["scope_hash"]
        if not current["facts"]["meta"]["pagination"]["next_cursor"]:
            break
        _, current = send(restored, "下一页", current)
        assert current["intent"] == "NEXT"
        assert "retrieval_rank_audit" not in current["facts"]["meta"]
        assert "catalog_selection_audit" not in current["facts"]["meta"]
    assert len(runs) == 7 and len(rows) == len({r["canonical_question_id"] for r in rows}) == 31
    assert sum(row.get("relevance_scope_status") == "PENDING" for row in rows) == 7
    assert len(retriever.calls) == 2  # first generic query + one narrowed retrieval
    with db.session() as session:
        turns = list(session.scalars(select(AgentTurn).where(AgentTurn.conversation_id == first_run.conversation_id)))
        assert all(turn.status == "SUCCEEDED" for turn in turns)
        assert turns[-1].state_after["relevance_scope"]["scope_hash"] == scope["scope_hash"]


@pytest.mark.parametrize("source_kind", ["SEARCH", "NEXT"])
def test_requery_restores_business_goal_instead_of_control_button_or_old_wide_root(tmp_path, source_kind, catalog_receipt_at_persistence_boundary):
    _, service, retriever, clarification = chain(tmp_path)
    _, result = send(service, MESSAGES[3], clarification, page_size=5)
    first_ids = [row["canonical_question_id"] for row in result["facts"]["data"]]
    if source_kind == "NEXT":
        _, result = send(service, "下一页", result)
    scope = result["facts"]["meta"]["relevance_scope"]
    _, repeated = send(service, "重新检索", result, requery_of_run_id=result["run_id"])
    assert repeated["intent"] == "SEARCH"
    assert [row["canonical_question_id"] for row in repeated["facts"]["data"]] == first_ids
    assert repeated["facts"]["meta"]["relevance_scope_hash"] == scope["scope_hash"]
    assert retriever.calls[-1]["scope"]["excluded_domains"] == []
    assert "具体业务系统设计" in retriever.calls[-1]["relevance_query"]
    assert "按题意查找候选" not in retriever.calls[-1]["relevance_query"]
    assert retriever.calls[-1]["query"] == "\n".join(scope["source_messages"])
    assert repeated["facts"]["meta"]["semantic_recall"]["source"] == "user_messages"
    # A private ranking/catalog receipt cannot become the user's next goal.
    assert "private-catalog-audit" not in json.dumps({
        "query": retriever.calls[-1]["query"],
        "relevance_query": retriever.calls[-1]["relevance_query"],
        "semantic_recall": repeated["facts"]["meta"]["semantic_recall"],
        "scope": repeated["facts"]["meta"]["relevance_scope"],
    }, ensure_ascii=False)


@pytest.mark.parametrize("authorization", ["selected", "model_option_only", "untrusted_option", "request_mismatch", "missing_goal"])
def test_legacy_requery_recovers_only_actual_user_candidate_choice(tmp_path, authorization):
    db, service, retriever, clarification = chain(tmp_path)
    _, source = send(service, MESSAGES[3], clarification)
    with db.session() as session, session.begin():
        turn = session.get(AgentTurn, source["run_id"])
        after = deepcopy(turn.state_after)
        for key in ("relevance_scope", "semantic_candidate_pages", "semantic_recall"):
            after.pop(key, None)
        after.pop("list_request", None)
        turn.state_after = after
        before = deepcopy(turn.state_before)
        for key in ("relevance_scope", "semantic_candidate_pages", "semantic_recall"):
            before.pop(key, None)
        payload = deepcopy(turn.request_payload)
        if authorization == "model_option_only":
            turn.message = payload["message"] = MESSAGES[2]
        elif authorization == "untrusted_option":
            before["pending_clarification"]["options"] = ["查看完整工程设计集合"]
        elif authorization == "request_mismatch":
            payload["message"] = MESSAGES[2]
        elif authorization == "missing_goal":
            before["pending_clarification"]["original_message"] = ""
        turn.request_payload, turn.state_before = payload, before
    calls = len(retriever.calls)
    request = QueryRequest(message="重新检索", request_id=str(uuid4()), requery_of_run_id=source["run_id"])
    run = service.begin(request)
    plan = service.complete_collection_plan(run)
    if plan is None:
        plan = QuerySpec(action="SEARCH", search_query="旧模型业务设计扩展词")
    service.execute(run, "search_questions" if plan.action == "SEARCH" else "list_questions", plan)
    result = service.finish(run, provider="legacy-contract-double")
    if authorization == "selected":
        assert result["intent"] == "SEARCH" and len(retriever.calls) == calls + 1
        scope = result["facts"]["meta"]["relevance_scope"]
        assert scope["target_unit"] == "BUSINESS_APPLICATION" and scope["retrieval_mode"] == "CANDIDATES"
        assert scope["excluded_domains"] == [] and scope["independent_only"] is False
        assert MESSAGES[2] in scope["source_messages"] and MESSAGES[3] not in scope["source_messages"]
        assert result["facts"]["meta"]["pagination"]["offset"] == 0
        assert result["facts"]["meta"]["relevance_scope_recovery"]["source_run_id"] == source["run_id"]
        assert "旧模型" not in retriever.calls[-1]["query"]
    else:
        assert result["intent"] == "CLARIFY" and len(retriever.calls) == calls
        assert "relevance_scope_recovery" not in run.state


def test_all_after_narrowing_does_not_resurrect_broad_collection(tmp_path):
    _, service, retriever, clarification = chain(tmp_path)
    _, result = send(service, "全量分页", clarification)
    assert result["intent"] == "CLARIFY" and result["facts"]["data"] == []
    assert "不能把有限候选当作全库全集" in result["answer"]
    assert "collection_selection" not in result["facts"]["meta"]
    assert len(retriever.calls) == 1


def test_new_goal_resets_business_scope_and_does_not_page_stale_candidates(tmp_path):
    _, service, retriever, clarification = chain(tmp_path)
    _, result = send(service, MESSAGES[3], clarification)
    new, changed = send(service, "改查Redis原理题", result,
        plan=QuerySpec(action="SEARCH", search_query="Redis 原理"))
    assert changed["intent"] == "SEARCH" and "relevance_scope" not in new.state
    assert "semantic_candidate_pages" not in new.state and retriever.calls[-1]["scope"] is None


def test_candidate_cursor_rejects_changed_annotation_revision(tmp_path):
    db, service, _, clarification = chain(tmp_path)
    _, result = send(service, MESSAGES[3], clarification)
    with db.session() as session, session.begin():
        state = session.get(CorpusState, 1)
        state.task_annotation_revision += 1
    with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
        send(service, "下一页", result)


def test_candidate_choice_is_deterministic_even_with_bad_clarification_plan(tmp_path):
    _, service, _, clarification = chain(tmp_path)
    _, result = send(service, MESSAGES[3], clarification,
        plan=QuerySpec(action="CLARIFY", clarification="是否只要传统后端？"))
    assert result["intent"] == "SEARCH"
    assert result["facts"]["meta"]["relevance_scope"]["excluded_domains"] == []


def test_repeated_exclude_allow_exclude_keeps_last_user_correction(tmp_path):
    _, service, retriever, clarification = chain(tmp_path)
    _, result = send(service, MESSAGES[3], clarification)
    for message, excluded in [("不要AI题", ["AI"]), ("不要排除AI业务应用", []), ("不要AI题", ["AI"])]:
        _, result = send(service, message, result)
        assert result["intent"] == "SEARCH"
        assert retriever.calls[-1]["scope"]["excluded_domains"] == excluded
        assert retriever.calls[-1]["scope"]["target_unit"] == "BUSINESS_APPLICATION"


def test_new_scope_correction_does_not_reuse_old_soft_recall_query(tmp_path):
    _, service, retriever, clarification = chain(tmp_path)
    _, first = send(service, MESSAGES[3], clarification,
        plan=QuerySpec(action="SEARCH", search_query="业务系统软扩展词"))
    assert retriever.calls[-1]["query"] == "业务系统软扩展词"
    _, corrected = send(service, "只看订单业务的超时关闭和库存释放设计题", first)
    assert "只看订单业务的超时关闭和库存释放设计题" in retriever.calls[-1]["query"]
    assert "业务系统软扩展词" not in retriever.calls[-1]["query"]
    assert corrected["facts"]["meta"]["semantic_recall"]["source"] == "user_messages"


def test_soft_recall_limit_keeps_latest_correction_and_full_scope_evidence():
    from interview_intelligence.agent.query_scope import recall_intent, relevance_intent

    messages = ["原先找业务设计题。" * 100, "只看预约候补转正和释放号源的业务流程设计题", "按题意查找候选"]
    query = recall_intent(messages)
    assert len(query) <= 500 and query.endswith(messages[1])
    assert "用户此前原话" not in query and messages[2] not in query
    # Recall truncation has no authority to truncate semantic authorization.
    assert messages[0] in relevance_intent(messages) and messages[1] in relevance_intent(messages)


@pytest.mark.parametrize("message", ["这类题再找一些", "这些问题继续检索更多。", "同类题目再查一批"])
def test_content_free_followup_keeps_business_scope_through_host_message_restoration(tmp_path, message):
    _, service, retriever, clarification = chain(tmp_path)
    _, first = send(service, MESSAGES[3], clarification)
    _, followed = send(service, message, first)
    assert followed["intent"] == "SEARCH"
    assert followed["facts"]["meta"]["relevance_scope_hash"] == first["facts"]["meta"]["relevance_scope_hash"]
    assert retriever.calls[-1]["scope"]["target_unit"] == "BUSINESS_APPLICATION"
    assert retriever.calls[-1]["scope"]["excluded_domains"] == []
    assert message not in retriever.calls[-1]["query"]


def test_same_class_followup_with_exclusion_retains_business_goal_and_new_condition(tmp_path):
    _, service, retriever, clarification = chain(tmp_path)
    _, first = send(service, MESSAGES[3], clarification)
    message = "同类题目再查一批，排除AI"
    _, followed = send(service, message, first)
    scope = retriever.calls[-1]["scope"]
    assert followed["intent"] == "SEARCH" and scope["target_unit"] == "BUSINESS_APPLICATION"
    assert scope["excluded_domains"] == ["AI"] and message in scope["source_messages"]


def test_next_with_changed_explicit_filter_retrieves_new_scoped_snapshot(tmp_path):
    _, service, retriever, clarification = chain(tmp_path)
    _, first = send(service, MESSAGES[3], clarification, page_size=5)
    calls = len(retriever.calls)
    _, changed = send(service, "下一页", first, filters={"company": "字节"})
    assert changed["intent"] == "SEARCH" and len(retriever.calls) == calls + 1
    assert changed["facts"]["meta"]["pagination"]["offset"] == 0
    assert changed["facts"]["meta"]["applied_filters"]["company"] == "字节"
    assert changed["facts"]["meta"]["relevance_scope_hash"] == first["facts"]["meta"]["relevance_scope_hash"]
    _, requery = send(service, "重查", changed, requery_of_run_id=changed["run_id"])
    assert requery["facts"]["meta"]["applied_filters"]["company"] == "字节"


def test_short_page_alias_keeps_scope_and_does_not_retrieve_again(tmp_path):
    _, service, retriever, clarification = chain(tmp_path)
    _, first = send(service, MESSAGES[3], clarification, page_size=5)
    calls = len(retriever.calls)
    _, second = send(service, "下页", first)
    assert second["intent"] == "NEXT" and len(retriever.calls) == calls
    assert second["facts"]["meta"]["relevance_scope_hash"] == first["facts"]["meta"]["relevance_scope_hash"]


@pytest.mark.parametrize("changed_binding", ["owner", "conversation", "scope_hash", "result_hash", "page_size", "filters_hash"])
def test_candidate_cursor_cannot_cross_boundaries(tmp_path, changed_binding):
    from interview_intelligence.analytics.scope import sign_scope, verify_scope
    _, service, _, clarification = chain(tmp_path)
    _, first = send(service, MESSAGES[3], clarification, page_size=5)
    run = service.begin(QueryRequest(message="下一页", request_id=str(uuid4()),
        conversation_id=first["conversation_id"], expected_version=first["conversation_version"]))
    cursor = run.state["list_request"]["cursor"]
    payload = verify_scope(cursor, key=service.signing_key, corpus_revision=run.revisions[0])
    payload[changed_binding] = 37 if changed_binding == "page_size" else "different-binding"
    run.state["list_request"]["cursor"] = sign_scope(payload, key=service.signing_key,
        corpus_revision=run.revisions[0])
    with pytest.raises(ValueError, match="INVALID_SCOPE"):
        service.execute(run, "list_questions", QuerySpec(action="NEXT"))


def test_api_reload_reads_durable_scope_then_continues_without_planner(tmp_path):
    from fastapi.testclient import TestClient
    from interview_intelligence.api import create_app
    from interview_intelligence.config import Settings
    from test_complete_engineering_query import NoPlanner
    db, service, retriever, clarification = chain(tmp_path)
    _, first = send(service, MESSAGES[3], clarification, page_size=5)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:",
        snapshot_root=tmp_path / "snapshots", query_router_enabled=True,
        query_prompt_version="query_agent_v15",
        app_signing_key=service.signing_key)
    client = TestClient(create_app(db, settings, retriever, query_planner=NoPlanner()))
    restored = client.get(f"/api/conversations/{first['conversation_id']}")
    assert restored.status_code == 200, restored.text
    state = restored.json()["data"]["state"]
    assert state["relevance_scope"]["scope_hash"] == first["facts"]["meta"]["relevance_scope_hash"]
    calls = len(retriever.calls)
    next_page = client.post("/api/query", json={"message": "下一页", "request_id": str(uuid4()),
        "conversation_id": first["conversation_id"], "expected_version": restored.json()["data"]["version"]})
    assert next_page.status_code == 200, next_page.text
    assert next_page.json()["meta"]["pagination"]["kind"] == "semantic_candidates"
    assert next_page.json()["meta"]["pagination"]["offset"] == 5
    assert len(retriever.calls) == calls


def test_literal_company_is_planned_and_new_goal_does_not_inherit_old_inference(tmp_path):
    _, service, retriever, clarification = chain(tmp_path)
    _, first = send(service, MESSAGES[3], clarification)
    _, filtered = send(service, "只看字节的具体业务系统设计题", first,
        plan=QuerySpec(action="SEARCH", search_query="业务设计", filters={"company": "字节"}))
    assert filtered["facts"]["meta"]["applied_filters"]["company"] == "字节"
    _, fresh = send(service, "改查具体业务系统设计题", filtered,
        plan=QuerySpec(action="SEARCH", search_query="业务设计", filters={"company": "字节"}))
    assert fresh["facts"]["meta"]["applied_filters"]["company"] is None
    assert retriever.calls[-1]["scope"]["target_unit"] == "BUSINESS_APPLICATION"


def test_explanation_keeps_durable_page_and_scope(tmp_path):
    _, service, retriever, clarification = chain(tmp_path)
    _, first = send(service, MESSAGES[3], clarification, page_size=5)
    before = len(retriever.calls)
    request = QueryRequest(message="第二题怎么回答", request_id=str(uuid4()),
        conversation_id=first["conversation_id"], expected_version=first["conversation_version"])
    run = service.begin(request)
    assert service.complete_collection_plan(run) is None
    service.execute(run, "answer_question", QuerySpec(action="ANSWER", answer_text="示例解答",
        answer_kind="EXPLAIN", answer_basis="GENERAL_KNOWLEDGE"))
    explained = service.finish(run, provider="contract-double")
    assert run.state["relevance_scope"]["scope_hash"] == first["facts"]["meta"]["relevance_scope_hash"]
    _, second = send(service, "下一页", explained)
    assert second["intent"] == "NEXT" and second["facts"]["meta"]["pagination"]["offset"] == 5
    assert len(retriever.calls) == before


def test_changed_page_size_restarts_candidates_without_changing_goal(tmp_path):
    _, service, retriever, clarification = chain(tmp_path)
    _, first = send(service, MESSAGES[3], clarification, page_size=5)
    before = len(retriever.calls)
    _, resized = send(service, "下一页", first, page_size=1)
    assert resized["intent"] == "SEARCH" and len(retriever.calls) == before + 1
    assert resized["facts"]["meta"]["pagination"]["offset"] == 0
    assert resized["facts"]["meta"]["pagination"]["page_size"] == 1
    assert resized["facts"]["meta"]["relevance_scope_hash"] == first["facts"]["meta"]["relevance_scope_hash"]


@pytest.mark.parametrize("qualifier,excluded,independent", [
    ("不包括AI", ["AI"], False), ("只看独立系统设计", [], True)])
def test_nonbusiness_constraint_invalidates_old_broad_menu_and_survives_next(tmp_path, qualifier, excluded, independent):
    db, service = fixture(tmp_path, extra=40)
    with db.session() as session, session.begin():
        corpus = session.get(CorpusState, 1)
        corpus.current_revision = corpus.indexed_revision = 1
    service.collection_selection.threshold = 3
    retriever = ScopedRetriever()
    service.retriever = retriever
    _, menu = send(service, ENGINEERING_ALL_MESSAGE)
    assert menu["facts"]["meta"]["collection_selection"]
    _, narrowed = send(service, ENGINEERING_ALL_MESSAGE + " " + qualifier, menu,
        plan=QuerySpec(action="SEARCH", search_query="工程设计"))
    assert narrowed["intent"] == "CLARIFY"
    _, first = send(service, MESSAGES[3], narrowed, page_size=5)
    scope = first["facts"]["meta"]["relevance_scope"]
    assert scope["target_unit"] == "ANY_ENGINEERING" and scope["excluded_domains"] == excluded
    assert scope["independent_only"] is independent
    _, second = send(service, "下一页", first)
    assert second["intent"] == "NEXT" and "collection_selection" not in second["facts"]["meta"]
    assert second["facts"]["meta"]["relevance_scope_hash"] == scope["scope_hash"]


def test_conflicting_provider_plan_fails_before_tools_with_private_receipt(tmp_path, monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from interview_intelligence.agent.model_gateway import ModelGateway
    from interview_intelligence.api import create_app
    from interview_intelligence.config import Settings
    from interview_intelligence.domain.models import ToolInvocation
    db, _ = fixture(tmp_path)
    raw = QuerySpec(action="SEARCH", search_query="设计任务").model_dump(mode="json")
    raw["semantic_task"] = "ENGINEERING_DESIGN"
    async def complete(self, run, payload, emit=None):
        run.model_calls += 1
        return {"choices": [{"message": {"content": json.dumps(raw, ensure_ascii=False)}}]}
    monkeypatch.setattr(ModelGateway, "complete", complete)
    app = create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:",
        snapshot_root=tmp_path / "snapshots", query_router_enabled=True, pi_agent_enabled=False))
    client = TestClient(app)
    response = client.post("/api/query", json={"message": "查一组设计任务", "request_id": "invalid-upstream-plan"})
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "QUERY_MODEL_PLAN_INVALID"
    assert "input_value" not in response.text and "ENGINEERING_DESIGN" not in response.text
    with db.session() as session:
        turn = session.scalar(select(AgentTurn).where(AgentTurn.request_id == "invalid-upstream-plan"))
        assert turn.status == "FAILED" and turn.error_code == "QUERY_MODEL_PLAN_INVALID"
        assert list(session.scalars(select(ToolInvocation).where(ToolInvocation.run_id == turn.id))) == []
    events = client.get(f"/api/runs/{turn.id}").json()["data"]["events"]
    private = next(event for event in events if event["type"] == "model_response")
    assert json.loads(private["data"]["content"]) == raw
