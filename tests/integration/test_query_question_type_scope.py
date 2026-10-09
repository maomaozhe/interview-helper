"""Semantic intent must not turn extraction taxonomy into an unsupported scope."""
from copy import deepcopy
import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.agent.query_service import QueryService
from interview_intelligence.domain.models import (
    AgentTurn, CanonicalQuestion, CorpusState, OccurrenceTaskAnnotation, QuestionOccurrence,
)
from test_analytics import seed_corpus
from test_conversation_quality import app_fixture


class CapturingRetriever:
    supports_relevance_query = True

    def __init__(self):
        self.calls = []

    def retrieve(self, query, eligible, pipeline, top_k, *, relevance_query=None):
        self.calls.append({"query": query, "eligible": eligible, "relevance_query": relevance_query})
        return {"data": [{"canonical_question_id": qid} for qid in eligible[:top_k]],
                "meta": {"pipeline": pipeline, "rerank_status": "COMPLETED"}}


def fixture():
    database, project_id, scenario_id = seed_corpus()
    with database.session() as session, session.begin():
        corpus = session.get(CorpusState, 1)
        corpus.current_revision = corpus.indexed_revision = 1
        for qid, kind, text in [(project_id, "PROJECT", "支付平台中如何设计订单状态流转与重试？"),
                                (scenario_id, "SCENARIO", "请求超时后如何定位线上故障？")]:
            question = session.get(CanonicalQuestion, qid)
            question.question_type, question.canonical_text = kind, text
            for occurrence in session.scalars(select(QuestionOccurrence).where(
                    QuestionOccurrence.canonical_question_id == qid)):
                occurrence.question_type = kind
                occurrence.raw_question = occurrence.normalized_question = text
    retriever = CapturingRetriever()
    return database, project_id, scenario_id, QueryService(database, signing_key="test", retriever=retriever)


def search(service, request, *, question_type=None, **filters):
    run = service.begin(request)
    service.execute(run, "search_questions", QuerySpec(action="SEARCH", search_query="支付 订单 状态机 重试",
        relevance_query="支付平台订单流转的业务场景设计", filters={"question_type": question_type, **filters}))
    service.finish(run, provider="fixture")
    return run


@pytest.mark.parametrize("inferred", ["SCENARIO", "SYSTEM_DESIGN", "AI", "PROJECT"])
def test_inferred_search_type_does_not_exclude_business_project_before_retrieval(inferred):
    _, project_id, scenario_id, service = fixture()
    run = search(service, QueryRequest(message="找一些支付订单流转的场景题", request_id="soft-type"), question_type=inferred)
    assert set(service.retriever.calls[0]["eligible"]) == {project_id, scenario_id}
    assert project_id in {row["canonical_question_id"] for row in run.result["facts"]["data"]}
    assert service.retriever.calls[0]["relevance_query"] == "支付平台订单流转的业务场景设计"
    assert run.result["facts"]["meta"]["question_type_filter_policy"] == {
        "version": "search_question_type_origin_v1", "planned_question_type": inferred,
        "applied_question_type": None, "source": "model_intent", "decision": "model_inferred_softened",
        "mode": "semantic_intent"}
    assert run.result["planning"]["spec"]["filters"]["question_type"] is None
    assert run.result["tool_trace"][0]["parameters"]["filters"]["question_type"] is None
    assert "question_type_scope" not in run.state


def test_explicit_ui_type_is_enforced_and_inherited_only_when_same_scope_is_used():
    _, project_id, scenario_id, service = fixture()
    original = search(service, QueryRequest(message="场景题", request_id="ui-type",
        filters={"question_type": "SCENARIO"}), question_type="SCENARIO")
    assert service.retriever.calls[-1]["eligible"] == [scenario_id]
    following = QueryRequest(message="这类题再找一些", request_id="same-type",
        conversation_id=original.conversation_id, expected_version=1)
    pending = service.begin(following)
    assert service.model_context(pending)["session"]["question_type_scope"]["source"] == "explicit_ui"
    service.execute(pending, "search_questions", QuerySpec(action="SEARCH", search_query="线上故障",
        filters={"question_type": "SCENARIO"}))
    service.finish(pending, provider="fixture")
    assert service.retriever.calls[-1]["eligible"] == [scenario_id]
    assert pending.result["facts"]["meta"]["question_type_filter_policy"]["source"] == "inherited_explicit_ui"
    switched = search(service, QueryRequest(message="换一个方向找支付平台设计", request_id="new-topic",
        conversation_id=original.conversation_id, expected_version=2), question_type="PROJECT")
    assert set(service.retriever.calls[-1]["eligible"]) == {project_id, scenario_id}
    assert "question_type_scope" not in switched.state


