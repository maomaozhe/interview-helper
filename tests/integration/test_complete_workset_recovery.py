"""Failure receipts must not overwrite goals or silently repeat uncertain work."""
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from interview_intelligence.agent import complete_workset as pages
from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.api_errors import register_error_handlers
from interview_intelligence.domain.models import AgentConversation, AgentTurn
from test_complete_business_workset import setup, send
from test_clarified_business_scope import ScopedRetriever


def accepted(service, *, mode="全量分页"):
    _, broad = send(service, "列出所有工程系统设计题", page_size=5)
    _, refined = send(service, "所有工程系统设计题 具体业务系统设计", broad)
    request = QueryRequest(message=mode, request_id=str(uuid4()),
        conversation_id=refined["conversation_id"], expected_version=refined["conversation_version"])
    return request, service.begin(request), refined


def interrupted_after_processing(service, run, monkeypatch):
    import interview_intelligence.search.service as backend
    original = backend.process_engineering_workset_batch
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("worker lost outcome before durable apply")
    monkeypatch.setattr(backend, "process_engineering_workset_batch", interrupted)
    with pytest.raises(RuntimeError, match="worker lost outcome"):
        service.execute(run, "search_questions", service.complete_collection_plan(run))
    service.fail(run, RuntimeError("worker lost outcome before durable apply"))
    monkeypatch.setattr(backend, "process_engineering_workset_batch", original)


def test_old_failed_checkpoint_cannot_replace_a_new_conversation_goal(tmp_path, monkeypatch):
    db, service, retriever, _, ranker = setup(tmp_path, monkeypatch)
    request, run, previous = accepted(service)
    interrupted_after_processing(service, run, monkeypatch)
    service.retriever = ScopedRetriever()
    _, newer = send(service, "换个问题，只查Redis原理题", previous,
        plan=QuerySpec(action="SEARCH", search_query="Redis原理题"))
    service.retriever = retriever
    with pytest.raises(ValueError, match="CONVERSATION_VERSION_CONFLICT"):
        service.begin(request)
    with db.session() as session:
        conversation = session.get(AgentConversation, newer["conversation_id"])
        assert conversation.version == newer["conversation_version"] and pages.STATE_KEY not in conversation.state
    assert len(ranker.calls) == 1


def test_returned_batch_survives_checkpoint_failure_in_failure_transaction(tmp_path, monkeypatch):
    _, service, _, _, ranker = setup(tmp_path, monkeypatch)
    request, run, _ = accepted(service)
    checkpoint = service._checkpoint_workset
    def unavailable(current):
        if current.state[pages.STATE_KEY]["frontier"]:
            raise RuntimeError("batch checkpoint unavailable after result")
        return checkpoint(current)
    monkeypatch.setattr(service, "_checkpoint_workset", unavailable)
    with pytest.raises(RuntimeError):
        service.execute(run, "search_questions", service.complete_collection_plan(run))
    assert len(ranker.calls) == 1
    service.fail(run, RuntimeError("batch checkpoint unavailable after result"))
    monkeypatch.setattr(service, "_checkpoint_workset", checkpoint)
    resumed = service.begin(request)
    service.execute(resumed, "search_questions", service.complete_collection_plan(resumed))
    result = service.finish(resumed, provider="offline-recovery")
    assert len(ranker.calls) == 1 and result["facts"]["meta"]["candidate_workset_progress"]["processed"] == 20


def test_returned_partial_prefix_survives_checkpoint_failure_without_replaying_the_offered_tail(tmp_path, monkeypatch):
    _, service, retriever, _, ranker = setup(tmp_path, monkeypatch)
    original = retriever.hydrate_and_rerank_ids
    def partial(query, identities, **kwargs):
        selected = identities[:7]
        options = {**kwargs, "target_catalog": {**kwargs["target_catalog"],
            "titles": {identity: kwargs["target_catalog"]["titles"][identity] for identity in selected}}}
        result = original(query, selected, **options)
        result["processed_candidate_ids"] = identities[:7]
        result["meta"]["workset_batch_audit"].update(offered_candidate_ids=list(identities),
            processed_candidate_ids=list(selected), unprocessed_candidate_ids=identities[len(selected):])
        return result
    monkeypatch.setattr(retriever, "hydrate_and_rerank_ids", partial)
    request, run, _ = accepted(service)
    checkpoint = service._checkpoint_workset
    def unavailable(current):
        if current.state[pages.STATE_KEY]["frontier"]:
            raise RuntimeError("partial checkpoint unavailable after result")
        return checkpoint(current)
    monkeypatch.setattr(service, "_checkpoint_workset", unavailable)
    with pytest.raises(RuntimeError, match="partial checkpoint"):
        service.execute(run, "search_questions", service.complete_collection_plan(run))
    service.fail(run, RuntimeError("partial checkpoint unavailable after result"))
    monkeypatch.setattr(service, "_checkpoint_workset", checkpoint)
    resumed = service.begin(request)
    service.execute(resumed, "search_questions", service.complete_collection_plan(resumed))
    result = service.finish(resumed, provider="offline-partial-recovery")
    snapshot = resumed.state[pages.STATE_KEY]
    assert len(ranker.calls) == 1 and snapshot["frontier"] == 7
    assert len(snapshot["batch_receipts"][0]["offered_candidate_ids"]) == 20
    assert len(snapshot["batch_receipts"][0]["candidate_ids"]) == 7
    assert all(snapshot["statuses"][identity] == "UNPROCESSED"
        for identity in snapshot["manifest"]["candidate_ids"][7:])
    assert result["facts"]["meta"]["candidate_workset_progress"]["processed"] == 7


