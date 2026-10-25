"""Durable host continuation with real SQL/source eligibility and a proof double.

The imported backend fixture is a deterministic source-proof contract double;
these tests establish persistence/authorization/paging, not model semantics.
"""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import update

from interview_intelligence.agent import complete_workset as pages
from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.domain.models import AgentTurn, QuestionOccurrence
from interview_intelligence.search.business_scope import CLARIFIED_ADJUDICATION_CONTRACT
from test_engineering_candidate_workset import setup_workset, LastForty
from test_clarified_business_scope import ScopedRetriever


def setup(tmp_path, monkeypatch, extra=61):
    db, retriever, client, ranker, _, _ = setup_workset(tmp_path, monkeypatch, extra=extra)
    retriever.catalog_selector = LastForty()
    service = QueryService(db, retriever, signing_key="complete-pages", max_tokens=96000,
        deadline_seconds=180, collection_selection_threshold=10, feedback_root=tmp_path / "feedback")
    return db, service, retriever, client, ranker


def send(service, message, previous=None, *, plan=None, **options):
    if previous:
        options.setdefault("conversation_id", previous["conversation_id"])
        options.setdefault("expected_version", previous["conversation_version"])
    request = QueryRequest(message=message, request_id=str(uuid4()), **options)
    run = service.begin(request)
    if isinstance(run, dict):
        return None, run
    plan = plan or service.complete_collection_plan(run)
    assert plan is not None
    service.execute(run, "search_questions" if plan.action == "SEARCH" else "list_questions", plan)
    return run, service.finish(run, provider="workset-contract-double")


def chain(service, *, mode="按题意查找候选"):
    _, broad = send(service, "列出所有工程系统设计题", page_size=5)
    assert broad["intent"] == "CLARIFY"
    _, refined = send(service, "所有工程系统设计题 具体业务系统设计", broad)
    assert refined["intent"] == "CLARIFY" and "全量分页" in refined["answer"]
    return send(service, mode, refined)


def test_full_ids_are_durable_before_first_verification_and_pages_resume_across_services(tmp_path, monkeypatch):
    db, service, retriever, client, ranker = setup(tmp_path, monkeypatch)
    original = retriever.hydrate_and_rerank_ids
    def checked(query, identities, **kwargs):
        with db.session() as session:
            turns = session.query(AgentTurn).filter(AgentTurn.status == "RUNNING").all()
            snapshot = turns[-1].state_after[pages.STATE_KEY]
            assert len(snapshot["manifest"]["candidate_ids"]) == 65
            assert snapshot["frontier"] == 0  # the complete universe was committed first
        return original(query, identities, **kwargs)
    monkeypatch.setattr(retriever, "hydrate_and_rerank_ids", checked)
    run, response = chain(service)
    monkeypatch.setattr(retriever, "hydrate_and_rerank_ids", original)
    assert len(ranker.calls) == 1 and len(ranker.calls[0]) == 20
    assert len(response["facts"]["data"]) == 5
    assert len(run.state[pages.STATE_KEY]["statuses"]) == 65
    ids = []
    while True:
        meta = response["facts"]["meta"]
        ids.extend(row["canonical_question_id"] for row in response["facts"]["data"])
        attempts, backend_calls = len(ranker.calls), len(client.calls)
        view = service.journal.view(response["run_id"])
        assert view["result"]["facts"]["data"] == response["facts"]["data"]
        assert (len(ranker.calls), len(client.calls)) == (attempts, backend_calls)
        if not meta["pagination"]["next_cursor"]: break
        processing = meta["pagination"]["next_requires_processing"]
        service = QueryService(db, retriever, signing_key="complete-pages", max_tokens=96000,
            deadline_seconds=180, feedback_root=tmp_path / "feedback")
        run, response = send(service, "下一页", response)
        assert response["intent"] == "NEXT"
        assert len(ranker.calls) == attempts + int(processing)
        if not processing: assert len(client.calls) == backend_calls
    snapshot = run.state[pages.STATE_KEY]
    assert snapshot["frontier"] == 65
    assert [len(batch) for batch in ranker.calls] == [20, 20, 20, 5]
    assert ids == [identity for identity in snapshot["manifest"]["candidate_ids"] if identity != "cache"]
    assert len(ids) == len(set(ids)) == 64 and "other" in ids and "agent" in ids
    assert snapshot["statuses"]["other"] == snapshot["statuses"]["agent"] == "PENDING"
    assert "最多50" not in response["answer"]
    assert "candidate_pool_limit" not in response["facts"]["meta"]


