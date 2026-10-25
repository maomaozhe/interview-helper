"""Source-bound reassessment controls, independent of the private gold."""
from copy import deepcopy
import json
import time

import pytest

from test_business_scope_verification import Provider, QUERY, candidate, scope
from interview_intelligence.providers.runtime import RequestLimits, current_limits
from interview_intelligence.search.business_scope import business_candidate_payload
from interview_intelligence.search.citations import resolve_task_option
from interview_intelligence.search.reranker import LLMReranker, RerankedCandidates


def run(text, *, primary=None, verification=None, unit="BUSINESS_APPLICATION"):
    provider = Provider({text.split("，")[0]: unit}, primary_overrides=primary, scope_overrides=verification)
    result = LLMReranker(client=provider, model="fixture").rerank(QUERY, [candidate("unseen", text)], relevance_scope=scope())
    return result, provider


def test_source_title_newline_normalizes_offsets_with_quote():
    text = "场景题：设计一个社区取件系统\n居民登记包裹并查看取件码。"
    override = {"unit_evidence": "s0o1", "business_goal_evidence": "s0o2"}
    result, _ = run(text, primary={**override, "task_evidence": "s0o1"}, verification={
        **override, "scope_task_evidence": "s0o1", "construction_evidence": "s0o1"})
    assert result[0]["relevance_scope_status"] == "CONFIRMED"
    assert result.audit["candidates"][0]["design_assessment"]["outcome"] == "POSITIVE"


def test_exact_source_range_need_not_be_on_semantic_offer_list():
    text = "开发一个诊所排班功能，医生登记可用时段，患者选择预约时段。"
    payload = business_candidate_payload([candidate("range", text)])[0]
    assert "s0o0:s0o2" not in payload["construction_ranges"]
    result, provider = run(text, verification={"construction_evidence": "s0o0:s0o2"})
    assert result[0]["relevance_scope_status"] == "CONFIRMED"
    proof = result.audit["candidates"][0]
    assert proof["construction_evidence"] == text
    schema = provider.requests[1]["response_format"]["json_schema"]["schema"]
    assert "pattern" in schema["$defs"]["CompactBusinessVerification"]["properties"]["construction_evidence"]


def test_primary_current_task_can_use_same_source_structural_range():
    text = "材料太多，希望助手完成一轮筛查，你怎么实现？"
    result, provider = run(text, primary={"unit_evidence": "s0o1", "business_goal_evidence": "s0o1",
        "task_evidence": "s0o0:s0o2"}, verification={"unit_evidence": "s0o1", "business_goal_evidence": "s0o1",
        "construction_evidence": "s0o0:s0o2", "scope_task_evidence": "s0o0:s0o2", "design_unit": "COMPONENT_OR_WORKFLOW"})
    assert result[0]["relevance_scope_status"] == "CONFIRMED"
    assert result.audit["candidates"][0]["primary_diagnostic"]["task_evidence"] == text


@pytest.mark.parametrize("reference", ["s0o0:s1o1", "s0o2:s0o0", "s0o0:s0o99", "q0:q2", "s9o0"])
def test_source_range_cross_origin_unknown_reverse_or_canonical_stays_pending(reference):
    result, _ = run("设计一个公益物品借还应用，居民借出物品并查看归还状态。",
        verification={"construction_evidence": reference})
    assert result[0]["relevance_scope_status"] == "PENDING"


def test_forged_non_contiguous_range_and_embedded_other_question_are_rejected():
    payload = business_candidate_payload([candidate("range", "设计一个社区取件功能，居民登记包裹，领取后更新状态。")])[0]
    assert resolve_task_option("s0o0:s0o2", payload, {"s0o0:s0o2": ["s0o0", "s0o2"]}) is None
    payload = business_candidate_payload([candidate("boundary", "设计一个取件应用。\n2. 解释缓存原理。")])[0]
    assert resolve_task_option("s0o0:s0o2", payload) is None
    assert resolve_task_option("s0o0:s0o" + "9" * 120, payload) is None
    assert resolve_task_option("s0o" + "9" * 60 + ":s0o" + "9" * 61, payload) is None