@pytest.mark.parametrize("planned", [None, "PROJECT"])
def test_planner_cannot_clear_or_change_current_explicit_ui_type(planned):
    _, _, _, service = fixture()
    run = service.begin(QueryRequest(message="场景题", request_id="ui-conflict", filters={"question_type": "SCENARIO"}))
    with pytest.raises(ValueError, match="EXPLICIT_FILTER_CONFLICT"):
        service.execute(run, "search_questions", QuerySpec(action="SEARCH", search_query="设计",
            filters={"question_type": planned}))
    assert service.retriever.calls == []
    service.fail(run)


def test_historical_search_preserves_inherited_type_and_current_null_clears_chained_scope():
    _, project_id, scenario_id, service = fixture()
    original = search(service, QueryRequest(message="只要旧分类中的场景题", request_id="history-ui",
        filters={"question_type": "SCENARIO"}), question_type="SCENARIO")
    following = search(service, QueryRequest(message="这类题", request_id="history-inherited",
        conversation_id=original.conversation_id), question_type="SCENARIO")
    with service.database.session() as session:
        assert session.get(AgentTurn, following.id).request_payload["filters"]["question_type"] is None
    refresh = service.begin(QueryRequest(message="这类题", request_id="history-refresh", requery_of_run_id=following.id))
    assert service.model_context(refresh)["explicit_filters"] == {"question_type": "SCENARIO"}
    with pytest.raises(ValueError, match="EXPLICIT_FILTER_CONFLICT"):
        service.execute(refresh, "search_questions", QuerySpec(action="SEARCH", search_query="设计"))
    service.fail(refresh)
    cleared = search(service, QueryRequest(message="清除题型限制重新找", request_id="history-clear",
        requery_of_run_id=following.id, filters={"question_type": None}))
    assert set(service.retriever.calls[-1]["eligible"]) == {project_id, scenario_id}
    chained = service.begin(QueryRequest(message="重新找", request_id="history-clear-chain", requery_of_run_id=cleared.id))
    assert service.model_context(chained)["explicit_filters"] == {"question_type": None}
    with pytest.raises(ValueError, match="EXPLICIT_FILTER_CONFLICT"):
        service.execute(chained, "search_questions", QuerySpec(action="SEARCH", search_query="设计",
            filters={"question_type": "SCENARIO"}))
    service.fail(chained)


def test_old_search_inference_does_not_become_historical_explicit_provenance():
    _, project_id, scenario_id, service = fixture()
    original = search(service, QueryRequest(message="业务场景题", request_id="legacy-search"))
    with service.database.session() as session, session.begin():
        receipt = session.get(AgentTurn, original.id)
        payload = dict(receipt.request_payload)
        payload.pop("resolved_explicit_filters"); payload.pop("query_filter_policy_version")
        receipt.request_payload = payload
        state = deepcopy(receipt.state_after)
        state["filters"]["question_type"] = "SCENARIO"
        state["last_plan"]["filters"]["question_type"] = "SCENARIO"
        receipt.state_after = state
        response = deepcopy(receipt.response)
        response["planning"]["spec"]["filters"]["question_type"] = "SCENARIO"
        receipt.response = response
    refreshed = search(service, QueryRequest(message="业务场景题", request_id="legacy-refresh",
        requery_of_run_id=original.id), question_type="SCENARIO")
    assert service.model_context(refreshed)["explicit_filters"] == {}
    assert set(service.retriever.calls[-1]["eligible"]) == {project_id, scenario_id}


