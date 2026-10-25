"""Current asked-delivery relation, independent of the frozen private gold."""
import json
import time

import pytest

from test_business_scope_verification import Provider, QUERY, candidate, scope
from interview_intelligence.providers.budget import CallBudget
from interview_intelligence.providers.runtime import RequestLimits, current_limits
from interview_intelligence.search.business_scope import business_candidate_payload
from interview_intelligence.search.reranker import LLMReranker, RerankedCandidates


@pytest.mark.parametrize("text,unit,status", [
    ("设计一个社区包裹领取功能，居民取件。", "BUSINESS_WORKFLOW", "CONFIRMED"),
    ("设计一个文艺演出票务功能，10万人抢票。", "BUSINESS_APPLICATION", "CONFIRMED"),
    ("设计一个通用请求缓冲组件，要求低延迟。", "FOUNDATION_COMPONENT", "EXCLUDE"),
    ("设计一个任务公平调度组件，保证公平性。", "FOUNDATION_COMPONENT", "EXCLUDE"),
])
def test_short_results_are_not_a_character_length_test(text, unit, status):
    provider = Provider({text.split("，")[0]: unit})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [candidate("short", text)], relevance_scope=scope())
    assert (rows[0]["relevance_scope_status"] if rows else "EXCLUDE") == status


@pytest.mark.parametrize("text,unit,field,goal", [
    ("设计一个社区包裹领取功能，居民登记包裹并领取。", "BUSINESS_WORKFLOW", "business_goal_evidence", "包裹"),
    ("设计一个通用请求缓冲组件，要求低延迟，容量100000。", "FOUNDATION_COMPONENT", "technical_goal_evidence", "100000"),
])
def test_object_substring_or_isolated_number_is_not_a_result(text, unit, field, goal):
    provider = Provider({text.split("，")[0]: unit}, primary_overrides={field: goal}, scope_overrides={field: goal})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [candidate("no-goal", text)], relevance_scope=scope())
    assert rows[0]["relevance_scope_status"] == "PENDING"


def test_fresh_scope_can_recover_workflow_without_rewriting_primary_task():
    text = "设计一个社区物品归还流程，居民归还物品后释放押金并通知物主。"
    provider = Provider({"社区物品归还流程": "BUSINESS_WORKFLOW"}, primary_overrides={"candidate_task": "LOCAL_DETAIL"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [candidate("flow", text)], relevance_scope=scope())
    proof = rows.audit["candidates"][0]
    assert rows[0]["relevance_scope_status"] == "CONFIRMED"
    assert proof["candidate_task"] == proof["primary_diagnostic"]["candidate_task"] == "LOCAL_DETAIL"
    assert proof["scope_candidate_task"] == "DESIGN_TASK" and proof["scope_task_recovery"] == "RECOVERED"
    assert proof["verification_resolution"] == "RESOLVED" and proof["evidence_status"] == "VALID"
    assert proof["invalid_evidence_fields"] == []
    assert proof["primary_diagnostic"]["verification_resolution"] == "UNRESOLVED"
    # v42 independent initial scope is deliberately blind to provisional
    # primary semantics; the original failure stays in the nested diagnostic.
    assert "repair_feedback" not in json.loads(provider.requests[1]["messages"][1]["content"])
    assert len(provider.requests) == 2


@pytest.mark.parametrize("relation", ["?", "C"])
def test_new_design_label_does_not_override_missing_relation(relation):
    provider = Provider({"材料补正应用": "BUSINESS_APPLICATION"}, primary_overrides={"candidate_task": "LOCAL_DETAIL"},
        scope_overrides={"artifact_goal_relation": relation})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
        candidate("unknown-relation", "设计一个材料补正应用，申请人提交材料并接收补正意见。")], relevance_scope=scope())
    assert rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["candidates"][0]["scope_host_reason"] == "ASKED_ARTIFACT_GOAL_RELATION_UNRESOLVED"


@pytest.mark.parametrize("complete", [True, False])
def test_history_is_not_a_current_technical_capability(complete):
    source = candidate("past", "通知网关和消息规则怎么做的，以前遇到过规则冲突吗？")
    source["source_context"][0]["original_question_complete"] = complete
    provider = Provider({"通知网关": "FOUNDATION_COMPONENT"}, primary_overrides={"candidate_task": "LOCAL_DETAIL"},
        scope_overrides={"scope_candidate_task": "L"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source], relevance_scope=scope())
    assert rows[0]["relevance_scope_status"] == "PENDING"