def test_task_and_construction_can_share_complete_same_source_delivery():
    text = "候选材料太多，希望助手完成一轮筛查，你怎么实现？"
    result, _ = run(text, primary={"unit_evidence": "s0o1", "business_goal_evidence": "s0o1", "task_evidence": "s0o2"},
        verification={"unit_evidence": "s0o1", "business_goal_evidence": "s0o1", "scope_task_evidence": "s0o1",
            "construction_evidence": "s0o0:s0o2", "design_unit": "COMPONENT_OR_WORKFLOW"}, unit="AI_APPLICATION")
    assert result[0]["relevance_scope_status"] == "CONFIRMED"


def test_current_source_task_can_supply_absent_construction_but_not_unknown_reference():
    text = "设计一个居民访客登记功能，住户确认来访并生成通行码。"
    result, _ = run(text, verification={"construction_evidence": "", "scope_task_evidence": "s0o0"})
    assert result[0]["relevance_scope_status"] == "CONFIRMED"
    result, _ = run(text, verification={"construction_evidence": "s0o99", "scope_task_evidence": "s0o0"})
    assert result[0]["relevance_scope_status"] == "PENDING"


def test_numbered_subject_prefix_is_not_the_requirement_proposition():
    text = "设计一个共享实验设备预约应用，（1）同一台仪器，优先分配给预约时长短的研究员。"
    result, _ = run(text, primary={"business_goal_evidence": "s0o1"}, verification={"business_goal_evidence": "s0o1"})
    assert result[0]["relevance_scope_status"] == "PENDING"
    assert result.audit["candidates"][0]["business_assessment"]["host_reason"] == "DELIVERY_GOAL_IS_INCOMPLETE_REQUIREMENT"
    result, _ = run(text, primary={"business_goal_evidence": "s0o1:s0o2"}, verification={"business_goal_evidence": "s0o1:s0o2"})
    assert result[0]["relevance_scope_status"] == "CONFIRMED"


def test_unnumbered_subject_opener_needs_its_result_but_modal_requirement_is_a_statement():
    text = "设计一个共享展位预约应用，同一个展位，优先分配给预约时间早的住户。"
    result, _ = run(text, primary={"business_goal_evidence": "s0o1"}, verification={"business_goal_evidence": "s0o1"})
    assert result[0]["relevance_scope_status"] == "PENDING"
    text = "设计一个居民签约应用，同一个居民只能签约一次，重复请求返回原结果。"
    result, _ = run(text, primary={"business_goal_evidence": "s0o1"}, verification={"business_goal_evidence": "s0o1"})
    assert result[0]["relevance_scope_status"] == "CONFIRMED"


def test_fresh_grounded_relation_recovers_primary_axes_and_keeps_original_facts():
    relation = {"o": "INSTANCE", "f": "SAME", "e": "s0o0", "h": "s0o1", "g": 3}
    result, provider = run("设计一个图书归还流程，读者归还后释放押金并通知管理员。", primary={
        "candidate_task": "LOCAL_DETAIL", "object_scope": "INSTANCE", "focus_scope": "ADJACENT"},
        verification={"scope_query_relation": relation}, unit="BUSINESS_WORKFLOW")
    row = result.audit["candidates"][0]
    assert result[0]["relevance_scope_status"] == "CONFIRMED" and result[0]["relevance_grade"] == 3
    assert row["primary_diagnostic"]["relevance_grade"] == 1
    assert row["candidate_task"] == row["primary_diagnostic"]["candidate_task"] == "LOCAL_DETAIL"
    assert row["scope_candidate_task"] == "DESIGN_TASK"
    assert row["effective_query_relation_source"] == "scope_independent_reassessment"
    assert len(provider.requests) == 2