def test_all_choice_persists_and_requery_restarts_without_clarification(tmp_path, monkeypatch):
    db, service, retriever, _, ranker = setup(tmp_path, monkeypatch)
    run, response = chain(service, mode="全量分页")
    assert run.state["complete_workset_mode"] == "ALL" and len(ranker.calls) == 1
    first_hash = response["facts"]["meta"]["relevance_scope_hash"]
    _, cached = send(service, "下一页", response)
    restarted, new = send(service, "重新检索", requery_of_run_id=cached["run_id"])
    assert new["intent"] == "SEARCH" and new["facts"]["meta"]["pagination"]["offset"] == 0
    assert new["facts"]["meta"]["relevance_scope_hash"] == first_hash
    assert restarted.state["complete_workset_mode"] == "ALL" and len(ranker.calls) == 2
    assert len(restarted.state[pages.STATE_KEY]["manifest"]["candidate_ids"]) == 65


def test_adaptive_prefix_keeps_the_whole_scope_through_refresh_paging_and_requery(tmp_path, monkeypatch):
    db, service, retriever, client, ranker = setup(tmp_path, monkeypatch)
    original = retriever.hydrate_and_rerank_ids
    offered_windows, actual_prefixes = [], []
    def partial(query, identities, **kwargs):
        # Exercise the production SQL/host continuation with a neutral
        # capacity-protocol double, independently of model semantics.
        selected = identities[:7]
        options = {**kwargs, "target_catalog": {**kwargs["target_catalog"],
            "titles": {identity: kwargs["target_catalog"]["titles"][identity] for identity in selected}}}
        result = original(query, selected, **options)
        result["processed_candidate_ids"] = list(selected)
        result["meta"]["workset_batch_audit"].update(offered_candidate_ids=list(identities),
            processed_candidate_ids=list(selected), unprocessed_candidate_ids=identities[len(selected):])
        offered_windows.append(list(identities))
        actual_prefixes.append(list(selected))
        return result
    monkeypatch.setattr(retriever, "hydrate_and_rerank_ids", partial)
    run, response = chain(service, mode="全量分页")
    manifest = deepcopy(run.state[pages.STATE_KEY]["manifest"])
    scope = deepcopy(run.state["relevance_scope"])
    assert run.state[pages.STATE_KEY]["frontier"] == 7
    seen = []
    while True:
        data, meta = response["facts"]["data"], response["facts"]["meta"]
        seen.extend(row["canonical_question_id"] for row in data)
        calls = len(ranker.calls), len(client.calls)
        # GET and service recreation represent durable refresh, with no
        # reranker, retrieval or clarification on already committed results.
        assert service.journal.view(response["run_id"])["result"]["facts"]["data"] == data
        fresh = QueryService(db, retriever, signing_key="complete-pages", max_tokens=96000,
            deadline_seconds=180, feedback_root=tmp_path / "refreshed-feedback")
        assert (len(ranker.calls), len(client.calls)) == calls
        if not meta["pagination"]["next_cursor"]:
            service = fresh
            break
        processing = meta["pagination"]["next_requires_processing"]
        run, response = send(fresh, "下一页", response)
        service = fresh
        assert response["intent"] == "NEXT" and run.state["complete_workset_mode"] == "ALL"
        assert run.state["relevance_scope"] == scope
        assert run.state[pages.STATE_KEY]["manifest"] == manifest
        assert len(ranker.calls) == calls[0] + int(processing)
        if not processing:
            assert len(client.calls) == calls[1]
    assert [identity for prefix in actual_prefixes for identity in prefix] == manifest["candidate_ids"]
    assert seen == [identity for identity in manifest["candidate_ids"] if identity != "cache"]
    assert len(seen) == len(set(seen)) == 64
    snapshot = run.state[pages.STATE_KEY]
    assert snapshot["frontier"] == 65 and snapshot["statuses"]["agent"] == snapshot["statuses"]["other"] == "PENDING"
    assert all(receipt["candidate_ids"] == selected and receipt["offered_candidate_ids"] == offered
        for receipt, selected, offered in zip(snapshot["batch_receipts"], actual_prefixes, offered_windows))
    calls = len(ranker.calls)
    restarted, result = send(service, "重新检索", requery_of_run_id=response["run_id"])
    assert result["intent"] == "SEARCH" and restarted.state["complete_workset_mode"] == "ALL"
    assert restarted.state["relevance_scope"] == scope and scope["excluded_domains"] == []
    assert restarted.state[pages.STATE_KEY]["manifest"]["candidate_ids"] == manifest["candidate_ids"]
    assert restarted.state[pages.STATE_KEY]["frontier"] == 7 and len(ranker.calls) == calls + 1