def test_current_technical_local_operation_is_a_valid_business_scope_negative():
    provider = Provider({"联合索引": "FOUNDATION_COMPONENT"}, primary_overrides={"candidate_task": "LOCAL_DETAIL"},
        scope_overrides={"scope_candidate_task": "L"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
        candidate("index", "为这条SQL设计联合索引，要求低延迟。")], relevance_scope=scope())
    assert rows == [] and rows.audit["candidates"][0]["business_assessment"]["outcome"] == "NEGATIVE"


@pytest.mark.parametrize("mutation", ["clipped", "another-question", "invalid-source"])
def test_negative_requires_complete_current_sources(mutation):
    source = candidate("negative", "设计一个请求缓冲组件，要求低延迟。")
    if mutation == "clipped":
        source["source_context"][0]["original_question_complete"] = False
    else:
        source["source_context"].append({"source_context_status": "VERIFIED" if mutation == "another-question" else "INVALID_SPANS",
            "original_question": "设计复诊预约功能，患者改期后通知医生。", "original_question_complete": True})
    provider = Provider({"请求缓冲组件": "FOUNDATION_COMPONENT"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source], relevance_scope=scope())
    assert rows[0]["relevance_scope_status"] == "PENDING"


def test_referential_ask_has_an_exact_span_and_scoped_wire_omits_only_neighbors():
    text = "设计一个社区探访预约功能，住户申请探访并由志愿者确认时段，你会怎么实现？"
    source = candidate("referential", text)
    source["source_context"][0].update(context_before="介绍去年项目。", context_after="然后设计索引。")
    assert "s0o0:s0o2" in business_candidate_payload([source])[0]["construction_ranges"]
    provider = Provider({"社区探访预约功能": "BUSINESS_APPLICATION"}, scope_overrides={
        "scope_task_evidence": "s0o0:s0o2", "construction_evidence": "s0o0:s0o2"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source], relevance_scope=scope())
    assert rows[0]["relevance_scope_status"] == "CONFIRMED" and rows[0]["source_context"] == source["source_context"]
    assert rows.audit["candidates"][0]["scope_task_evidence"] == text
    for request in provider.requests:
        item = json.loads(request["messages"][1]["content"])["candidates"][0]
        assert "q0" in item["question"]
        assert "context_before" not in item["source_context"][0] and "context_after" not in item["source_context"][0]
        assert "".join(item["source_context"][0]["original_question"].values()) == text
        assert all(key.startswith("s") for key in item["construction_options"])


def test_feedback_changes_prepared_hash_and_conservative_reservation():
    source = candidate("hash", "设计一个材料补正应用，申请人提交材料并接收补正意见。")
    ranker = LLMReranker(client=object(), model="fixture")
    plain = ranker._scope_request(QUERY, [source], relevance_scope=scope())
    feedback = ranker._scope_request(QUERY, RerankedCandidates([source], {"scope_feedback": {"hash": {
        "decision": "INVALID_BUSINESS_EVIDENCE", "scope_host_reason": "BUSINESS_TASK_SHAPE_CONTRADICTION",
        "invalid_evidence_fields": ["candidate_task"], "candidate_task": "LOCAL_DETAIL"}}}), relevance_scope=scope())
    assert plain["prepared_sha256"] != feedback["prepared_sha256"]
    assert feedback["token_upper_bound"] > plain["token_upper_bound"]


def test_budget_deferral_keeps_all_rows_and_does_not_shrink_primary():
    sources = [candidate(f"fair-{i}", f"设计一个材料补正应用编号{i}，申请人提交材料并接收补正意见。") for i in range(24)]
    provider = Provider({"材料补正应用": "BUSINESS_APPLICATION"}, primary_overrides={"candidate_task": "LOCAL_DETAIL"})
    ranker = LLMReranker(client=provider, model="fixture")
    upper = ranker._primary_request(QUERY, sources, relevance_scope=scope())["input_token_upper_bound"]
    ranker.budget = CallBudget(max_calls=2, max_tokens=upper + 151)
    rows = ranker.rerank(QUERY, sources, relevance_scope=scope())
    assert len(json.loads(provider.requests[0]["messages"][1]["content"])["candidates"]) == 24
    assert len(rows) == 24
    plan = rows.audit["scope_adjudication"]
    assert 0 < len(plan["selected_candidate_ids"]) < 24 and plan["deferred_candidate_ids"]
    assert all(row["relevance_scope_status"] == "PENDING" for row in rows if row["canonical_question_id"] in plan["deferred_candidate_ids"])
    assert len(provider.requests) == 2


