"""Partial candidate schema failures cannot masquerade as a verified empty set."""
import pytest
from sqlalchemy import select

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.domain.models import AgentConversation, AgentTurn, ToolInvocation
from test_conversation_quality import app_fixture


@pytest.mark.parametrize("has_results,invalid_count,status,verification,executed,expected", [
    (True, 2, "COMPLETED_PARTIAL", "PARTIAL", "HYBRID_RERANK", "VERIFIED"),
    (False, 2, "COMPLETED_PARTIAL", "PARTIAL", "HYBRID_RERANK", "UNAVAILABLE"),
    (False, 0, "COMPLETED", "COMPLETE", "HYBRID_RERANK", "VERIFIED"),
    (True, 2, "FAILED", "PARTIAL", "HYBRID", "UNAVAILABLE"),
    (True, 0, "COMPLETED_PARTIAL", "PARTIAL", "HYBRID_RERANK", "UNAVAILABLE"),
    (True, 2, "COMPLETED", "PARTIAL", "HYBRID_RERANK", "UNAVAILABLE"),
])
def test_partial_schema_delivery_and_empty_verification(tmp_path, has_results, invalid_count, status,
                                                        verification, executed, expected):
    class Retriever:
        calls = 0

        def retrieve(self, query, eligible, pipeline, top_k):
            self.calls += 1
            return {"data": [{"canonical_question_id": eligible[0]}] if has_results else [],
                    "meta": {"pipeline": executed, "rerank_status": status,
                             "candidate_verification_status": verification, "invalid_candidate_count": invalid_count}}

    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="SEARCH", search_query="系统设计")

    retriever = Retriever()
    client, db, _, _ = app_fixture(tmp_path, Planner(), retriever)
    body = {"message": "找系统设计相关题", "request_id": "partial-schema-result"}
    response = client.post("/api/query", json=body)
    if expected == "UNAVAILABLE":
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "QUERY_RELEVANCE_UNAVAILABLE"
        assert response.json()["error"]["retryable"] is True
        with db.session() as session:
            receipt = session.scalar(select(AgentTurn).where(AgentTurn.request_id == body["request_id"]))
            assert receipt.status == "FAILED"
            assert receipt.error_code == "QUERY_RELEVANCE_UNAVAILABLE"
            facts = receipt.response["facts"]
            result = {**facts, "meta": {**facts["meta"], "answer": receipt.response["answer"]},
                      "warnings": receipt.response["warnings"]}
    else:
        assert response.status_code == 200, response.text
        result = response.json()
    assert result["meta"]["relevance_status"] == expected
    assert len(result["data"]) == int(has_results and expected == "VERIFIED")
    assert result["meta"]["invalid_candidate_count"] == invalid_count
    assert result["meta"]["candidate_verification_status"] == verification
    warnings = result["warnings"]
    if status == "COMPLETED_PARTIAL" and invalid_count:
        assert any("2 个结构异常候选" in warning and "可能不完整" in warning for warning in warnings)
    if expected == "UNAVAILABLE":
        assert "核验未完成" in result["meta"]["answer"]
    elif not has_results:
        assert not warnings
    repeated = client.post("/api/query", json=body)
    if expected == "UNAVAILABLE":
        assert repeated.status_code == 503
        assert repeated.json()["error"]["code"] == "QUERY_RELEVANCE_UNAVAILABLE"
    else:
        assert repeated.json() == result
    assert retriever.calls == 1


def test_unavailable_verification_persists_failure_and_partial_audit_without_advancing_conversation(tmp_path):
    audit = {"candidates": [{"canonical_question_id": "diagnostic-candidate",
                             "decision": "VERIFICATION_UNAVAILABLE"}]}

    class Retriever:
        def retrieve(self, query, eligible, pipeline, top_k):
            return {"data": [{"canonical_question_id": eligible[0]}],
                    "meta": {"pipeline": pipeline, "rerank_status": "SKIPPED_BUDGET",
                             "candidate_verification_status": "UNAVAILABLE", "rerank_audit": audit}}

    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="SEARCH", search_query="系统设计")

    client, db, _, service = app_fixture(tmp_path, Planner(), Retriever())
    response = client.post("/api/query", json={"message": "找系统设计相关题",
                                               "request_id": "unavailable-persisted"})
    assert response.status_code == 503, response.text
    with db.session() as session:
        turn = session.scalar(select(AgentTurn).where(AgentTurn.request_id == "unavailable-persisted"))
        assert turn.status == "FAILED"
        assert turn.error_code == "QUERY_RELEVANCE_UNAVAILABLE"
        assert turn.finished_at is not None
        assert turn.response["facts"]["data"] == []
        assert turn.response["facts"]["meta"]["relevance_status"] == "UNAVAILABLE"
        assert turn.response["facts"]["meta"]["rerank_audit"] == audit
        assert session.get(AgentConversation, turn.conversation_id).version == 0
        invocation = session.scalar(select(ToolInvocation).where(ToolInvocation.run_id == turn.id))
        assert invocation.status == "COMPLETED"
        assert invocation.result["facts"]["meta"]["rerank_audit"] == audit
        run_id = turn.id
    status = client.get(f"/api/runs/{run_id}").json()["data"]
    assert status["status"] == "FAILED"
    assert status["error_code"] == "QUERY_RELEVANCE_UNAVAILABLE"
    assert status["result"] is None
    assert status["partial_result"]["facts"]["meta"]["rerank_audit"] == audit
    assert status["events"][-1]["type"] == "failed"
    assert status["events"][-1]["data"]["partial"] is True
    assert not any(event["type"] == "completed" for event in status["events"])
    assert run_id not in service.runs