def test_cached_cursor_checks_source_changes_without_a_revision_bump(tmp_path, monkeypatch):
    db, service, _, client, ranker = setup(tmp_path, monkeypatch)
    _, response = chain(service)
    counts = len(client.calls), len(ranker.calls)
    with db.session() as session, session.begin():
        session.execute(update(QuestionOccurrence).where(QuestionOccurrence.id == "o-003")
            .values(raw_question="changed without revision"))
    run = service.begin(QueryRequest(message="下一页", request_id=str(uuid4()),
        conversation_id=response["conversation_id"], expected_version=response["conversation_version"]))
    with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
        service.execute(run, "list_questions", service.complete_collection_plan(run))
    service.fail(run, ValueError("SNAPSHOT_CHANGED"))
    assert (len(client.calls), len(ranker.calls)) == counts


def test_page_size_change_repages_cached_rows_and_completed_request_is_idempotent(tmp_path, monkeypatch):
    _, service, _, _, ranker = setup(tmp_path, monkeypatch)
    _, response = chain(service)
    run, response = send(service, "下一页", response, page_size=7)
    assert response["intent"] == "SEARCH" and len(response["facts"]["data"]) == 7
    assert len(ranker.calls) == 1
    assert service.begin(run.request) == response
    assert len(ranker.calls) == 1


def test_a_negative_batch_reports_progress_and_next_processes_once_without_clarifying(tmp_path, monkeypatch):
    _, service, retriever, _, ranker = setup(tmp_path, monkeypatch)
    original = retriever.hydrate_and_rerank_ids
    def negative(query, identities, **kwargs):
        return {"data": [], "outcomes": [{"canonical_question_id": identity, "status": "EXCLUDED",
            "source_verified_negative": True, "scope_adjudication_contract": CLARIFIED_ADJUDICATION_CONTRACT,
            "scope_adjudication_status": "VERIFIED", "scope_verification_status": "SCOPE_REJECTED",
            "relevance_scope_hash": kwargs["relevance_scope"]["scope_hash"]} for identity in identities], "meta": {}}
    monkeypatch.setattr(retriever, "hydrate_and_rerank_ids", negative)
    _, response = chain(service)
    progress = response["facts"]["meta"]["candidate_workset_progress"]
    assert response["intent"] == "SEARCH" and response["facts"]["data"] == []
    assert (progress["processed"], progress["excluded"], progress["unprocessed"]) == (20, 20, 45)
    assert progress["next_requires_processing"] and "45 道候选未核验" in response["answer"]
    monkeypatch.setattr(retriever, "hydrate_and_rerank_ids", original)
    _, following = send(service, "下一页", response)
    assert following["intent"] == "NEXT" and len(ranker.calls) == 1
    assert following["facts"]["meta"]["candidate_workset_progress"]["processed"] == 40