@pytest.mark.parametrize("action,tool", [("LIST", "list_questions"), ("STATS", "get_question_stats")])
def test_sql_category_scope_and_paging_remain_exact_and_can_be_inherited_by_search(action, tool):
    database, project_id, scenario_id, service = fixture()
    # Two entries in one SQL category make paging meaningful.
    with database.session() as session, session.begin():
        session.get(CanonicalQuestion, project_id).question_type = "SCENARIO"
        for row in session.scalars(select(QuestionOccurrence)):
            row.question_type = "SCENARIO"
    listed = service.begin(QueryRequest(message="按分类列出场景题", request_id="typed-list"))
    service.execute(listed, tool, QuerySpec(action=action, filters={"question_type": "SCENARIO"}, page_size=1))
    service.finish(listed, provider="fixture")
    first_id = listed.result["facts"]["data"][0]["canonical_question_id"]
    paged = service.begin(QueryRequest(message="下一页", request_id="typed-next", conversation_id=listed.conversation_id))
    service.execute(paged, "list_questions", QuerySpec(action="NEXT"))
    service.finish(paged, provider="fixture")
    assert paged.result["facts"]["meta"]["applied_filters"]["question_type"] == "SCENARIO"
    assert paged.result["facts"]["data"][0]["canonical_question_id"] != first_id
    assert paged.state["question_type_scope"]["source"] == "sql_list"
    following = search(service, QueryRequest(message="在这类里找设计题", request_id="search-sql-scope",
        conversation_id=listed.conversation_id), question_type="SCENARIO")
    assert following.result["facts"]["meta"]["question_type_filter_policy"]["source"] == "inherited_sql_list"
    assert set(service.retriever.calls[-1]["eligible"]) == {project_id, scenario_id}


def test_ordinary_explicit_null_clears_scope_and_is_a_distinct_idempotent_request():
    _, project_id, scenario_id, service = fixture()
    original = search(service, QueryRequest(message="只看场景分类", request_id="null-original",
        filters={"question_type": "SCENARIO"}), question_type="SCENARIO")
    body = QueryRequest(message="不限分类继续找", request_id="null-clear", conversation_id=original.conversation_id,
        filters={"question_type": None})
    pending = service.begin(body)
    assert service.model_context(pending)["explicit_filters"] == {"question_type": None}
    with pytest.raises(ValueError, match="EXPLICIT_FILTER_CONFLICT"):
        service.execute(pending, "search_questions", QuerySpec(action="SEARCH", search_query="设计",
            filters={"question_type": "SCENARIO"}))
    service.fail(pending)
    cleared = search(service, body)
    assert set(service.retriever.calls[-1]["eligible"]) == {project_id, scenario_id}
    assert "question_type_scope" not in cleared.state
    assert service.begin(body) == cleared.result
    refreshed = service.begin(QueryRequest(message="不限分类继续找", request_id="normal-clear-refresh",
        requery_of_run_id=cleared.id))
    assert service.model_context(refreshed)["explicit_filters"] == {"question_type": None}
    service.fail(refreshed)
    omitted = QueryRequest(message=body.message, request_id=body.request_id, conversation_id=body.conversation_id)
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        service.begin(omitted)
    other = QueryRequest(message="不限分类", request_id="omit-first")
    search(service, other)
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        service.begin(other.model_copy(update={"filters": other.filters.model_validate({"question_type": None})}))


def test_old_completed_receipt_with_explicit_null_preserves_legacy_digest():
    _, _, _, service = fixture()
    body = QueryRequest(message="不限分类", request_id="legacy-null", filters={"question_type": None})
    original = search(service, body)
    with service.database.session() as session, session.begin():
        receipt = session.get(AgentTurn, original.id)
        receipt.payload_hash = hashlib.sha256(body.model_dump_json(exclude={"requery_of_run_id"}).encode()).hexdigest()
        payload = dict(receipt.request_payload); payload.pop("query_filter_policy_version")
        receipt.request_payload = payload
    assert service.begin(body) == original.result