@pytest.mark.parametrize("relation", [
    {"o": "UNKNOWN", "f": "UNKNOWN", "e": "", "h": "", "g": 0},
    {"o": "INSTANCE", "f": "SAME", "e": "q0", "h": "q1", "g": 3},
    {"o": "INSTANCE", "f": "SAME", "e": "s9o0", "h": "s0o1", "g": 3},
    {"o": "INSTANCE", "f": "SAME", "e": "s0o0", "h": "s0o1", "g": 2},
])
def test_keep_and_business_positive_cannot_create_missing_axes(relation):
    result, provider = run("设计一个图书归还流程，读者归还后释放押金并通知管理员。",
        primary={"focus_scope": "UNKNOWN"}, verification={"scope_query_relation": relation})
    assert result[0]["relevance_scope_status"] == "PENDING"
    assert len(provider.requests) == 3  # the final axes failure is repair eligible


def test_new_axes_failure_enters_bounded_independent_repair():
    text = "设计一个图书归还流程，读者归还后释放押金并通知管理员。"
    class Changing(Provider):
        def create(self, **request):
            if len(self.requests) == 2:
                self.scope_overrides = {"scope_query_relation": {"o": "INSTANCE", "f": "SAME", "e": "s0o0", "h": "s0o1", "g": 3}}
            return super().create(**request)
    provider = Changing({"图书归还流程": "BUSINESS_WORKFLOW"}, primary_overrides={"focus_scope": "UNKNOWN"})
    result = LLMReranker(client=provider, model="fixture").rerank(QUERY, [candidate("flow", text)], relevance_scope=scope())
    assert result[0]["relevance_scope_status"] == "CONFIRMED"
    assert result.audit["evidence_repair"]["recovered_count"] == 1 and len(provider.requests) == 3
    feedback = json.loads(provider.requests[2]["messages"][1]["content"])["repair_feedback"]["candidates"][0]
    assert feedback["host_reason"] == "PRIMARY_RELEVANCE_AXES_UNRESOLVED"


@pytest.mark.parametrize("relation", [
    {"o": "INSTANCE", "f": "SAME", "e": "s9o0", "h": "s0o1", "g": 3},
    {"o": "INSTANCE", "f": "SAME", "e": "s0o0", "h": "s0o1", "g": 2},
])
def test_explicit_bad_fresh_relation_cannot_be_hidden_by_good_old_axes(relation):
    result, _ = run("设计一个居民领用预约应用，居民预约物品并接收领取通知。",
        verification={"scope_query_relation": relation})
    assert result[0]["relevance_scope_status"] == "PENDING"
    assert result.audit["candidates"][0]["primary_diagnostic"]["relevance_grade"] == 3


def test_fresh_query_relation_must_share_final_task_and_business_source_identity():
    text = "设计一个诊所预约应用，患者选择时段并接收预约结果。"
    source = candidate("same-title-different-occurrence", text)
    source["source_context"].append(deepcopy(source["source_context"][0]))
    provider = Provider({"诊所预约": "BUSINESS_APPLICATION"}, primary_overrides={"focus_scope": "UNKNOWN"},
        scope_overrides={"scope_query_relation": {"o": "INSTANCE", "f": "SAME", "e": "s1o0", "h": "s1o1", "g": 3}})
    result = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source], relevance_scope=scope())
    assert result[0]["relevance_scope_status"] == "PENDING"
    assert result.audit["candidates"][0]["scope_query_relation_assessment"]["grounded"] is False