@pytest.mark.parametrize("fault", ["legacy_bool_only", "unresolved", "old_contract", "wrong_scope"])
def test_unreviewed_negative_outcomes_stay_visible_through_cached_paging(tmp_path, monkeypatch, fault):
    _, service, retriever, _, ranker = setup(tmp_path, monkeypatch)
    calls = []
    def unreviewed(query, identities, **kwargs):
        calls.append(list(identities))
        proofs = []
        for identity in identities:
            proof = {"canonical_question_id": identity, "status": "EXCLUDED", "source_verified_negative": True,
                "reason": "已由当前原问证据核验为范围外", "scope_adjudication_contract": CLARIFIED_ADJUDICATION_CONTRACT,
                "scope_adjudication_status": "VERIFIED", "scope_verification_status": "SCOPE_REJECTED",
                "relevance_scope_hash": kwargs["relevance_scope"]["scope_hash"]}
            if fault == "legacy_bool_only":
                for key in ("scope_adjudication_contract", "scope_adjudication_status", "scope_verification_status"):
                    proof.pop(key)
            elif fault == "unresolved": proof["scope_adjudication_status"] = "UNRESOLVED"
            elif fault == "old_contract": proof["scope_adjudication_contract"] = "primary-only-old-contract"
            elif fault == "wrong_scope": proof["relevance_scope_hash"] = "0" * 64
            proofs.append(proof)
        return {"data": [], "outcomes": proofs, "meta": {}}
    monkeypatch.setattr(retriever, "hydrate_and_rerank_ids", unreviewed)
    _, result = chain(service)
    progress = result["facts"]["meta"]["candidate_workset_progress"]
    assert (progress["processed"], progress["pending"], progress["excluded"], progress["visible"]) == (20, 20, 0, 20)
    assert len(result["facts"]["data"]) == 5
    assert all(row["relevance_scope_status"] == "PENDING" and "范围外" not in row["relevance_scope_reason"]
        for row in result["facts"]["data"])
    _, following = send(service, "下一页", result)
    assert following["intent"] == "NEXT" and len(following["facts"]["data"]) == 5
    assert len(calls) == 1 and not ranker.calls
    assert following["facts"]["meta"]["candidate_workset_progress"]["processed"] == 20


def test_all_after_candidate_selection_reuses_the_complete_manifest_and_cached_rows(tmp_path, monkeypatch):
    _, service, _, _, ranker = setup(tmp_path, monkeypatch)
    run, response = chain(service)
    manifest = deepcopy(run.state[pages.STATE_KEY]["manifest"])
    switched, result = send(service, "全量分页", response)
    assert result["intent"] == "SEARCH" and len(ranker.calls) == 1
    assert switched.state["complete_workset_mode"] == "ALL"
    assert switched.state[pages.STATE_KEY]["manifest"] == manifest


def test_original_authorization_matches_accepted_user_receipt_and_is_actor_bound(tmp_path, monkeypatch):
    db, service, retriever, _, _ = setup(tmp_path, monkeypatch)
    raw = "  所有工程系统设计题\n具体业务系统设计  "
    source, clarification = send(service, raw, page_size=5)
    run, response = send(service, "按题意查找候选", clarification)
    authorization = run.state["engineering_collection_authorization"]
    # QueryRequest's existing contract strips outer whitespace before storage.
    # The workset evidence preserves the accepted receipt verbatim, including
    # its internal newline, rather than using a planner's rewrite.
    assert source.request.message == raw.strip()
    assert authorization["source_messages"] == [source.request.message]
    assert authorization["source_evidence"][0]["quote"] == source.request.message
    other = QueryService(db, retriever, user_id="different", signing_key="complete-pages", feedback_root=tmp_path / "other")
    with pytest.raises(KeyError, match="RUN_NOT_FOUND"):
        other.journal.view(response["run_id"])


def test_explicit_filter_change_rebuilds_only_the_authorized_intersection(tmp_path, monkeypatch):
    _, service, _, _, ranker = setup(tmp_path, monkeypatch)
    _, response = chain(service)
    run, filtered = send(service, "下一页", response, filters={"company": "字节", "round": "SECOND"})
    assert filtered["intent"] == "SEARCH" and len(ranker.calls) == 2
    snapshot = run.state[pages.STATE_KEY]
    assert snapshot["manifest"]["candidate_ids"] == ["cache"]
    assert snapshot["frontier"] == 1 and snapshot["statuses"]["cache"] == "EXCLUDED"


