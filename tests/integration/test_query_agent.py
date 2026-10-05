"""Global Top N, task separation, pagination and durable natural-language queries."""
import pytest
from types import SimpleNamespace
from fastapi.testclient import TestClient
from sqlalchemy import select

from interview_intelligence.api import create_app
from interview_intelligence.agent.query_contract import QuerySpec, QueryRequest
from interview_intelligence.analytics.listing import ListRequest, list_questions
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import (
    AgentConversation, AgentTurn, CanonicalQuestion, CorpusState, Interview,
    OccurrenceTaskAnnotation, QuestionOccurrence, ModelCall,
)
from test_analytics import seed_corpus


def ranked_corpus():
    db, _, _ = seed_corpus()
    expected = []
    with db.session() as session, session.begin():
        interview = session.scalar(select(Interview).where(Interview.company_normalized == "字节", Interview.round == "FIRST"))
        for row in session.scalars(select(QuestionOccurrence)):
            session.add(OccurrenceTaskAnnotation(occurrence_id=row.id, response_form="VERBAL",
                coding_focus="NONE", producer_version="fixture", classification_status="VERIFIED", evidence={}))
        order = 2
        for i in range(55):
            engineering = i >= 50
            frequency = i + 15 if engineering else 50 - i
            canonical = CanonicalQuestion(canonical_text=f"{'手写线程池' if engineering else '算法求解'} {i}",
                primary_topic_id="redis.performance", taxonomy_version="v1", question_type="ALGORITHM")
            session.add(canonical)
            session.flush()
            if not engineering:
                expected.append((canonical.id, frequency))
            for j in range(frequency):
                row = QuestionOccurrence(interview_id=interview.id, canonical_question_id=canonical.id,
                    raw_question=canonical.canonical_text, normalized_question=canonical.canonical_text,
                    question_order=order, source_spans=[{"quote": canonical.canonical_text}],
                    topic_id="redis.performance", taxonomy_version="v1", question_type="ALGORITHM", confidence=1)
                order += 1
                session.add(row)
                session.flush()
                session.add(OccurrenceTaskAnnotation(occurrence_id=row.id, response_form="CODE",
                    coding_focus="ENGINEERING" if engineering else "ALGORITHM", producer_version="fixture", classification_status="VERIFIED", evidence={}))
    return db, expected


def test_global_top_40_excludes_engineering_and_preserves_exact_frequency():
    db, expected = ranked_corpus()
    with db.session() as session:
        result = list_questions(session, ListRequest(coding_focus="ALGORITHM", top_n=40, page_size=40), signing_key="test")
    assert [(r["canonical_question_id"], r["occurrence_count"]) for r in result["data"]] == expected[:40]
    assert all(r["interview_count"] == 1 for r in result["data"])
    assert result["meta"]["pagination"] == {"total": 50, "result_total": 40, "returned": 40, "offset": 0,
        "page_size": 40, "top_n": 40, "next_cursor": None}
    assert result["meta"]["task_coverage"]["complete"]


def test_signed_pages_bind_filter_number_user_and_annotation_revision():
    db, expected = ranked_corpus()
    request = ListRequest(coding_focus="ALGORITHM", top_n=40, page_size=20)
    with db.session() as session:
        first = list_questions(session, request, signing_key="test")
        cursor = first["meta"]["pagination"]["next_cursor"]
        second = list_questions(session, request.model_copy(update={"cursor": cursor}), signing_key="test")
        assert [r["canonical_question_id"] for r in first["data"] + second["data"]] == [i for i, _ in expected[:40]]
        assert second["meta"]["pagination"]["next_cursor"] is None
        with pytest.raises(ValueError, match="INVALID_CURSOR_SCOPE"):
            list_questions(session, request.model_copy(update={"cursor": cursor, "top_n": 50}), signing_key="test")
        with pytest.raises(ValueError, match="INVALID_CURSOR_SCOPE"):
            list_questions(session, request.model_copy(update={"cursor": cursor}), signing_key="test", user_id="other")
        session.get(CorpusState, 1).task_annotation_revision += 1
        session.commit()
        with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
            list_questions(session, request.model_copy(update={"cursor": cursor}), signing_key="test")