def test_negative_canonical_status_needs_all_qualified_occurrences_proved_not_just_shown():
    text = "设计一个通用任务缓冲组件，要求低延迟。"
    source = candidate("negative", text)
    source["source_context"][0]["occurrence_id"] = "occ-a"
    source["source_context_coverage"] = {"qualified_occurrence_count": 1, "qualified_occurrence_ids": ["occ-a"], "complete": True}
    provider = Provider({"任务缓冲": "FOUNDATION_COMPONENT"})
    ranker = LLMReranker(client=provider, model="fixture")
    result = ranker.rerank(QUERY, [source], relevance_scope=scope())
    assert result == [] and result.audit["candidates"][0]["relevance_scope_status"] == "EXCLUDED"
    assert result.audit["candidates"][0]["source_verified_negative_occurrence_ids"] == ["occ-a"]
    source["source_context"].append({**source["source_context"][0], "occurrence_id": "occ-b"})
    source["source_context_coverage"] = {"qualified_occurrence_count": 2, "qualified_occurrence_ids": ["occ-a", "occ-b"], "complete": True}
    result = ranker.rerank(QUERY, [source], relevance_scope=scope())
    assert result[0]["relevance_scope_status"] == "PENDING"
    diagnostic = result.audit["candidates"][0]
    assert diagnostic["source_verified_negative_occurrence_ids"] == ["occ-a"]
    assert diagnostic["scope_host_reason"] == "NEGATIVE_CANONICAL_SOURCE_COVERAGE_UNRESOLVED"


def test_one_sufficient_positive_occurrence_does_not_require_negative_coverage():
    text = "设计一个诊所预约应用，患者选择时段并接收预约结果。"
    source = candidate("positive", text)
    source["source_context"][0]["occurrence_id"] = "occ-a"
    source["source_context_coverage"] = {"qualified_occurrence_count": 2, "qualified_occurrence_ids": ["occ-a", "occ-b"], "complete": False}
    provider = Provider({"诊所预约": "BUSINESS_APPLICATION"})
    result = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source], relevance_scope=scope())
    assert result[0]["relevance_scope_status"] == "CONFIRMED"


def test_budget_fair_repair_selects_feasible_subset_and_retains_every_deferred_row():
    sources = [candidate(f"case-{i}", f"设计一个住户访客应用{i}，住户确认来访并生成通行码。") for i in range(6)]
    checks = [{"canonical_question_id": row["canonical_question_id"], "scope_verification_rank": i + 1,
        "decision": "INVALID_BUSINESS_EVIDENCE", "model_scope_verification_keep": True,
        "scope_host_reason": "FAULT_A" if i < 3 else "FAULT_B", "evidence_repair_eligible": True}
        for i, row in enumerate(sources)]
    verified = RerankedCandidates([], {"candidates": checks, "scope_verification_status": "PARTIAL", "invalid_candidate_count": 6,
        "invalid_evidence_count": 6, "invalid_schema_count": 0})
    class CostRanker(LLMReranker):
        def _scope_request(self, query, rows, **kwargs):
            return {"token_upper_bound": 10 + 20 * len(rows)}
        def _verify_scope(self, query, rows, **kwargs):
            return RerankedCandidates(rows, {"candidates": [{**checks[int(row["canonical_question_id"].split("-")[1])],
                "decision": "ACCEPTED"} for row in rows]})
    ranker = CostRanker(client=object(), model="fixture")
    limits = RequestLimits(deadline=time.monotonic() + 60, max_tokens=55, max_calls=6, calls=4)
    token = current_limits.set(limits)
    try:
        result = ranker.repair_scope_evidence(QUERY, sources, verified, relevance_scope=scope())
    finally:
        current_limits.reset(token)
    repair = result.audit["evidence_repair"]
    assert repair["candidate_ids"] == ["case-0", "case-3"]
    assert len(repair["deferred_candidate_ids"]) == 4 and repair["recovered_count"] == 2
    assert len(result.audit["candidates"]) == 6
    assert all(row["evidence_repair_status"] == "DEFERRED_BUDGET_OR_TIME" for row in result.audit["candidates"]
               if row["canonical_question_id"] in repair["deferred_candidate_ids"])