def test_actual_four_messages_select_complete_workset_without_a_planner(tmp_path, monkeypatch):
    _, service, retriever, _, ranker = setup(tmp_path, monkeypatch)
    service.retriever = ScopedRetriever()
    _, generic = send(service, "系统设计题", plan=QuerySpec(action="SEARCH", search_query="系统设计题"))
    assert generic["intent"] == "SEARCH" and "candidate_workset_progress" not in generic["facts"]["meta"]
    service.retriever = retriever
    _, broad = send(service, "列出所有工程系统设计题", generic, page_size=5)
    _, refinement = send(service, "所有工程系统设计题 具体业务系统设计", broad)
    run, result = send(service, "按题意查找候选", refinement)
    assert [generic["intent"], broad["intent"], refinement["intent"], result["intent"]] == ["SEARCH", "CLARIFY", "CLARIFY", "SEARCH"]
    assert len(run.state[pages.STATE_KEY]["manifest"]["candidate_ids"]) == 65
    assert len(ranker.calls) == 1 and result["facts"]["meta"]["candidate_workset_progress"]["processed"] == 20
    assert run.state["relevance_scope"]["excluded_domains"] == []
    assert run.state["relevance_scope"]["independent_only"] is False


def test_ordinary_business_search_does_not_gain_an_engineering_hard_universe(tmp_path, monkeypatch):
    _, service, _, _, _ = setup(tmp_path, monkeypatch)
    bounded = ScopedRetriever()
    service.retriever = bounded
    run, result = send(service, "查找具体业务系统设计题", plan=QuerySpec(action="SEARCH", search_query="业务系统"), page_size=5)
    assert result["intent"] == "SEARCH" and bounded.calls
    assert "project" in bounded.calls[-1]["eligible"]
    assert pages.STATE_KEY not in run.state and "engineering_collection_authorization" not in run.state
    assert result["facts"]["meta"]["candidate_pool_limit"] == 50


@pytest.mark.parametrize("operation", ["first_batch", "next_batch"])
def test_interrupted_negative_batch_resumes_its_committed_progress_without_processing_twice(tmp_path, monkeypatch, operation):
    db, service, retriever, _, _ = setup(tmp_path, monkeypatch)
    calls = []
    def negative(query, identities, **kwargs):
        calls.append(list(identities))
        return {"data": [], "outcomes": [{"canonical_question_id": identity, "status": "EXCLUDED",
            "source_verified_negative": True, "scope_adjudication_contract": CLARIFIED_ADJUDICATION_CONTRACT,
            "scope_adjudication_status": "VERIFIED", "scope_verification_status": "SCOPE_REJECTED",
            "relevance_scope_hash": kwargs["relevance_scope"]["scope_hash"]} for identity in identities],
            "meta": {"workset_batch_audit": {"fixture": "negative"}}}
    monkeypatch.setattr(retriever, "hydrate_and_rerank_ids", negative)
    _, broad = send(service, "列出所有工程系统设计题", page_size=5)
    _, refinement = send(service, "所有工程系统设计题 具体业务系统设计", broad)
    if operation == "next_batch":
        _, refinement = send(service, "按题意查找候选", refinement)
    expected_batches = 2 if operation == "next_batch" else 1
    request = QueryRequest(message="下一页" if operation == "next_batch" else "按题意查找候选", request_id=str(uuid4()),
        conversation_id=refinement["conversation_id"], expected_version=refinement["conversation_version"])
    run = service.begin(request)
    original_page = pages.page
    def interrupted(*args, **kwargs): raise RuntimeError("presentation interrupted after batch commit")
    monkeypatch.setattr(pages, "page", interrupted)
    with pytest.raises(RuntimeError):
        plan = service.complete_collection_plan(run)
        service.execute(run, "search_questions" if plan.action == "SEARCH" else "list_questions", plan)
    service.fail(run, RuntimeError("presentation interrupted after batch commit"))
    monkeypatch.setattr(pages, "page", original_page)
    fresh = QueryService(db, retriever, signing_key="complete-pages", max_tokens=96000,
        deadline_seconds=180, feedback_root=tmp_path / "feedback")
    restored = fresh.begin(request)
    plan = fresh.complete_collection_plan(restored)
    fresh.execute(restored, "search_questions" if plan.action == "SEARCH" else "list_questions", plan)
    result = fresh.finish(restored, provider="fixture-resume")
    assert len(calls) == expected_batches and result["facts"]["data"] == []
    assert result["facts"]["meta"]["candidate_workset_progress"]["processed"] == 20 * expected_batches
    assert len(restored.state[pages.STATE_KEY]["batch_receipts"]) == expected_batches
    _, following = send(fresh, "下一页", result)
    assert len(calls) == expected_batches + 1
    assert following["facts"]["meta"]["candidate_workset_progress"]["processed"] == 20 * (expected_batches + 1)