def test_query_and_chat_share_host_state_and_replay_completed_turn_without_model():
    db, expected = ranked_corpus()
    class Planner:
        calls = 0
        async def plan(self, run, context):
            self.calls += 1
            if context["message"] == "下一页":
                assert len(context["session"]["current_page_ids"]) == 20
                return QuerySpec(action="NEXT")
            if context["message"] == "查看第一题来源":
                return QuerySpec(action="DETAILS", filters={"coding_focus": "ALGORITHM"},
                    question_ids=[context["session"]["current_page_ids"][0]])
            return QuerySpec(action="LIST", filters={"coding_focus": "ALGORITHM"}, top_n=40, page_size=20)
    planner = Planner()
    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:"), query_planner=planner))
    body = {"message": "前40个频率最高的算法题", "request_id": "first", "filters": {"coding_focus": "ALGORITHM"}}
    first = client.post("/api/questions/query", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["meta"]["pagination"]["result_total"] == 40
    assert client.post("/api/questions/query", json=body).json() == first.json()
    assert planner.calls == 1
    details = client.post("/api/agent/chat", json={"message": "查看第一题来源", "request_id": "details",
        "conversation_id": first.json()["meta"]["conversation_id"], "expected_version": 1})
    assert details.status_code == 200, details.text
    assert details.json()["data"]["facts"]["data"][0]["canonical_question_id"] == expected[0][0]
    chat = client.post("/api/questions/query", json={"message": "下一页", "request_id": "next",
        "filters": {"coding_focus": "ALGORITHM"},
        "conversation_id": first.json()["meta"]["conversation_id"], "expected_version": 2})
    assert chat.status_code == 200, chat.text
    assert [r["canonical_question_id"] for r in chat.json()["data"]] == [i for i, _ in expected[20:40]]
    assert chat.json()["meta"]["conversation_version"] == 3
    with db.session() as session:
        conversation = session.scalar(select(AgentConversation))
        assert conversation.version == 3
        assert len(list(session.scalars(select(AgentTurn)))) == 3
    # Structured pagination/filter changes do not invoke a decision model.
    direct = client.get("/api/questions/list", params={"coding_focus": "ALGORITHM", "top_n": 40, "page_size": 40})
    assert direct.status_code == 200 and len(direct.json()["data"]) == 40
    assert planner.calls == 3


def test_unknown_classification_and_insufficient_results_are_visible():
    db, _, _ = seed_corpus()
    with db.session() as session:
        result = list_questions(session, ListRequest(coding_focus="ALGORITHM", top_n=40, page_size=40), signing_key="test")
    assert result["data"] == []
    assert result["meta"]["task_coverage"]["unknown"] == 3
    assert result["meta"]["task_coverage"]["pending"] == 3
    assert not result["meta"]["task_coverage"]["processed"]
    assert len(result["warnings"]) == 2


def test_processed_but_uncertain_labels_are_not_reported_as_pending():
    db, _, _ = seed_corpus()
    with db.session() as session, session.begin():
        for row in session.scalars(select(QuestionOccurrence)):
            session.add(OccurrenceTaskAnnotation(occurrence_id=row.id, response_form="UNKNOWN",
                coding_focus="UNKNOWN", producer_version="fixture", classification_status="VERIFIED", evidence={}))
    with db.session() as session:
        result = list_questions(session, ListRequest(coding_focus="ALGORITHM"), signing_key="test")
    coverage = result["meta"]["task_coverage"]
    assert coverage["processed"] and not coverage["complete"]
    assert coverage["pending"] == 0 and coverage["uncertain"] == 3
    assert "分类不确定" in result["warnings"][0]
    assert "尚未完成" not in result["warnings"][0]


def test_structured_pages_update_agent_references_without_planning_and_replay_safely():
    db, _, _ = seed_corpus()
    class Planner:
        calls = 0
        async def plan(self, run, context):
            self.calls += 1
            return QuerySpec(action="DETAILS", filters=context["session"]["filters"],
                question_ids=[context["session"]["current_page_ids"][0]])
    planner = Planner()
    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:"), query_planner=planner))
    first_body = {"list_request": {"page_size": 1}, "request_id": "structured-first"}
    first = client.post("/api/questions/list", json=first_body)
    assert first.status_code == 200, first.text
    assert first.json()["meta"]["planning"]["provider"] == "structured_sql"
    assert first.json()["meta"]["timings"]["model_attempts"] == 0 and planner.calls == 0
    second_body = {"list_request": {"page_size": 1, "cursor": first.json()["meta"]["pagination"]["next_cursor"]},
        "conversation_id": first.json()["meta"]["conversation_id"], "expected_version": 1,
        "request_id": "structured-next"}
    second = client.post("/api/questions/list", json=second_body)
    assert second.status_code == 200, second.text
    second_id = second.json()["data"][0]["canonical_question_id"]
    assert second_id != first.json()["data"][0]["canonical_question_id"]
    assert client.post("/api/questions/list", json=second_body).json() == second.json()
    conflicting = {**second_body, "list_request": {**second_body["list_request"], "top_n": 1}}
    assert client.post("/api/questions/list", json=conflicting).status_code == 409
    stale = {**second_body, "request_id": "stale-version"}
    assert client.post("/api/questions/list", json=stale).status_code == 409
    detail = client.post("/api/agent/chat", json={"message": "查看第一题来源", "request_id": "current-detail",
        "conversation_id": first.json()["meta"]["conversation_id"], "expected_version": 2})
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["facts"]["data"][0]["canonical_question_id"] == second_id
    assert planner.calls == 1
    changed = client.post("/api/questions/list", json={"list_request": {"company": "腾讯", "page_size": 1},
        "conversation_id": first.json()["meta"]["conversation_id"], "expected_version": 3,
        "request_id": "changed-scope"})
    assert changed.status_code == 200, changed.text
    with db.session() as session:
        conversation = session.get(AgentConversation, first.json()["meta"]["conversation_id"])
        assert conversation.version == 4 and conversation.state["filters"]["company"] == "腾讯"
        assert conversation.state["current_page_ids"] == [changed.json()["data"][0]["canonical_question_id"]]
    assert planner.calls == 1


def test_search_model_call_uses_query_receipt_trace_and_contributes_phase_timings(monkeypatch, tmp_path):
    import interview_intelligence.api as api_module
    from interview_intelligence.dedup.provider import ArkMultimodalEncoder
    db, fast_id, _ = seed_corpus()
    with db.session() as session, session.begin():
        state = session.get(CorpusState, 1)
        state.current_revision = state.indexed_revision = 1
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"data": {"embedding": [1.0] + [0.0] * 1023}, "usage": {"prompt_tokens": 10}}
    def encoder(**kwargs):
        return ArkMultimodalEncoder(**kwargs, client=SimpleNamespace(post=lambda *a, **k: Response()))
    class Retriever:
        def __init__(self, url, encoder, **kwargs): self.encoder = encoder
        def retrieve(self, query, eligible, pipeline, top_k):
            self.encoder.embed_query(query)
            return {"data": [{"canonical_question_id": fast_id}], "meta": {}}
    class Planner:
        async def plan(self, run, context):
            return QuerySpec(action="SEARCH", search_query="Redis", page_size=10)
    monkeypatch.setattr(api_module, "ArkMultimodalEncoder", encoder)
    monkeypatch.setattr(api_module, "ElasticsearchRetriever", Retriever)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", elasticsearch_url="http://es.test",
        model_api_key="test", model_base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        model_lock_path=tmp_path / "gate", model_min_interval_seconds=0, max_model_calls=10, max_model_tokens=10000,
        internal_agent_token="test-internal-token")
    app = create_app(db, settings, query_planner=Planner())
    client = TestClient(app)
    result = client.post("/api/questions/query", json={"message": "Redis语义查询", "request_id": "same-query-trace"})
    assert result.status_code == 200, result.text
    assert result.json()["meta"]["timings"]["model_attempts"] == 1
    with db.session() as session:
        calls = list(session.scalars(select(ModelCall)))
        assert len(calls) == 1 and calls[0].request_id == "same-query-trace"
        assert calls[0].operation_type == "EMBEDDING"
        assert calls[0].provider_ms == result.json()["meta"]["timings"]["provider_ms"]
    # Pi callbacks arrive as separate HTTP requests and must restore the run trace.
    run = app.state.query_service.begin(QueryRequest(message="Redis", request_id="same-pi-tool-trace"))
    tool = client.post(f"/internal/agent/runs/{run.id}/tools/search_questions",
        headers={"Authorization":"Bearer test-internal-token"},
        json=QuerySpec(action="SEARCH", search_query="Redis", page_size=10).model_dump(mode="json"))
    assert tool.status_code == 200, tool.text
    with db.session() as session:
        call = session.scalar(select(ModelCall).where(ModelCall.request_id == "same-pi-tool-trace"))
        assert call is not None and call.operation_type == "EMBEDDING"
        assert run.call_timings["provider_ms"] == call.provider_ms
    app.state.query_service.finish(run, provider="pi-test")
