"""Real query/reranker boundaries with deterministic ES and provider transports."""
from collections import defaultdict
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.domain.models import (
    AgentConversation, AgentTurn, CanonicalQuestion, DocumentBuild, Interview,
    QuestionOccurrence, SourceRevision, ToolInvocation,
)
from interview_intelligence.search.elasticsearch import ElasticsearchRetriever
from interview_intelligence.search.reranker import DesignScopeResult, LLMReranker, RerankResult
from test_conversation_quality import app_fixture


TEXTS = {
    "valid": "设计一个消息推送服务，支持多机连接路由与断线恢复。",
    "parking": "请设计一个停车场管理系统：支持车辆入场、出场、车位查询与计费。请给出核心数据模型、主要接口和异常处理方案。",
    "cache": "如何设计一个高吞吐、低延时、可扩展的高速Cache？",
}


class EvidenceProvider:
    def __init__(self):
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        body = json.loads(kwargs["messages"][-1]["content"])
        contract = kwargs["response_format"]["json_schema"]
        assert contract["strict"] is True
        if "rankings" in contract["schema"]["properties"]:
            assert "enum" in contract["schema"]["$defs"]["CandidateRank"]["properties"]["task_evidence"]
            rankings = []
            for candidate in body["candidates"]:
                assert all(source["source_context_status"] == "VERIFIED" for source in candidate["source_context"])
                text = "".join(candidate["question"].values())
                row = {"candidate_id": candidate["id"], "candidate_task": "DESIGN_TASK",
                       "task_evidence": "q0", "object_evidence": "q0", "focus_evidence": "q0",
                       "object_scope": "SAME", "focus_scope": "SAME"}
                # Providers can violate their requested enum contract. Preserve
                # historical malformed literals to exercise host rejection;
                # do not map either corruption to a legal selection ID.
                if text == TEXTS["parking"]:
                    row["task_evidence"] = "请设计一个停车场管理系统：...请给出核心数据模型、主要接口和异常处理方案。"
                elif text == TEXTS["cache"]:
                    row["focus_evidence"] = "高吞吐，低延时，可扩展"
                rankings.append(row)
            result = {"query_task": "INDEPENDENT_SYSTEM_DESIGN", "query_task_evidence": body["query"],
                      "query_object": body["query"], "query_focus": body["query"],
                      "query_object_evidence": body["query"], "query_focus_evidence": body["query"],
                      "rankings": rankings}
            RerankResult.model_validate(result)  # Full current shape and strict field types.
        else:
            result = {"verifications": [{"candidate_id": candidate["id"], "keep": True,
                "reason": "当前题明确要求新建独立消息推送服务", "design_unit": "NEW_OR_REBUILT_SYSTEM",
                "construction_evidence": "q0", "constraint_a": "", "constraint_b": ""}
                for candidate in body["candidates"]]}
            assert ["".join(candidate["question"].values()) for candidate in body["candidates"]] == [TEXTS["valid"]]
            DesignScopeResult.model_validate(result)
        return SimpleNamespace(model="fixture", usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(result)))])


class EvidenceES:
    def __init__(self):
        self.texts, self.calls = {}, []

    @staticmethod
    def response(value):
        return httpx.Response(200, json=value, request=httpx.Request("POST", "http://fixture.test"))

    def post(self, path, **kwargs):
        self.calls.append(path)
        if path.endswith("/_pit"):
            return self.response({"id": "evidence-pit"})
        assert path == "/_search"
        body = kwargs["json"]
        allowed = (body["knn"]["filter"] if "knn" in body else body["query"]["bool"]["filter"][0])["terms"]["_id"]
        assert set(self.texts) <= set(allowed)
        return self.response({"hits": {"hits": [{"_id": identity, "_score": 1.0,
            "_source": {"canonical_text": text}} for identity, text in self.texts.items()]}})

    def delete(self, path, **kwargs):
        self.calls.append(path)
        assert path == "/_pit"
        return self.response({"succeeded": True})


def evidence_app(tmp_path, monkeypatch, include_valid):
    class Planner:
        calls = 0

        def plan(self, run, context):
            self.calls += 1
            return QuerySpec(action="SEARCH", search_query="独立系统设计", relevance_query="独立系统设计")

    provider, es, planner = EvidenceProvider(), EvidenceES(), Planner()
    encoder = SimpleNamespace(version="fixture:2", embed=lambda query: [0.5, 0.5])
    retriever = ElasticsearchRetriever("http://unused", encoder, client=es,
        reranker=LLMReranker(client=provider, model="fixture"))
    client, db, valid_id, _ = app_fixture(tmp_path, planner, retriever)
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    monkeypatch.setattr("interview_intelligence.config.load_settings", lambda: SimpleNamespace(snapshot_root=snapshots))
    with db.session() as session, session.begin():
        questions = list(session.scalars(select(CanonicalQuestion)))
        parking = next(question for question in questions if question.id != valid_id)
        cache = CanonicalQuestion(canonical_text=TEXTS["cache"], primary_topic_id="system_design.cache",
                                  taxonomy_version="v1", question_type="SYSTEM_DESIGN")
        session.add(cache)
        session.flush()
        original = session.scalar(select(QuestionOccurrence).where(QuestionOccurrence.canonical_question_id == parking.id))
        session.add(QuestionOccurrence(interview_id=original.interview_id, canonical_question_id=cache.id,
            raw_question=TEXTS["cache"], normalized_question=TEXTS["cache"], question_order=2,
            source_spans=[], topic_id=cache.primary_topic_id, taxonomy_version="v1", question_type="SYSTEM_DESIGN", confidence=1))
        session.get(CanonicalQuestion, valid_id).canonical_text = TEXTS["valid"]
        parking.canonical_text = TEXTS["parking"]
        grouped = defaultdict(list)
        for occurrence in session.scalars(select(QuestionOccurrence)):
            occurrence.raw_question = occurrence.normalized_question = session.get(CanonicalQuestion, occurrence.canonical_question_id).canonical_text
            interview = session.get(Interview, occurrence.interview_id)
            revision_id = session.get(DocumentBuild, interview.build_id).source_revision_id
            grouped[revision_id].append(occurrence)
        for revision_id, occurrences in grouped.items():
            text = "# 面经\n" + "\n".join(row.raw_question for row in occurrences) + "\n"
            digest = hashlib.sha256(text.encode()).hexdigest()
            (snapshots / f"{digest}.md").write_text(text, encoding="utf-8", newline="")
            session.get(SourceRevision, revision_id).raw_file_hash = digest
            for occurrence in occurrences:
                start = text.index(occurrence.raw_question)
                occurrence.source_spans = [{"start_char": start, "end_char": start + len(occurrence.raw_question),
                    "start_line": text[:start].count("\n") + 1,
                    "end_line": text[:start + len(occurrence.raw_question)].count("\n") + 1,
                    "quote": occurrence.raw_question}]
        es.texts = {parking.id: TEXTS["parking"], cache.id: TEXTS["cache"]}
        if include_valid:
            es.texts[valid_id] = TEXTS["valid"]
    return client, db, valid_id, provider, es, planner