def test_new_goal_clears_workset_permission_and_raw_model_context_cannot_see_private_universe(tmp_path, monkeypatch):
    _, service, _, _, _ = setup(tmp_path, monkeypatch)
    run, response = chain(service)
    service.compact_context = False
    probe = service.begin(QueryRequest(message="讲解第一题", request_id=str(uuid4()),
        conversation_id=response["conversation_id"], expected_version=response["conversation_version"]))
    context = service.model_context(probe)
    assert pages.STATE_KEY not in context["session"]
    assert "engineering_collection_authorization" not in context["session"]
    service.fail(probe, ValueError("fixture end"))
    next_run = service.begin(QueryRequest(message="换个问题，只查Redis原理题", request_id=str(uuid4()),
        conversation_id=response["conversation_id"], expected_version=response["conversation_version"]))
    assert service.complete_collection_plan(next_run) is None
    assert pages.STATE_KEY not in next_run.state and "engineering_collection_authorization" not in next_run.state
    service.fail(next_run, ValueError("fixture end"))


@pytest.mark.parametrize("receipt", ["selected", "model_option_only", "untrusted_option", "request_mismatch", "missing_goal"])
def test_legacy_engineering_all_candidate_receipt_requires_actual_user_selection(tmp_path, monkeypatch, receipt):
    from copy import deepcopy
    from interview_intelligence.domain.models import AgentTurn

    db, service, retriever, _, ranker = setup(tmp_path, monkeypatch)
    _, source = chain(service)
    with db.session() as session, session.begin():
        turn = session.get(AgentTurn, source["run_id"])
        after, before, payload = deepcopy(turn.state_after), deepcopy(turn.state_before), deepcopy(turn.request_payload)
        for state in (after, before):
            for key in (pages.STATE_KEY, "engineering_collection_authorization", "complete_workset_mode",
                    "relevance_scope", "semantic_candidate_pages", "semantic_recall"):
                state.pop(key, None)
        after.pop("list_request", None)
        if receipt == "model_option_only":
            turn.message = payload["message"] = "所有工程系统设计题 具体业务系统设计"
        elif receipt == "untrusted_option":
            before["pending_clarification"]["options"] = ["查看完整工程设计集合"]
        elif receipt == "request_mismatch":
            payload["message"] = "所有工程系统设计题 具体业务系统设计"
        elif receipt == "missing_goal":
            before["pending_clarification"]["original_message"] = ""
        turn.state_after, turn.state_before, turn.request_payload = after, before, payload
    calls = len(ranker.calls)
    run = service.begin(QueryRequest(message="重新检索", request_id=str(uuid4()), requery_of_run_id=source["run_id"]))
    plan = service.complete_collection_plan(run) or QuerySpec(action="SEARCH", search_query="旧模型业务设计扩展词")
    service.execute(run, "search_questions" if plan.action == "SEARCH" else "list_questions", plan)
    result = service.finish(run, provider="legacy-workset-contract-double")
    if receipt == "selected":
        assert result["intent"] == "SEARCH" and len(ranker.calls) == calls + 1
        assert len(run.state[pages.STATE_KEY]["manifest"]["candidate_ids"]) == 65
        assert run.state["relevance_scope_recovery"]["kind"] == "legacy_user_option_receipt"
        assert run.state["relevance_scope"]["excluded_domains"] == []
        assert run.state["relevance_scope"]["independent_only"] is False
        assert result["facts"]["meta"]["pagination"]["offset"] == 0
    else:
        assert result["intent"] == "CLARIFY" and len(ranker.calls) == calls
        assert pages.STATE_KEY not in run.state and "relevance_scope_recovery" not in run.state