@pytest.mark.parametrize("complete_null_body", [False, True])
@pytest.mark.parametrize("interrupt_before_finish", [False, True])
def test_same_json_body_replays_and_recovers_without_losing_null_presence(
        tmp_path, monkeypatch, complete_null_body, interrupt_before_finish):
    class Planner:
        calls = 0

        def plan(self, run, context):
            self.calls += 1
            return QuerySpec(action="SEARCH", search_query="场景设计", filters={"question_type":
                None if "question_type" in context["explicit_filters"] else "SCENARIO"})

    planner, retriever = Planner(), CapturingRetriever()
    initial_client, database, _, service = app_fixture(tmp_path, planner, retriever)
    client = TestClient(initial_client.app, raise_server_exceptions=False)
    request = QueryRequest(message="场景设计题", request_id="same-wire-body")
    body = request.model_dump(mode="json", exclude_unset=not complete_null_body)
    if interrupt_before_finish:
        finish, interrupted = service.finish, []

        def fail_once(*args, **kwargs):
            if not interrupted:
                interrupted.append(True)
                raise RuntimeError("simulated interruption after durable read")
            return finish(*args, **kwargs)

        monkeypatch.setattr(service, "finish", fail_once)
    first = client.post("/api/questions/query", json=body)
    if interrupt_before_finish:
        assert first.status_code == 500
        with database.session() as session:
            assert session.scalar(select(AgentTurn)).status == "FAILED"
        completed = client.post("/api/questions/query", json=body)
        assert completed.status_code == 200, completed.text
        assert completed.json()["meta"]["planning"]["provider"] == "recovered_read"
    else:
        completed = first
        assert completed.status_code == 200, completed.text
    assert client.post("/api/questions/query", json=body).json() == completed.json()
    assert planner.calls == len(retriever.calls) == 1
    with database.session() as session:
        turn = session.scalar(select(AgentTurn))
        assert turn.status == "SUCCEEDED"
        assert ("question_type" in turn.request_payload["resolved_explicit_filters"]) is complete_null_body


def test_old_sql_listing_without_new_scope_marker_is_still_a_trusted_exact_category():
    database, _, scenario_id, service = fixture()
    listed = service.begin(QueryRequest(message="按分类列题", request_id="legacy-sql-scope"))
    service.execute(listed, "list_questions", QuerySpec(action="LIST", filters={"question_type": "SCENARIO"}))
    service.finish(listed, provider="fixture")
    from interview_intelligence.domain.models import AgentConversation
    with database.session() as session, session.begin():
        conversation = session.get(AgentConversation, listed.conversation_id)
        state = deepcopy(conversation.state); state.pop("question_type_scope")
        conversation.state = state
    following = search(service, QueryRequest(message="这类题的线上场景", request_id="legacy-sql-followup",
        conversation_id=listed.conversation_id), question_type="SCENARIO")
    assert service.retriever.calls[-1]["eligible"] == [scenario_id]
    assert following.result["facts"]["meta"]["question_type_filter_policy"]["decision"] == "trusted_inherited"


@pytest.mark.parametrize("response_form", ["CODE", "SQL"])
def test_task_filters_remain_hard_while_only_inferred_legacy_type_is_soft(response_form):
    database, project_id, _, service = fixture()
    with database.session() as session, session.begin():
        for occurrence in session.scalars(select(QuestionOccurrence).where(
                QuestionOccurrence.canonical_question_id == project_id)):
            session.add(OccurrenceTaskAnnotation(occurrence_id=occurrence.id, response_form=response_form,
                coding_focus="ENGINEERING", classification_status="VERIFIED", producer_version="fixture"))
    run = search(service, QueryRequest(message="工程实现练习", request_id="task-filter"), question_type="SCENARIO",
                 response_form=response_form, coding_focus="ENGINEERING")
    assert service.retriever.calls[-1]["eligible"] == [project_id]
    applied = run.result["facts"]["meta"]["applied_filters"]
    assert applied["question_type"] is None
    assert applied["response_form"] == response_form and applied["coding_focus"] == "ENGINEERING"
    assert applied["annotation_status"] == "VERIFIED"