@pytest.mark.parametrize("include_valid", [True, False], ids=["partial-evidence", "all-invalid-evidence"])
def test_real_evidence_failures_cross_retriever_api_and_durable_replay(tmp_path, monkeypatch, include_valid):
    client, db, valid_id, provider, es, planner = evidence_app(tmp_path, monkeypatch, include_valid)
    # This fixture isolates legacy task/citation failures. Clarified business
    # scope has its separate strict proof transport tests.
    body = {"message": "独立系统设计", "request_id": "real-evidence-chain"}
    headers = {"x-request-id": "evidence-http-request"}
    response = client.post("/api/query", json=body, headers=headers)
    assert response.status_code == (200 if include_valid else 503), response.text
    with db.session() as session:
        turn = session.scalar(select(AgentTurn).where(AgentTurn.request_id == body["request_id"]))
        invocations = list(session.scalars(select(ToolInvocation).where(ToolInvocation.run_id == turn.id)))
        assert len(invocations) == 1 and invocations[0].status == "COMPLETED"
        result, tool_result = turn.response, invocations[0].result
        assert result["facts"]["data"] == tool_result["facts"]["data"]
        # finish adds whole-run timings after the completed tool checkpoint.
        tool_meta, final_meta = tool_result["facts"]["meta"], result["facts"]["meta"]
        assert {key: value for key, value in final_meta.items() if key != "timings"} == {
            key: value for key, value in tool_meta.items() if key != "timings"}
        assert tool_meta["timings"].items() <= final_meta["timings"].items()
        assert result["answer"] == tool_result["answer"] and result["warnings"] == tool_result["warnings"]
        meta = result["facts"]["meta"]
        assert meta["rerank_status"] == "COMPLETED_PARTIAL"
        assert meta["candidate_verification_status"] == "PARTIAL"
        assert meta["result_completeness"]["invalid_evidence_count"] == meta["invalid_candidate_count"] == 2
        assert meta["result_completeness"]["invalid_schema_count"] == 0
        assert meta["result_completeness"]["corpus_complete"] is False
        decisions = {row["decision"] for row in meta["rerank_audit"]["candidates"]}
        assert {"INVALID_TASK_EVIDENCE", "INVALID_EVIDENCE"} <= decisions
        assert any("2 个证据异常候选" in warning and "可能不完整" in warning for warning in result["warnings"])
        assert turn.status == ("SUCCEEDED" if include_valid else "FAILED")
        assert session.get(AgentConversation, turn.conversation_id).version == int(include_valid)
        if include_valid:
            assert [row["canonical_question_id"] for row in result["facts"]["data"]] == [valid_id]
            assert "不代表题库全部匹配题" in result["answer"] and "证据或格式异常" in result["answer"]
            assert meta["relevance_status"] == "VERIFIED"
            assert response.json()["meta"]["result_completeness"] == meta["result_completeness"]
            assert response.json()["data"] == result["facts"]["data"]
            assert response.json()["meta"]["answer"] == result["answer"]
            assert response.json()["warnings"] == result["warnings"]
        else:
            assert result["facts"]["data"] == [] and meta["relevance_status"] == "UNAVAILABLE"
            assert turn.error_code == response.json()["error"]["code"] == "QUERY_RELEVANCE_UNAVAILABLE"
        run_id = turn.id
    status = client.get(f"/api/runs/{run_id}").json()["data"]
    assert status["events"][-1]["type"] == ("completed" if include_valid else "failed")
    assert any(event["type"] == "completed" for event in status["events"]) == include_valid
    persisted = status["result"] if include_valid else status["partial_result"]
    assert persisted["facts"]["meta"]["result_completeness"] == meta["result_completeness"]
    if not include_valid:
        assert status["result"] is None
    before = len(provider.requests), len(es.calls), planner.calls
    # One bounded source-ID repair is attempted before unresolved evidence is
    # persisted; idempotent replay below must not perform any further call.
    assert before[0] == (3 if include_valid else 2)
    assert client.post("/api/query", json=body, headers=headers).json() == response.json()
    assert (len(provider.requests), len(es.calls), planner.calls) == before