def test_unknown_attempt_is_fail_closed_for_same_request_but_explicit_new_requery_is_allowed(tmp_path, monkeypatch):
    db, service, retriever, _, ranker = setup(tmp_path, monkeypatch)
    request, run, _ = accepted(service)
    interrupted_after_processing(service, run, monkeypatch)
    with db.session() as session:
        saved = deepcopy(session.get(AgentTurn, run.id).state_after[pages.STATE_KEY])
    assert saved["frontier"] == 0 and len(saved["manifest"]["candidate_ids"]) == 65
    assert saved["batch_attempt"]["run_id"] == run.id and len(saved["batch_attempt"]["candidate_ids"]) == 20
    fresh = QueryService(db, retriever, signing_key="complete-pages", max_tokens=96000,
        deadline_seconds=180, feedback_root=tmp_path / "fresh-feedback")
    resumed = fresh.begin(request)
    with pytest.raises(ValueError, match="WORKSET_BATCH_OUTCOME_UNAVAILABLE"):
        fresh.execute(resumed, "search_questions", fresh.complete_collection_plan(resumed))
    fresh.fail(resumed, ValueError("WORKSET_BATCH_OUTCOME_UNAVAILABLE"))
    assert len(ranker.calls) == 1
    restarted, result = send(fresh, "重新检索", requery_of_run_id=run.id)
    assert result["intent"] == "SEARCH" and len(ranker.calls) == 2
    assert restarted.state["complete_workset_mode"] == "ALL"
    assert restarted.state[pages.STATE_KEY]["frontier"] == 20
    assert restarted.state[pages.STATE_KEY]["source_run_id"] != run.id
    assert result["facts"]["meta"]["pagination"]["offset"] == 0


@pytest.mark.parametrize("fault", ["missing_base_version", "tampered_signature", "tampered_auth", "another_actor", "current_conversation_version"])
def test_failed_workset_restore_retains_receipt_and_requery_authority_boundaries(tmp_path, monkeypatch, fault):
    db, service, _, _, ranker = setup(tmp_path, monkeypatch)
    request, run, previous = accepted(service)
    interrupted_after_processing(service, run, monkeypatch)
    if fault in {"missing_base_version", "tampered_signature", "tampered_auth"}:
        with db.session() as session, session.begin():
            turn = session.get(AgentTurn, run.id)
            if fault == "missing_base_version":
                payload = deepcopy(turn.request_payload)
                payload.pop("host_conversation_base_version")
                turn.request_payload = payload
            else:
                state = deepcopy(turn.state_after)
                if fault == "tampered_signature": state[pages.STATE_KEY]["signature"] = "forged"
                else: state["engineering_collection_authorization"]["source_messages"] = ["所有具体业务系统设计题"]
                turn.state_after = state
    if fault == "missing_base_version":
        with pytest.raises(ValueError, match="CONVERSATION_VERSION_CONFLICT"):
            service.begin(request)
    elif fault == "another_actor":
        other = QueryService(db, service.retriever, signing_key="complete-pages", user_id="another-user")
        with pytest.raises(KeyError, match="RUN_NOT_FOUND"):
            other.begin(QueryRequest(message="重新检索", request_id=str(uuid4()), requery_of_run_id=run.id))
    else:
        options = {"requery_of_run_id": run.id}
        if fault == "current_conversation_version":
            options.update(conversation_id=previous["conversation_id"], expected_version=previous["conversation_version"] + 1)
        with pytest.raises(ValueError):
            service.begin(QueryRequest(message="重新检索", request_id=str(uuid4()), **options))
    assert len(ranker.calls) == 1


def test_unknown_batch_error_is_a_nonretryable_conflict_with_actionable_message():
    app = FastAPI()
    register_error_handlers(app, lambda request: "offline-error")
    @app.get("/probe")
    def probe():
        raise ValueError("WORKSET_BATCH_OUTCOME_UNAVAILABLE")
    response = TestClient(app).get("/probe")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "WORKSET_BATCH_OUTCOME_UNAVAILABLE" and error["retryable"] is False
    assert "原查询范围已保留" in error["message"] and "新请求" in error["message"]


def test_new_failed_turn_base_version_is_not_limited_to_workset_queries(tmp_path, monkeypatch):
    db, service, _, _, _ = setup(tmp_path, monkeypatch)
    service.retriever = ScopedRetriever()
    request = QueryRequest(message="查询Redis原理题", request_id=str(uuid4()))
    failed = service.begin(request)
    service.fail(failed, RuntimeError("failed before completion"))
    _, newer = send(service, "改查Python原理题", conversation_id=failed.conversation_id,
        expected_version=failed.version, plan=QuerySpec(action="SEARCH", search_query="Python原理题"))
    with pytest.raises(ValueError, match="CONVERSATION_VERSION_CONFLICT"):
        service.begin(request)
    with db.session() as session:
        assert session.get(AgentConversation, newer["conversation_id"]).version == newer["conversation_version"]