def test_finish_rejects_unavailable_result_before_creating_success_receipt(tmp_path):
    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="SEARCH", search_query="系统设计")

    class Retriever:
        def retrieve(self, query, eligible, pipeline, top_k):
            return {"data": [], "meta": {"pipeline": pipeline, "rerank_status": "FAILED"}}

    _, db, _, service = app_fixture(tmp_path, Planner(), Retriever())
    run = service.begin(QueryRequest(message="找系统设计相关题", request_id="finish-unavailable"))
    result = service.execute(run, "search_questions", QuerySpec(action="SEARCH", search_query="系统设计"))
    assert result["facts"]["meta"]["relevance_status"] == "UNAVAILABLE"
    with pytest.raises(ValueError, match="QUERY_RELEVANCE_UNAVAILABLE") as failure:
        service.finish(run, provider="fixture")
    with db.session() as session:
        turn = session.get(AgentTurn, run.id)
        assert turn.status == "RUNNING"
        assert turn.response["facts"]["meta"]["relevance_status"] == "UNAVAILABLE"
        assert session.get(AgentConversation, run.conversation_id).version == 0
    service.fail(run, failure.value)
    with db.session() as session:
        assert session.get(AgentTurn, run.id).status == "FAILED"


@pytest.mark.parametrize("has_results", [True, False])
def test_evidence_failures_are_visible_and_persisted_without_claiming_full_coverage(tmp_path, has_results):
    class Retriever:
        calls = 0
        supports_relevance_scope = True

        def retrieve(self, query, eligible, pipeline, top_k, *, relevance_scope):
            self.calls += 1
            assert relevance_scope["target_unit"] == "BUSINESS_APPLICATION"
            assert relevance_scope["excluded_domains"] == []
            return {"data": [{"canonical_question_id": eligible[0]}] if has_results else [],
                    "meta": {"pipeline": pipeline, "rerank_status": "COMPLETED_PARTIAL",
                             "relevance_scope_hash": relevance_scope["scope_hash"],
                             "relevance_scope_applied": True,
                             "candidate_count": 50, "candidate_verification_status": "PARTIAL",
                             "invalid_candidate_count": 2, "rerank_audit": {
                                 "invalid_evidence_count": 2, "invalid_schema_count": 0,
                                 "candidates": [{"canonical_question_id": "parking", "decision": "INVALID_TASK_EVIDENCE"},
                                                {"canonical_question_id": "cache", "decision": "INVALID_EVIDENCE"}]}}}

    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="SEARCH", search_query="业务系统设计")

    retriever = Retriever()
    client, db, _, _ = app_fixture(tmp_path, Planner(), retriever)
    body = {"message": "业务系统设计", "request_id": "partial-evidence-result"}
    response = client.post("/api/query", json=body)
    assert response.status_code == (200 if has_results else 503), response.text
    with db.session() as session:
        turn = session.scalar(select(AgentTurn).where(AgentTurn.request_id == body["request_id"]))
        assert turn.status == ("SUCCEEDED" if has_results else "FAILED")
        result = turn.response
        completeness = result["facts"]["meta"]["result_completeness"]
        assert completeness["scope"] == "retrieval_candidates"
        assert completeness["corpus_complete"] is False
        assert completeness["verification_complete"] is False
        assert completeness["candidate_count"] == 50
        assert completeness["invalid_evidence_count"] == completeness["invalid_candidate_count"] == 2
        assert completeness["invalid_schema_count"] == 0
        assert any("2 个证据异常候选" in warning and "可能不完整" in warning for warning in result["warnings"])
        assert not any("结构异常" in warning for warning in result["warnings"])
        if has_results:
            assert result["facts"]["meta"]["pagination"]["kind"] == "semantic_candidates"
            assert "范围待核验" in result["answer"]
            assert "不代表全库全部匹配题" in result["answer"]
        else:
            assert result["facts"]["data"] == []
            assert turn.error_code == "QUERY_RELEVANCE_UNAVAILABLE"
    repeated = client.post("/api/query", json=body)
    assert repeated.status_code == response.status_code
    assert retriever.calls == 1