def test_failure_after_last_admitted_call_preserves_pending():
    class FailingScope(Provider):
        def create(self, **request):
            if self.requests:
                current_limits.get().calls = 6
                raise ValueError("MODEL_OUTPUT_TRUNCATED")
            return super().create(**request)
    provider = FailingScope({"材料补正应用": "BUSINESS_APPLICATION"})
    token = current_limits.set(RequestLimits(deadline=time.monotonic() + 180, max_tokens=96000))
    try:
        rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
            candidate("failure", "设计一个材料补正应用，申请人提交材料并接收补正意见。")], relevance_scope=scope())
    finally:
        current_limits.reset(token)
    assert rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["scope_adjudication"]["error_type"] == "ValueError"


@pytest.mark.parametrize("current_request,expected", [(False, "EXCLUDE"), (True, "PENDING")])
def test_explicit_experience_frame_and_new_current_delivery_are_distinct(current_request, expected):
    text = "实习遇到过高并发场景吗，都怎么解决其中问题的？"
    if current_request:
        text += "现在重新设计一个社区派单流程，居民提交需求后分配志愿者并通知结果。"
    provider = Provider({"实习遇到过": "KNOWLEDGE"}, primary_overrides={"candidate_task": "PROJECT_REPORT", "delivery_role": "HISTORICAL_REPORT"},
        scope_overrides={"scope_candidate_task": "H", "delivery_role": "HISTORICAL_REPORT"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [candidate("history-task", text)], relevance_scope=scope())
    assert (rows[0]["relevance_scope_status"] if rows else "EXCLUDE") == expected


def test_feasible_mandatory_subset_is_selected_before_optional_repairs():
    costs = {"a1": 30, "a2": 30, "a3": 30, "c1": 15, "c2": 15}
    sources = [candidate(key, "设计一个材料补正应用，申请人提交材料并接收补正意见。") for key in costs]
    class CostRanker(LLMReranker):
        def _primary_request(self, *args, **kwargs):
            return {"token_upper_bound": 1}
        def _rank_candidates(self, query, candidates, **kwargs):
            diagnostics = [{"canonical_question_id": row["canonical_question_id"], "input_rank": i + 1, "model_rank": i + 1,
                "relevance_grade": 2, "quote_grounded": True, "candidate_task": "DESIGN_TASK",
                "decision": "ACCEPTED" if i < 3 else "INVALID_BUSINESS_EVIDENCE", "evidence_repair_eligible": True,
                "scope_host_reason": "Z_MANDATORY" if i < 3 else "A_REPAIR"} for i, row in enumerate(candidates)]
            return RerankedCandidates(candidates[:3], {"candidates": diagnostics, "candidate_verification_status": "PARTIAL",
                "invalid_candidate_count": 2})
        def _scope_request(self, query, candidates, **kwargs):
            return {"token_upper_bound": sum(costs[row["canonical_question_id"]] for row in candidates)}
        def _verify_scope(self, query, candidates, **kwargs):
            return RerankedCandidates([], {"candidates": [{"canonical_question_id": row["canonical_question_id"],
                "decision": "SCOPE_REJECTED", "scope_verification_status": "SCOPE_REJECTED"} for row in candidates],
                "scope_verification_status": "COMPLETE", "invalid_candidate_count": 0})
    result = CostRanker(client=object(), model="fixture", budget=CallBudget(max_calls=6, max_tokens=70)).rerank(
        QUERY, sources, relevance_scope=scope())
    assert result.audit["scope_adjudication"]["selected_candidate_ids"] == ["a1", "a2"]
    assert set(result.audit["scope_adjudication"]["deferred_candidate_ids"]) == {"a3", "c1", "c2"}


def test_global_header_violation_cannot_be_erased_by_a_fresh_scope_negative():
    provider = Provider({"请求缓冲组件": "FOUNDATION_COMPONENT"}, query_task="INDEPENDENT_SYSTEM_DESIGN")
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
        candidate("negative-with-bad-header", "设计一个请求缓冲组件，要求低延迟。")], relevance_scope=scope())
    assert rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["query_task_contract"]["status"] == "CONFLICT"
    assert rows.audit["candidates"][0]["scope_host_reason"] == "QUERY_TASK_CONTRADICTS_HOST_SCOPE"
    assert rows.audit["candidates"][0]["verification_resolution"] == "UNRESOLVED"