@pytest.mark.parametrize("has_results", [True, False])
@pytest.mark.parametrize("repair_mode", ["short-time", "child-timeout", "parent-expiry"])
def test_optional_repair_time_policy_reaches_durable_partial_receipt(tmp_path, monkeypatch, has_results, repair_mode):
    """Exercise the real repair controller through QueryService, with no provider."""
    import time
    from types import SimpleNamespace
    from interview_intelligence.providers import runtime
    from interview_intelligence.search import reranker as ranker_module
    from interview_intelligence.search.reranker import LLMReranker, RerankedCandidates

    clock = SimpleNamespace(now=time.monotonic())
    clock.monotonic = lambda: clock.now
    monkeypatch.setattr(runtime, "time", clock)
    monkeypatch.setattr(ranker_module, "time", clock)

    class ControlledRepair(LLMReranker):
        attempts = 0

        def _verify_scope(self, query, candidates, *, operation="RERANK_SCOPE_VERIFY", relevance_scope=None):
            self.attempts += 1
            assert relevance_scope is None
            assert operation == "RERANK_EVIDENCE_REPAIR"
            assert runtime.current_model_call_deadline.get() is not None
            clock.now += 32
            if repair_mode == "parent-expiry":
                runtime.current_limits.get().deadline = clock.now - 1
            runtime.check_model_call_deadline()
            raise AssertionError("late repair must not become accepted evidence")

    ranker = ControlledRepair(client=object(), model="fixture")

    class Retriever:
        def retrieve(self, query, eligible, pipeline, top_k):
            limits = runtime.current_limits.get()
            assert limits is not None
            limits.deadline = clock.now + (14 if repair_mode == "short-time" else 180)
            accepted = {"canonical_question_id": eligible[0], "canonical_text": "设计一套通知服务。"}
            invalid = {"canonical_question_id": "unresolved", "canonical_text": "设计服务并保证高并发和幂等。"}
            primary = RerankedCandidates([accepted, invalid], {"query_task": "INDEPENDENT_SYSTEM_DESIGN"})
            diagnostics = [{"canonical_question_id": invalid["canonical_question_id"],
                "decision": "INVALID_DESIGN_EVIDENCE", "scope_verification_rank": 2,
                "scope_verification_status": "INVALID_DESIGN_EVIDENCE",
                "scope_verification_keep": False, "model_scope_verification_keep": True}]
            if has_results:
                diagnostics.insert(0, {"canonical_question_id": eligible[0], "decision": "ACCEPTED",
                    "relevance_grade": 3, "quote_grounded": True, "scope_verification_keep": True})
            partial = RerankedCandidates([accepted] if has_results else [], {
                "scope_verification_status": "PARTIAL", "candidate_verification_status": "PARTIAL",
                "invalid_candidate_count": 1, "invalid_evidence_count": 1, "invalid_schema_count": 0,
                "candidate_count": 2, "candidates": diagnostics})
            repaired = ranker.repair_scope_evidence("工程设计题", primary, partial)
            assert runtime.current_model_call_deadline.get() is None
            return {"data": list(repaired), "meta": {"pipeline": pipeline,
                "rerank_status": "COMPLETED_PARTIAL", "candidate_count": 2,
                "candidate_verification_status": "PARTIAL", "invalid_candidate_count": 1,
                "rerank_audit": repaired.audit}}

    class Planner:
        def plan(self, run, context):
            return QuerySpec(action="SEARCH", search_query="工程设计题")

    client, db, _, _ = app_fixture(tmp_path, Planner(), Retriever())
    request_id = f"repair-time-{repair_mode}-{has_results}"
    response = client.post("/api/query", json={"message": "工程设计题", "request_id": request_id})
    successful = has_results and repair_mode != "parent-expiry"
    assert response.status_code == (200 if successful else 503), response.text
    with db.session() as session:
        receipt = session.scalar(select(AgentTurn).where(AgentTurn.request_id == request_id))
        assert receipt.status == ("SUCCEEDED" if successful else "FAILED")
        if repair_mode == "parent-expiry":
            assert receipt.error_code == "QUERY_DEADLINE_EXCEEDED"
        else:
            meta = receipt.response["facts"]["meta"]
            assert meta["relevance_status"] == ("VERIFIED" if has_results else "UNAVAILABLE")
            assert meta["result_completeness"]["verification_complete"] is False
            assert meta["rerank_audit"]["evidence_repair"]["status"] == (
                "SKIPPED_TIME" if repair_mode == "short-time" else "TIMED_OUT")
            assert meta["invalid_candidate_count"] == 1
            assert len(receipt.response["facts"]["data"]) == int(has_results)
            assert session.get(AgentConversation, receipt.conversation_id).version == int(has_results)
        run_id = receipt.id
    durable = client.get(f"/api/runs/{run_id}").json()["data"]
    assert durable["status"] == ("SUCCEEDED" if successful else "FAILED")
    assert durable["events"][-1]["type"] == ("completed" if successful else "failed")
    assert ranker.attempts == (0 if repair_mode == "short-time" else 1)
