"""Source-bound clarified business scope, including AI and unresolved proofs."""
import json
from types import SimpleNamespace

import pytest

from interview_intelligence.relevance_scope import compile_relevance_scope
from interview_intelligence.providers.budget import CallBudget
from interview_intelligence.search.business_scope import (
    BusinessScopeEvidence, business_candidate_payload, business_proof_assessment,
    resolve_business_evidence, UNIT_CODES, REJECTION_CODES, ROLE_CODES,
    CLARIFIED_ADJUDICATION_CONTRACT,
)
from interview_intelligence.search.reranker import LLMReranker
from interview_intelligence.search.design_scope import DesignVerification, design_proof_assessment
from interview_intelligence.tenant_retriever import PersonalReranker


QUERY = "所有工程系统设计题 具体业务系统设计"
REAL_MESSAGES = ["系统设计题", "列出所有工程系统设计题", QUERY, "按题意查找候选"]


def scope(messages=REAL_MESSAGES):
    return compile_relevance_scope(messages, candidate_mode=True)


def candidate(identity, text):
    return {"canonical_question_id": identity, "canonical_text": text,
            "retrieval_provenance": {"stage_ranks": {"business_target": 3}},
            "source_context": [{"source_context_status": "VERIFIED",
                "original_question": text, "original_question_complete": True}]}


class Provider:
    def __init__(self, units, *, primary_overrides=None, scope_overrides=None, wire_overrides=None, query_task=None):
        self.units = units
        self.primary_overrides = primary_overrides or {}
        self.scope_overrides = scope_overrides or {}
        self.wire_overrides = wire_overrides or {}
        self.query_task = query_task
        self.requests, self.responses = [], []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(request)
        body = json.loads(request["messages"][-1]["content"])
        primary = "rankings" in request["response_format"]["json_schema"]["schema"]["properties"]
        rows = []
        for entry in body["candidates"]:
            # Test labels belong to fixture source texts, never production logic.
            text = "".join(entry["question"].values())
            unit = next(unit for marker, unit in self.units.items() if marker in text)
            business = unit in {"BUSINESS_APPLICATION", "BUSINESS_WORKFLOW", "AI_APPLICATION"}
            positive = business or (body["relevance_scope"]["target_unit"] == "ANY_ENGINEERING"
                                   and unit in {"FOUNDATION_COMPONENT", "AI_INFRASTRUCTURE"})
            evidence = {"semantic_unit": unit, "unit_evidence": "s0o0",
                "business_goal_evidence": "s0o1" if business else "",
                "domain_evidence": "", "excluded_domain": "",
                "rejection_basis": "NONE" if positive else "TASK_TYPE" if unit == "KNOWLEDGE" else "TARGET_UNIT"}
            if primary:
                row = {"candidate_id": entry["id"], "candidate_task": "KNOWLEDGE" if unit == "KNOWLEDGE" else "DESIGN_TASK",
                    "task_evidence": "s0o0", "object_evidence": "q0", "focus_evidence": "q0",
                    "object_scope": "INSTANCE", "focus_scope": "SAME", **evidence}
                row.update(self.primary_overrides)
            else:
                row = {"candidate_id": entry["id"], "keep": positive,
                    "reason": "结构化证据决定范围，自由文本不构成授权",
                    "design_unit": "NEW_OR_REBUILT_SYSTEM" if positive else "LOCAL_SUBTASK",
                    "construction_evidence": "s0o0" if positive else "",
                    "constraint_a": "", "constraint_b": "", **evidence}
                row.update(self.scope_overrides)
            if "delivery_role" not in (self.primary_overrides if primary else self.scope_overrides):
                row["delivery_role"] = ("BUSINESS_OUTCOME" if row["semantic_unit"] in {
                    "BUSINESS_APPLICATION", "BUSINESS_WORKFLOW", "AI_APPLICATION"} else
                    "TECHNICAL_CAPABILITY" if row["semantic_unit"] in {"FOUNDATION_COMPONENT", "AI_INFRASTRUCTURE"} else
                    "HISTORICAL_REPORT" if any(word in text for word in ("当时", "之前", "已有", "去年", "做过", "已经")) else "EXPLANATION")
            role = row.pop("delivery_role")
            goal = (row.pop("business_goal_evidence", "") if role == "BUSINESS_OUTCOME" else
                    row.pop("technical_goal_evidence", "s0o1") if role == "TECHNICAL_CAPABILITY" else
                    row.pop("negative_task_evidence", "s0o0"))
            row.pop("business_goal_evidence", None)
            unit_codes = {value: key for key, value in UNIT_CODES.items()}
            rejection_codes = {value: key for key, value in REJECTION_CODES.items()}
            row["scope"] = {"u": unit_codes.get(row.pop("semantic_unit"), "INVALID"),
                "t": {value: key for key, value in ROLE_CODES.items()}[role],
                "e": row.pop("unit_evidence"), "g": goal,
                "p": row.pop("business_activity_evidence", goal if role == "BUSINESS_OUTCOME" else ""),
                "r": rejection_codes.get(row.pop("rejection_basis"), "INVALID"),
                "d": row.pop("excluded_domain"), "x": row.pop("domain_evidence")}
            if not primary:
                row.pop("reason")
                row["k"] = row.pop("scope_candidate_task", "K" if role == "EXPLANATION" else
                    "H" if role == "HISTORICAL_REPORT" else "D")
                row["a"] = row.pop("scope_task_evidence", "s0o0")
                row["l"] = row.pop("artifact_goal_relation", "B" if role == "BUSINESS_OUTCOME" else
                    "T" if role == "TECHNICAL_CAPABILITY" else "?")
                row["q"] = row.pop("scope_query_relation", None)
                row["z"] = row.pop("source_reference_claim", None)
            row.update(self.wire_overrides)
            rows.append(row)
        result = ({"query_task": self.query_task or ("INDEPENDENT_SYSTEM_DESIGN"
            if body["relevance_scope"]["independent_only"] else "OTHER"), "query_task_evidence": QUERY,
            "query_object": QUERY, "query_focus": QUERY, "query_object_evidence": QUERY,
            "query_focus_evidence": QUERY, "rankings": rows} if primary else {"verifications": rows})
        self.responses.append(result)
        return SimpleNamespace(model="fixture", usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(result)))])


@pytest.mark.parametrize("text,unit", [
    ("设计一个社区志愿服务匹配平台，居民发布需求并与志愿者预约服务。", "BUSINESS_APPLICATION"),
    ("设计一个实验室样本追踪流程，研究员登记样本并跟踪移交状态。", "BUSINESS_WORKFLOW"),
    ("设计一个AI辅助保险理赔应用，理赔人员核查材料并向客户解释处理进度。", "AI_APPLICATION"),
    ("设计一个企业资料问答Agent，员工根据权限查询制度并追溯答案依据。", "AI_APPLICATION"),
    ("设计一个智能制造异常处置助手，生产人员审核告警并派发处理工单。", "AI_APPLICATION"),
])
def test_business_and_ai_applications_pass_both_source_bound_stages(text, unit):
    source = candidate("unseen-business", text)
    provider = Provider({text.split("，")[0]: unit})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source], relevance_scope=scope())
    assert [row["canonical_question_id"] for row in rows] == ["unseen-business"]
    assert rows[0]["relevance_scope_status"] == "CONFIRMED"
    assert rows[0]["retrieval_provenance"] == source["retrieval_provenance"]
    assert rows.audit["confirmed_candidate_count"] == 1
    assert rows.audit["pending_candidate_count"] == 0
    assert len(provider.requests) == 2
    for request in provider.requests:
        body = json.loads(request["messages"][-1]["content"])
        assert body["relevance_scope"] == scope()
        assert body["relevance_scope"]["excluded_domains"] == []
        assert body["relevance_scope"]["independent_only"] is False
        schema = request["response_format"]["json_schema"]["schema"]
        row_type = "CompactBusinessCandidateRank" if "rankings" in schema["properties"] else "CompactBusinessVerification"
        assert "scope" in schema["$defs"][row_type]["required"]
        assert {"u", "t", "e", "g", "p", "r", "d", "x"} == set(schema["$defs"]["CompactScopeProof"]["required"])
        assert schema["$defs"]["CompactScopeProof"]["properties"]["d"]["enum"] == [""]
        assert schema["$defs"]["CompactScopeProof"]["properties"]["x"]["enum"] == [""]


@pytest.mark.parametrize("text,unit", [
    ("设计一个分布式时钟服务，支持跨机房时钟同步与误差监测。", "FOUNDATION_COMPONENT"),
    ("设计一个热点数据缓存，支持并发读写与缓存淘汰。", "FOUNDATION_COMPONENT"),
    ("设计一个事件投递队列，保证消息重试和延迟投递。", "FOUNDATION_COMPONENT"),
    ("设计一个大模型推理批处理调度器，支持动态合批与显存配额。", "AI_INFRASTRUCTURE"),
    ("设计一个Agent运行时，支持工具注册和执行调度。", "AI_INFRASTRUCTURE"),
    ("解释向量索引的原理，比较近邻检索的召回率。", "KNOWLEDGE"),
])
def test_concrete_business_scope_rejects_grounded_component_infrastructure_and_knowledge(text, unit):
    provider = Provider({text.split("，")[0]: unit})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("unseen-negative", text)], relevance_scope=scope())
    assert rows == []
    assert rows.audit["pending_candidate_count"] == 0
    assert rows.audit["candidates"][0]["decision"] in {"SCOPE_REJECTED", "TASK_REJECTED"}
    assert len(provider.requests) == 2
    proof = rows.audit["candidates"][0]
    assert proof["scope_adjudication_contract"] == CLARIFIED_ADJUDICATION_CONTRACT
    assert proof["scope_adjudication_status"] == "VERIFIED"
    assert proof["scope_verification_status"] == "SCOPE_REJECTED"


def test_scope_verifier_independently_rejects_component_despite_primary_business_claim():
    provider = Provider({"通用资源池": "FOUNDATION_COMPONENT"}, primary_overrides={
        "semantic_unit": "BUSINESS_APPLICATION", "business_goal_evidence": "s0o1", "rejection_basis": "NONE"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("component", "设计一个通用资源池，支持任务并发和资源回收。")], relevance_scope=scope())
    assert rows == [] and len(provider.requests) == 2
    assert rows.audit["candidates"][0]["semantic_unit"] == "FOUNDATION_COMPONENT"


@pytest.mark.parametrize("phase", ["primary", "scope"])
def test_unauthorized_ai_exclusion_is_repaired_once_then_preserved_as_pending(phase):
    overrides = {"rejection_basis": "EXCLUDED_DOMAIN", "excluded_domain": "AI", "domain_evidence": "s0o0"}
    provider = Provider({"AI辅助理赔应用": "AI_APPLICATION"},
        primary_overrides=overrides if phase == "primary" else None,
        scope_overrides={**overrides, "keep": False, "reason": "不属于传统业务系统"} if phase == "scope" else None)
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("ai-app", "设计一个AI辅助理赔应用，理赔人员审核案件并联系客户补充材料。")], relevance_scope=scope())
    if phase == "primary":
        assert rows[0]["relevance_scope_status"] == "CONFIRMED"
        audit = rows.audit["candidates"][0]
        assert audit["primary_diagnostic"]["business_assessment"]["host_reason"] == "UNAUTHORIZED_DOMAIN_EXCLUSION"
        assert audit["scope_task_recovery"] == "RECOVERED" and audit["rejection_basis"] == "NONE"
        assert len(provider.requests) == 2
        return
    assert len(rows) == 1 and rows[0]["relevance_scope_status"] == "PENDING"
    assert rows[0]["relevance_grade"] == 0
    assert rows.audit["confirmed_candidate_count"] == 0
    assert rows.audit["pending_candidate_count"] == 1
    assert rows.audit["candidate_verification_status"] == "PARTIAL"
    assert rows.audit["candidates"][0]["business_assessment"]["host_reason"] == "UNAUTHORIZED_DOMAIN_EXCLUSION"
    assert len(provider.requests) == (2 if phase == "primary" else 3)
    assert all(json.loads(request["messages"][-1]["content"])["relevance_scope"] == scope()
               for request in provider.requests)


@pytest.mark.parametrize("overrides", [
    {"semantic_unit": "UNKNOWN", "rejection_basis": "UNKNOWN", "unit_evidence": "", "business_goal_evidence": ""},
    {"unit_evidence": "s9o0"},
    {"business_goal_evidence": "邻题的客户订单目标"},
    {"rejection_basis": "TARGET_UNIT"},
])
def test_unknown_forged_and_contradictory_business_proofs_are_pending(overrides):
    provider = Provider({"志愿服务平台": "BUSINESS_APPLICATION"}, scope_overrides={"keep": False, **overrides})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("unresolved", "设计一个志愿服务平台，居民预约服务并查看受理状态。")], relevance_scope=scope())
    assert len(rows) == 1 and rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["pending_candidate_count"] == 1
    assert rows.audit["evidence_repair"]["unresolved_count"] == 1
    assert len(provider.requests) == 3


@pytest.mark.parametrize("field", ["candidate_task", "object_scope", "focus_scope"])
def test_primary_unknown_task_or_relevance_axis_is_not_a_semantic_negative(field):
    provider = Provider({"志愿服务平台": "BUSINESS_APPLICATION"}, primary_overrides={field: "UNKNOWN"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
        candidate("unknown-axis", "设计一个志愿服务平台，居民预约服务并查看受理状态。")], relevance_scope=scope())
    if field == "candidate_task":
        assert rows[0]["relevance_scope_status"] == "CONFIRMED"
        assert rows.audit["candidates"][0]["primary_diagnostic"]["candidate_task"] == "UNKNOWN"
        assert rows.audit["candidates"][0]["scope_candidate_task"] == "DESIGN_TASK"
        return
    assert len(rows) == 1 and rows[0]["relevance_scope_status"] == "PENDING"
    assert len(provider.requests) == 3  # one bounded independent query-axis repair
    assert rows.audit["candidates"][0]["scope_host_reason"] == "PRIMARY_RELEVANCE_AXES_UNRESOLVED"


def test_business_goal_cannot_borrow_another_occurrence_even_with_shared_component_text():
    source = candidate("cross-occurrence", "设计一个通用队列，支持并发消费。")
    source["source_context"].append({"source_context_status": "VERIFIED",
        "original_question": "设计一个订阅业务，用户订阅作者并收到更新通知。", "original_question_complete": True})
    payload = business_candidate_payload([source])[0]
    proof = BusinessScopeEvidence(semantic_unit="BUSINESS_APPLICATION", delivery_role="BUSINESS_OUTCOME", unit_evidence="s0o0",
        business_goal_evidence="s1o1", rejection_basis="NONE", excluded_domain="", domain_evidence="")
    assessment = business_proof_assessment(source, resolve_business_evidence(proof, payload), scope())
    assert not assessment["grounded"]
    assert assessment["host_reason"] == "BUSINESS_PROOFS_FROM_DIFFERENT_OCCURRENCES"


def test_wide_engineering_scope_still_accepts_foundation_and_ai_infrastructure():
    provider = Provider({"通用资源池": "FOUNDATION_COMPONENT", "推理调度器": "AI_INFRASTRUCTURE"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
        candidate("base", "设计一个通用资源池，支持并发分配和资源回收。"),
        candidate("ai-infra", "设计一个推理调度器，支持显存配额和动态合批。")],
        relevance_scope=scope(["列出所有工程系统设计题"]))
    assert len(rows) == 2 and all(row["relevance_scope_status"] == "CONFIRMED" for row in rows)


@pytest.mark.parametrize("phase,max_calls", [("primary", 1), ("scope", 2)])
def test_no_remaining_repair_budget_keeps_unknown_candidate_pending(phase, max_calls):
    overrides = {"semantic_unit": "UNKNOWN", "rejection_basis": "UNKNOWN",
                 "unit_evidence": "", "business_goal_evidence": ""}
    provider = Provider({"志愿服务平台": "BUSINESS_APPLICATION"},
        primary_overrides=overrides if phase == "primary" else None,
        scope_overrides={"keep": False, **overrides} if phase == "scope" else None)
    ranker = LLMReranker(client=provider, model="fixture", budget=CallBudget(max_calls=max_calls, max_tokens=96000))
    rows = ranker.rerank(QUERY, [candidate("pending", "设计一个志愿服务平台，居民预约服务并查看状态。")],
                         relevance_scope=scope())
    assert len(provider.requests) == max_calls
    assert len(rows) == 1 and rows[0]["relevance_scope_status"] == "PENDING"
    repair = rows.audit["primary_evidence_repair" if phase == "primary" else "evidence_repair"]
    assert repair["status"] == ("REPLACED_BY_SCOPE_ADJUDICATION" if phase == "primary" else "SKIPPED_BUDGET")
    if phase == "primary":
        assert rows.audit["scope_adjudication"]["deferred_candidate_ids"] == ["pending"]
    assert repair["unresolved_count"] == 1


def test_scope_controls_independence_even_when_model_query_rewrite_invents_it():
    provider = Provider({"志愿服务平台": "BUSINESS_APPLICATION"})
    ranker = LLMReranker(client=provider, model="fixture")
    request = ranker._scope_request("独立系统设计 完整传统业务系统", [
        candidate("b", "设计一个志愿服务平台，居民预约服务并查看状态。")], relevance_scope=scope())
    instructions = request["messages"][0]["content"]
    assert "本轮为宽工程交付复核" in instructions
    assert "本轮独立设计复核schema优先" not in instructions


@pytest.mark.parametrize("messages,expected", [
    (REAL_MESSAGES, "OTHER"),
    ([QUERY, "只看独立系统设计的具体业务系统设计题", "按题意查找候选"], "INDEPENDENT_SYSTEM_DESIGN"),
])
def test_scope_query_task_schema_comes_only_from_host_independence(messages, expected):
    ranker = LLMReranker(client=object(), model="fixture")
    request = ranker._primary_request(QUERY, [candidate("new", "设计一个邻里互助应用，居民登记需求并预约帮助。")],
        relevance_scope=scope(messages))
    assert request["response_format"]["json_schema"]["schema"]["properties"]["query_task"]["enum"] == [expected]
    assert "INDEPENDENT_SYSTEM_DESIGN用于工程设计任务" not in request["messages"][0]["content"]


def test_scope_query_task_header_violation_remains_pending_and_audits_model_vs_host():
    provider = Provider({"邻里互助应用": "BUSINESS_APPLICATION"}, query_task="INDEPENDENT_SYSTEM_DESIGN")
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
        candidate("unseen-header", "设计一个邻里互助应用，居民登记需求并预约帮助。")], relevance_scope=scope())
    assert len(rows) == 1 and rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["query_task"] == "OTHER"
    assert rows.audit["query_task_contract"] == {"source": "host_relevance_scope",
        "target_unit": "BUSINESS_APPLICATION", "independent_only": False, "host_value": "OTHER",
        "model_value": "INDEPENDENT_SYSTEM_DESIGN", "status": "CONFLICT"}
    assert rows.audit["candidates"][0]["scope_host_reason"] == "QUERY_TASK_CONTRADICTS_HOST_SCOPE"
    assert len(provider.requests) == 2  # one bounded primary proof repair, no extra retry loop


@pytest.mark.parametrize("text", [
    "假设由你重新设计教学报名应用，学生选择课程并收到报名结果。",
    "面向新增转诊需求重新设计患者预约工作流，医护人员确认接诊并通知患者。",
])
def test_unseen_current_redesign_requests_with_business_task_proof_are_confirmed(text):
    provider = Provider({text.split("，")[0]: "BUSINESS_APPLICATION"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("new-counterfactual", text)], relevance_scope=scope())
    assert len(rows) == 1 and rows[0]["relevance_scope_status"] == "CONFIRMED"


@pytest.mark.parametrize("text", [
    "假如请你重新设计这个应用，你会如何选择方案。",
    "如果由你重新设计培训报名平台，学员选课后需要得到报名状态。",
])
def test_non_delivery_task_label_conflicting_with_current_redesign_is_pending_not_dropped(text):
    provider = Provider({text.split("，")[0]: "KNOWLEDGE"},
        primary_overrides={"candidate_task": "PROJECT_REPORT"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("unseen-counterfactual", text)], relevance_scope=scope())
    assert len(rows) == 1 and rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["candidates"][0]["scope_host_reason"] == "NON_DELIVERY_REJECTION_CONTRADICTS_SOURCE_TASK"
    assert rows.audit["primary_evidence_repair"]["unresolved_count"] == 1
    assert len(provider.requests) == 3


def test_business_unit_with_negative_task_shape_cannot_silently_become_hard_rejection():
    provider = Provider({"培训报名平台": "BUSINESS_APPLICATION"},
        primary_overrides={"candidate_task": "PROJECT_REPORT"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
        candidate("unit-task-conflict", "重新设计培训报名平台，学员选课后得到报名结果。")], relevance_scope=scope())
    assert rows[0]["relevance_scope_status"] == "CONFIRMED"
    assert rows.audit["candidates"][0]["primary_diagnostic"]["scope_host_reason"] == "BUSINESS_TASK_SHAPE_CONTRADICTION"
    assert rows.audit["candidates"][0]["scope_task_recovery"] == "RECOVERED"
    assert rows.audit["candidates"][0]["business_assessment"]["outcome"] == "POSITIVE"


def test_second_stage_cannot_call_same_counterfactual_delivery_a_project_report():
    provider = Provider({"培训报名平台": "BUSINESS_APPLICATION"}, scope_overrides={
        "semantic_unit": "KNOWLEDGE", "business_goal_evidence": "", "rejection_basis": "TASK_TYPE",
        "design_unit": "EXISTING_PROJECT", "construction_evidence": "", "keep": False})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
        candidate("scope-task-conflict", "如果由你重新设计培训报名平台，学员选课后需要得到报名状态。")], relevance_scope=scope())
    assert len(rows) == 1 and rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["candidates"][0]["scope_host_reason"] == "NON_DELIVERY_REJECTION_CONTRADICTS_SOURCE_TASK"
    assert rows.audit["evidence_repair"]["unresolved_count"] == 1
    assert len(provider.requests) == 3


def test_bounded_repair_can_resolve_a_counterfactual_task_conflict_with_complete_business_proof():
    class RepairProvider(Provider):
        def create(self, **request):
            if self.requests:
                self.units = {"疫苗预约应用": "BUSINESS_APPLICATION"}
                self.primary_overrides = {}
            return super().create(**request)
    provider = RepairProvider({"疫苗预约应用": "KNOWLEDGE"},
        primary_overrides={"candidate_task": "PROJECT_REPORT"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
        candidate("repair-new-task", "假设让你重新设计疫苗预约应用，居民选择接种时间并确认预约结果。")], relevance_scope=scope())
    assert len(rows) == 1 and rows[0]["relevance_scope_status"] == "CONFIRMED"
    assert rows.audit["candidates"][0]["scope_task_recovery"] == "RECOVERED"
    assert len(provider.requests) == 2


@pytest.mark.parametrize("text", [
    "介绍你已有的教学报名项目，讲讲学生选课流程当时是怎样实现的。",
    "描述你当时重新设计过的报名应用，讲讲已经交付的学生选课方案。",
    "解释应用设计原理是什么，比较两种既有方案的区别。",
])
def test_historical_redesign_and_explanatory_reports_remain_semantic_negatives(text):
    provider = Provider({text.split("，")[0]: "KNOWLEDGE"},
        primary_overrides={"candidate_task": "PROJECT_REPORT"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("unseen-past-report", text)], relevance_scope=scope())
    assert rows == [] and rows.audit["pending_candidate_count"] == 0
    proof = rows.audit["candidates"][0]
    assert proof["primary_diagnostic"]["business_assessment"]["host_reason"] == "GROUNDED_NON_DELIVERY_TASK"
    assert proof["scope_adjudication_status"] == "VERIFIED"
    assert proof["scope_verification_status"] == "SCOPE_REJECTED"


@pytest.mark.parametrize("text,complete,expected,reason", [
    ("介绍你已有的订单查询方案，然后重新设计订单超时回滚流程，要求释放库存并通知用户。", True,
        "PENDING", "NON_DELIVERY_REJECTION_CONTRADICTS_SOURCE_TASK"),
    ("介绍你已有的订单查询方案，", False, "PENDING", "NON_DELIVERY_SOURCE_INCOMPLETE"),
    ("不要设计新系统，解释已有系统的执行原理。", True, "EXCLUDE", "GROUNDED_NON_DELIVERY_TASK"),
    ("不要求你设计新系统，说明已有系统的工作原理。", True, "EXCLUDE", "GROUNDED_NON_DELIVERY_TASK"),
    ("当时你怎么设计订单支付模块？", True, "EXCLUDE", "GROUNDED_NON_DELIVERY_TASK"),
])
def test_negative_task_proof_checks_whole_same_original_and_preserves_negation_and_past_tense(text, complete, expected, reason):
    source = candidate("independent-task-frame", text)
    source["source_context"][0]["original_question_complete"] = complete
    explanation = text.startswith(("不要", "不要求"))
    provider = Provider({text.split("，")[0].rstrip("？"): "KNOWLEDGE"},
        primary_overrides={"candidate_task": "PROJECT_REPORT", **({"delivery_role": "EXPLANATION",
            "candidate_task": "KNOWLEDGE", "task_evidence": "s0o1", "unit_evidence": "s0o1", "negative_task_evidence": "s0o1"} if explanation else {})},
        scope_overrides={"delivery_role": "EXPLANATION", "scope_candidate_task": "K",
            "scope_task_evidence": "s0o1", "unit_evidence": "s0o1", "negative_task_evidence": "s0o1"} if explanation else None)
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source], relevance_scope=scope())
    assert ("PENDING" if rows else "EXCLUDE") == expected
    proof = rows.audit["candidates"][0]
    assert proof["primary_diagnostic"]["business_assessment"]["host_reason"] == reason
    if expected == "EXCLUDE":
        assert proof["scope_adjudication_status"] == "VERIFIED"
        assert proof["scope_verification_status"] == "SCOPE_REJECTED"


def test_neighboring_new_delivery_does_not_turn_current_history_report_into_counterfactual():
    source = candidate("neighbor-frame", "介绍你已有的订单查询方案，讲讲当时如何实现订单查询。")
    source["source_context"][0]["context_after"] = "下一题：重新设计订单超时回滚流程，要求释放库存并通知用户。"
    provider = Provider({"订单查询方案": "KNOWLEDGE"}, primary_overrides={"candidate_task": "PROJECT_REPORT"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source], relevance_scope=scope())
    assert rows == [] and rows.audit["pending_candidate_count"] == 0


def test_clipped_canonical_fallback_cannot_confirm_a_historical_task_negative():
    source = {"canonical_question_id": "unseen-clipped-fallback",
        "canonical_text": "介绍已有的社区服务项目。" + "已有流程记录。" * 100 + "然后重新设计居民预约处理流程。"}
    proof = BusinessScopeEvidence(semantic_unit="KNOWLEDGE", delivery_role="HISTORICAL_REPORT", unit_evidence="介绍已有的社区服务项目。",
        business_goal_evidence="", rejection_basis="TASK_TYPE", excluded_domain="", domain_evidence="")
    assessment = business_proof_assessment(source, proof, scope())
    assert not assessment["grounded"] and assessment["host_reason"] == "NON_DELIVERY_SOURCE_INCOMPLETE"


def test_business_workflow_is_not_silently_rejected_by_an_invented_independent_unit():
    provider = Provider({"样本追踪流程": "BUSINESS_WORKFLOW"}, scope_overrides={
        "design_unit": "LOCAL_SUBTASK", "keep": True})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
        candidate("workflow", "设计一个样本追踪流程，研究员登记样本并追踪移交状态。")], relevance_scope=scope())
    assert len(rows) == 1 and rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["candidates"][0]["scope_host_reason"] == "BUSINESS_DESIGN_UNIT_CONTRADICTION"


def test_normalized_business_title_cannot_invent_delivery_absent_from_original():
    source = candidate("project", "设计一个志愿服务平台，居民预约服务并查看受理状态。")
    source["source_context"][0]["original_question"] = "介绍你已有项目的志愿服务平台，居民预约的实现是怎样的。"
    provider = Provider({"志愿服务平台": "BUSINESS_APPLICATION"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source], relevance_scope=scope())
    assert rows == []
    assert rows.audit["pending_candidate_count"] == 0
    assert rows.audit["candidates"][0]["scope_host_reason"] == "EXPLICIT_PROJECT_REPORT"


def test_unverified_raw_source_is_preserved_pending_instead_of_borrowing_business_title():
    source = candidate("unverified", "设计一个志愿服务平台，居民预约服务并查看受理状态。")
    source["source_context"][0]["source_context_status"] = "HASH_MISMATCH"
    provider = Provider({"志愿服务平台": "BUSINESS_APPLICATION"}, primary_overrides={
        "task_evidence": "q0", "unit_evidence": "q0", "business_goal_evidence": "q1"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source], relevance_scope=scope())
    assert len(rows) == 1 and rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["candidates"][0]["scope_host_reason"] == "SCOPE_SOURCE_UNAVAILABLE"


def test_explicit_ai_exclusion_requires_user_quote_and_current_candidate_domain_evidence():
    narrowed = scope([QUERY, "不要AI应用"])
    source = candidate("ai", "设计一个AI辅助理赔应用，理赔人员审核案件并向客户解释结果。")
    provider = Provider({"AI辅助理赔应用": "AI_APPLICATION"}, primary_overrides={
        "rejection_basis": "EXCLUDED_DOMAIN", "excluded_domain": "AI", "domain_evidence": "s0o0"},
        scope_overrides={"keep": False, "rejection_basis": "EXCLUDED_DOMAIN", "excluded_domain": "AI", "domain_evidence": "s0o0"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source], relevance_scope=narrowed)
    assert rows == [] and rows.audit["pending_candidate_count"] == 0
    provider = Provider({"AI辅助理赔应用": "AI_APPLICATION"}, primary_overrides={
        "rejection_basis": "EXCLUDED_DOMAIN", "excluded_domain": "AI", "domain_evidence": "不存在的原文"},
        scope_overrides={"keep": False, "rejection_basis": "EXCLUDED_DOMAIN", "excluded_domain": "AI", "domain_evidence": "不存在的原文"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source], relevance_scope=narrowed)
    assert rows[0]["relevance_scope_status"] == "PENDING"


def test_personal_reranker_forwards_scope_and_retains_safe_error_boundary(monkeypatch):
    seen = []
    def capture(self, query, candidates, *, relevance_scope=None):
        seen.append(relevance_scope)
        raise RuntimeError("private provider detail must not escape")
    monkeypatch.setattr(LLMReranker, "rerank", capture)
    with pytest.raises(ValueError, match="^MODEL_PROVIDER_UNAVAILABLE$"):
        PersonalReranker(client=object(), model="fixture").rerank(QUERY, [], relevance_scope=scope())
    assert seen == [scope()]


def test_all50_compact_wire_rows_roundtrip_to_complete_standard_proofs_without_dropping_candidates():
    provider = Provider({"志愿服务平台": "BUSINESS_APPLICATION"})
    sources = [candidate(f"business-{index}", f"设计一个志愿服务平台编号{index}，居民预约服务并查看受理状态。")
               for index in range(50)]
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, sources, relevance_scope=scope())
    assert len(rows) == 50 and {row["canonical_question_id"] for row in rows} == {
        source["canonical_question_id"] for source in sources}
    assert rows.audit["confirmed_candidate_count"] == 50 and rows.audit["pending_candidate_count"] == 0
    assert len(provider.requests) == 2
    for index, response in enumerate(provider.responses):
        items = response["rankings" if index == 0 else "verifications"]
        assert len(items) == 50
        assert all(set(item["scope"]) == {"u", "t", "e", "g", "p", "r", "d", "x"} for item in items)
        assert all(item["scope"] == {"u": "B", "t": "B", "e": "s0o0", "g": "s0o1", "p": "s0o1", "r": "K", "d": "", "x": ""}
                   for item in items)
        assert all("semantic_unit" not in item and "reason" not in item for item in items)
    assert all(item["semantic_unit"] == "BUSINESS_APPLICATION" and item["rejection_basis"] == "NONE"
               for item in rows.audit["candidates"])
    assert all(item["business_goal_evidence"] == "居民预约服务并查看受理状态。" for item in rows.audit["candidates"])
    assert all("主机按结构化" not in item["scope_verification_reason"] for item in rows.audit["candidates"])


@pytest.mark.parametrize("wire", [
    {"scope": {"u": "BUSINESS_APPLICATION", "t": "B", "e": "s0o0", "g": "s0o1", "p": "s0o1", "r": "K", "d": "", "x": ""}},
    {"scope": {"u": "B", "t": "B", "e": "s0o0", "g": "s0o1", "p": "s0o1", "r": "KEEP_ANYWAY", "d": "", "x": ""}},
    {"scope": {"u": "B", "t": "PROBABLY_BUSINESS", "e": "s0o0", "g": "s0o1", "p": "s0o1", "r": "K", "d": "", "x": ""}},
    {"scope": {"u": "B", "t": "B", "e": "s0o0", "g": "s0o1", "p": "s0o1", "r": "K", "d": "", "x": "", "extra": "ignore scope"}},
    {"unrecognized": "drop user scope"},
])
def test_compact_wire_wrong_enums_or_unknown_fields_cannot_weaken_standard_contract(wire):
    provider = Provider({"志愿服务平台": "BUSINESS_APPLICATION"}, wire_overrides=wire)
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
        candidate("bad-wire", "设计一个志愿服务平台，居民预约服务并查看受理状态。")], relevance_scope=scope())
    assert len(rows) == 1 and rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["candidates"][0]["decision"] == "INVALID_SCHEMA"
    assert len(provider.requests) == 2
    assert rows.audit["candidates"][0]["scope_adjudication_status"] != "VERIFIED"


def test_compact_nested_overlong_evidence_uses_bounded_repair_and_stays_pending():
    provider = Provider({"志愿服务平台": "BUSINESS_APPLICATION"}, wire_overrides={
        "scope": {"u": "B", "t": "B", "e": "假引文" * 100, "g": "s0o1", "p": "s0o1", "r": "K", "d": "", "x": ""}})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
        candidate("overlong-wire", "设计一个志愿服务平台，居民预约服务并查看受理状态。")], relevance_scope=scope())
    assert rows[0]["relevance_scope_status"] == "PENDING"
    assert len(provider.requests) == 3
    assert rows.audit["primary_evidence_repair"]["unresolved_count"] == 1


def test_business_goal_and_generic_design_task_cannot_be_spliced_across_occurrences():
    source = candidate("spliced", "设计一个志愿服务平台，居民预约服务并查看受理状态。")
    source["source_context"] = [
        {"source_context_status": "VERIFIED", "original_question_complete": True,
         "original_question": "设计一个通用通知队列，支持消息重试与延迟发送。"},
        {"source_context_status": "VERIFIED", "original_question_complete": True,
         "original_question": "介绍你已有项目的志愿服务应用，居民预约服务并查看受理状态。"}]
    provider = Provider({"志愿服务平台": "BUSINESS_APPLICATION"}, scope_overrides={
        "unit_evidence": "s1o0", "business_goal_evidence": "s1o1", "construction_evidence": "s0o0"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source], relevance_scope=scope())
    assert len(rows) == 1 and rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["candidates"][0]["scope_host_reason"] == "ASKED_TASK_PROOFS_FROM_DIFFERENT_OCCURRENCES"
    assert len(provider.requests) == 3


@pytest.mark.parametrize("phase", ["primary", "scope"])
def test_explicit_ai_exclusion_cannot_be_ignored_by_positive_ai_unit(phase):
    narrowed = scope([QUERY, "排除 AI 应用"])
    provider = Provider({"AI理赔应用": "AI_APPLICATION"},
        primary_overrides={"semantic_unit": "BUSINESS_APPLICATION"} if phase == "scope" else None)
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
        candidate("ai-exclusion-conflict", "设计一个AI理赔应用，理赔人员审核材料并向客户解释结论。")], relevance_scope=narrowed)
    assert len(rows) == 1 and rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["confirmed_candidate_count"] == 0
    assert rows.audit["candidates"][0]["scope_host_reason"] == "AI_UNIT_ACCEPTANCE_CONTRADICTS_AUTHORIZED_EXCLUSION"
    assert len(provider.requests) == 3


@pytest.mark.parametrize("domain", ["Agent", "MCP"])
def test_ai_unit_alone_does_not_imply_an_agent_or_mcp_exclusion(domain):
    provider = Provider({"AI理赔应用": "AI_APPLICATION"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [
        candidate("ai-non-agent", "设计一个AI理赔应用，理赔人员审核材料并向客户解释结论。")],
        relevance_scope=scope([QUERY, f"排除 {domain} 应用"]))
    assert rows[0]["relevance_scope_status"] == "CONFIRMED"


@pytest.mark.parametrize("text", [
    "把诊所分诊助手改成规则引擎实现，保留症状登记和人工复核流程。",
    "将设备租赁平台换成事件驱动实现，租户办理借还并查看损坏赔付结果。",
    "将社区预约应用替换为规则服务，居民选择时段并接收受理状态通知。",
])
def test_new_business_transformations_are_delivery_without_independent_newbuild(text):
    provider = Provider({text.split("，")[0]: "BUSINESS_WORKFLOW"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("novel-transformation", text)], relevance_scope=scope())
    assert rows[0]["relevance_scope_status"] == "CONFIRMED"
    assert rows.audit["candidates"][0]["design_assessment"]["outcome"] == "POSITIVE"


def test_same_conversion_grammar_does_not_turn_technical_capability_into_business():
    text = "把模型推理调度器换成规则引擎实现，保留批处理和显存配额分配。"
    provider = Provider({"模型推理调度器": "AI_INFRASTRUCTURE"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("novel-technical-transform", text)], relevance_scope=scope())
    assert rows == []
    audit = rows.audit["candidates"][0]
    assert audit["business_assessment"]["outcome"] == "NEGATIVE"
    assert audit["technical_goal_evidence"] == "保留批处理和显存配额分配。"
    assert audit["business_goal_evidence"] == ""


@pytest.mark.parametrize("unit,goal", [("如何合理设计架构", "居民借出物品后收到归还状态通知"),
    ("你会怎么设计", "居民借出物品后收到归还状态通知"),
    ("设计这个系统", "居民借出物品后收到归还状态通知"),
    ("设计社区借还平台", "用户"), ("设计社区借还平台", "订单"), ("设计社区借还平台", "支付")])
def test_generic_unit_or_nominal_business_background_is_typed_insufficient(unit, goal):
    text = unit + "，" + goal + "。"
    evidence = BusinessScopeEvidence(semantic_unit="BUSINESS_APPLICATION", delivery_role="BUSINESS_OUTCOME",
        unit_evidence=unit, business_goal_evidence=goal, rejection_basis="NONE", excluded_domain="", domain_evidence="")
    result = business_proof_assessment(candidate("insufficient-statement", text), evidence, scope())
    assert result["outcome"] == "INSUFFICIENT" and not result["grounded"]


@pytest.mark.parametrize("goal", ["用户需要使用这个系统", "甲团队需要资料工具，乙团队需要收费工具",
                                  "甲部门提供客户资料工具", "支持这个系统"])
def test_consumer_names_and_generic_capabilities_cannot_establish_a_business_result(goal):
    text = "设计一个共享工具门户，" + goal + "。"
    proof = BusinessScopeEvidence(semantic_unit="BUSINESS_APPLICATION", delivery_role="BUSINESS_OUTCOME",
        unit_evidence="设计一个共享工具门户", business_goal_evidence=goal, rejection_basis="NONE",
        excluded_domain="", domain_evidence="")
    assessment = business_proof_assessment(candidate("background-consumers", text), proof, scope())
    assert assessment["outcome"] == "INSUFFICIENT"


def test_providing_explicit_business_behavior_is_not_a_technical_consumer_background():
    text = "设计一个诊所预约平台，为患者提供预约时段选择和取消服务。"
    provider = Provider({"诊所预约平台": "BUSINESS_WORKFLOW"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("actual-business-service", text)], relevance_scope=scope())
    assert rows[0]["relevance_scope_status"] == "CONFIRMED"


@pytest.mark.parametrize("phase", ["primary", "scope"])
def test_generic_positive_unit_is_visible_pending_and_repaired_once(phase):
    text = "设计一个设备借还应用，居民登记物品并跟踪借还状态。你会怎么设计？"
    provider = Provider({"设备借还应用": "BUSINESS_APPLICATION"},
        primary_overrides={"unit_evidence": "s0o2"} if phase == "primary" else None,
        scope_overrides={"unit_evidence": "s0o2"} if phase == "scope" else None)
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("generic-unit", text)], relevance_scope=scope())
    if phase == "primary":
        assert rows[0]["relevance_scope_status"] == "CONFIRMED"
        assert rows.audit["candidates"][0]["primary_diagnostic"]["business_assessment"]["outcome"] == "INSUFFICIENT"
        assert rows.audit["candidates"][0]["scope_task_recovery"] == "RECOVERED"
        return
    assert rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["candidates"][0]["business_assessment"]["outcome"] == "INSUFFICIENT"
    assert len(provider.requests) == (2 if phase == "primary" else 3)


@pytest.mark.parametrize("repair", [False, True])
def test_complete_original_does_not_make_bare_construction_quote_a_conclusive_negative(repair):
    class RepairConstructionProvider(Provider):
        def create(self, **request):
            if repair and len(self.requests) == 2:
                self.scope_overrides = {}
            return super().create(**request)
    provider = RepairConstructionProvider({"设备借还应用": "BUSINESS_APPLICATION"},
        scope_overrides={"construction_evidence": "s0o2"})
    text = "设计一个设备借还应用，居民登记物品并跟踪借还状态。你会怎么设计？"
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("bare-construction", text)], relevance_scope=scope())
    assert rows[0]["relevance_scope_status"] == ("CONFIRMED" if repair else "PENDING")
    audit = rows.audit["candidates"][0]
    initial = audit["initial_diagnostic"] if repair else audit
    assert initial["design_assessment"]["outcome"] == "INSUFFICIENT"
    assert initial["evidence_repair_eligible"] is True
    assert len(provider.requests) == 3


@pytest.mark.parametrize("text", ["多租户检验单数据怎么隔离？", "事件接收器和规则执行器怎么做的，规则之间会冲突吗？"])
def test_actionable_or_ambiguous_implementation_cannot_be_hard_knowledge_negative(text):
    provider = Provider({text.split("，")[0]: "KNOWLEDGE"}, primary_overrides={"candidate_task": "UNKNOWN"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("ambiguous-how", text)], relevance_scope=scope())
    assert rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["candidates"][0]["business_assessment"]["outcome"] == "INSUFFICIENT"


@pytest.mark.parametrize("role,goal", [("TECHNICAL_CAPABILITY", "订单查询"), ("BACKGROUND", "订单查询时用户需要结果"),
                                      ("UNKNOWN", "支持分页查询与索引更新")])
def test_technical_rejection_needs_actual_technical_capability_not_missing_business_goal(role, goal):
    text = "为预约SQL设计联合索引，" + goal + "。"
    evidence = BusinessScopeEvidence(semantic_unit="FOUNDATION_COMPONENT", delivery_role=role,
        unit_evidence="为预约SQL设计联合索引", technical_goal_evidence=goal,
        rejection_basis="TARGET_UNIT", excluded_domain="", domain_evidence="")
    result = business_proof_assessment(candidate("local-index", text), evidence, scope())
    assert result["outcome"] == "INSUFFICIENT"


def test_actual_technical_artifact_and_capability_are_negative_despite_business_consumers():
    text = "设计一个通用索引组件，供诊所预约与保险理赔服务复用，支持索引更新和查询路由。"
    provider = Provider({"通用索引组件": "FOUNDATION_COMPONENT"}, primary_overrides={
        "technical_goal_evidence": "s0o2"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("technical-consumers", text)], relevance_scope=scope())
    assert rows == [] and rows.audit["candidates"][0]["business_assessment"]["outcome"] == "NEGATIVE"


@pytest.mark.parametrize("task", ["LOCAL_DETAIL", "ALGORITHM_TASK"])
def test_business_background_plus_workflow_label_cannot_bypass_local_or_algorithm_conflict(task):
    text = "诊所预约表需要按患者编号查询，为这条SQL设计联合索引并解释字段顺序。"
    provider = Provider({"诊所预约表": "BUSINESS_WORKFLOW"}, primary_overrides={"candidate_task": task,
        "business_goal_evidence": "s0o0"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("index-business-background", text)], relevance_scope=scope())
    assert rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["candidates"][0]["scope_host_reason"] in {"BUSINESS_TASK_SHAPE_CONTRADICTION", "CURRENT_TASK_NOT_ESTABLISHED"}


@pytest.mark.parametrize("messages,status", [(REAL_MESSAGES, "PENDING"), (["列出所有工程系统设计题"], "CONFIRMED")])
def test_mixed_business_boundary_is_pending_only_for_business_target(messages, status):
    text = "设计一个跨部门工具接入平台，支持工具注册和访问授权。"
    provider = Provider({"跨部门工具接入平台": "MIXED_OR_AMBIGUOUS"}, primary_overrides={
        "delivery_role": "TECHNICAL_CAPABILITY", "technical_goal_evidence": "s0o1", "rejection_basis": "NONE"},
        scope_overrides={"delivery_role": "TECHNICAL_CAPABILITY", "technical_goal_evidence": "s0o1",
                         "rejection_basis": "NONE", "keep": True, "design_unit": "COMPONENT_OR_WORKFLOW",
                         "construction_evidence": "s0o0"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("mixed-boundary", text)], relevance_scope=scope(messages))
    assert rows[0]["relevance_scope_status"] == status


@pytest.mark.parametrize("raw,complete,reason", [
    ("介绍已有的课程预约项目，学员登记后可查看预约结果。", False, "NON_DELIVERY_SOURCE_INCOMPLETE"),
    ("代码题：实现课程预约接口，", False, "NON_DELIVERY_SOURCE_INCOMPLETE"),
])
def test_typed_design_negative_cannot_rely_on_incomplete_original(raw, complete, reason):
    source = candidate("clipped-negative", raw)
    source["source_context"][0]["original_question_complete"] = complete
    proof = DesignVerification(candidate_id="c0", keep=True, reason="fixture",
        design_unit="NEW_OR_REBUILT_SYSTEM", construction_evidence=raw, constraint_a="", constraint_b="")
    result = design_proof_assessment(source, proof, query="工程架构设计", typed=True)
    assert result["outcome"] == "INSUFFICIENT" and result["host_reason"] == reason


def test_typed_design_uses_authoritative_original_instead_of_canonical_code_label():
    raw = "设计一个课程预约应用，学员选择时段并收到预约结果。"
    source = candidate("wrong-canonical-negative", raw)
    source["canonical_text"] = "手写代码设计一个课程预约应用。"
    proof = DesignVerification(candidate_id="c0", keep=True, reason="fixture", design_unit="NEW_OR_REBUILT_SYSTEM",
        construction_evidence="设计一个课程预约应用", constraint_a="", constraint_b="")
    assert design_proof_assessment(source, proof, query="工程架构设计", typed=True)["outcome"] == "POSITIVE"


def test_authorized_domain_exclusion_does_not_require_business_or_technical_goal():
    proof = BusinessScopeEvidence(semantic_unit="UNKNOWN", delivery_role="UNKNOWN",
        unit_evidence="设计一个AI辅助随访平台", rejection_basis="EXCLUDED_DOMAIN", excluded_domain="AI",
        domain_evidence="AI辅助随访平台")
    result = business_proof_assessment(candidate("domain", "设计一个AI辅助随访平台。"), proof,
        scope([QUERY, "排除AI应用"]), model_keep=False)
    assert result["outcome"] == "NEGATIVE" and result["host_reason"] == "AUTHORIZED_DOMAIN_EXCLUSION"


@pytest.mark.parametrize("independent,keep,status", [(False, True, "CONFIRMED"), (True, True, "EXCLUDE"),
                                                    (True, False, "EXCLUDE")])
def test_explicit_independent_boundary_negative_is_not_overridden_by_business_keep_flag(independent, keep, status):
    text = "设计诊所预约系统的撤销功能，患者取消预约后释放号源并通知医生。"
    messages = [QUERY, "只看独立系统设计的具体业务系统设计题"] if independent else REAL_MESSAGES
    provider = Provider({"诊所预约系统": "BUSINESS_WORKFLOW"}, scope_overrides={"keep": keep})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("parent-bound-business-feature", text)], relevance_scope=scope(messages))
    assert (rows[0]["relevance_scope_status"] if rows else "EXCLUDE") == status
    if independent:
        assert rows.audit["candidates"][0]["business_assessment"]["host_reason"] == "AUTHORIZED_INDEPENDENT_UNIT_REJECTION"


@pytest.mark.parametrize("phase", ["primary", "scope"])
def test_consumers_honor_typed_insufficient_even_without_invalid_field_names(monkeypatch, phase):
    import interview_intelligence.search.reranker as implementation
    original = implementation.business_proof_assessment
    def insufficient(source, proof, host_scope, **kwargs):
        result = original(source, proof, host_scope, **kwargs)
        if (phase == "scope") == hasattr(proof, "keep"):
            return {**result, "outcome": "INSUFFICIENT", "grounded": False,
                "host_reason": "DELIVERY_PROOF_INSUFFICIENT", "invalid_evidence_fields": []}
        return result
    monkeypatch.setattr(implementation, "business_proof_assessment", insufficient)
    provider = Provider({"设备借还应用": "BUSINESS_APPLICATION"})
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("typed-no-fields", "设计一个设备借还应用，居民登记物品并查看借还状态。")], relevance_scope=scope())
    if phase == "primary":
        assert rows[0]["relevance_scope_status"] == "CONFIRMED"
        assert rows.audit["candidates"][0]["primary_diagnostic"]["business_assessment"]["outcome"] == "INSUFFICIENT"
        assert rows.audit["candidates"][0]["scope_task_recovery"] == "RECOVERED"
        return
    assert rows[0]["relevance_scope_status"] == "PENDING"
    assert rows.audit["candidates"][0]["decision"] == "INVALID_BUSINESS_EVIDENCE"
    assert rows.audit["candidates"][0]["evidence_repair_eligible"] is True
    assert len(provider.requests) == (2 if phase == "primary" else 3)


@pytest.mark.parametrize("source_marker", [
    {"source_context_version": "source_context_v3"}, {"source_context": []},
    {"source_context_required": True},
    {"source_context": [{"source_context_status": "UNAVAILABLE", "original_question": ""}]},
    {"source_context": [{"source_context_status": "INVALID_SPANS", "original_question": "设计预约平台。"}]},
    {"source_context": [{"source_context_status": "VERIFIED", "original_question": ""}]},
])
@pytest.mark.parametrize("negative", [False, True])
def test_source_aware_empty_or_invalid_original_never_falls_back_to_canonical_verdict(source_marker, negative):
    text = "解释工作流状态机的概念。" if negative else "设计课程预约平台，学员选课并确认预约结果。"
    source = {"canonical_question_id": "source-aware", "canonical_text": text, **source_marker}
    proof = BusinessScopeEvidence(semantic_unit="KNOWLEDGE" if negative else "BUSINESS_APPLICATION",
        delivery_role="EXPLANATION" if negative else "BUSINESS_OUTCOME", unit_evidence=text,
        business_goal_evidence="" if negative else "学员选课并确认预约结果",
        negative_task_evidence=text if negative else "", rejection_basis="TASK_TYPE" if negative else "NONE",
        excluded_domain="", domain_evidence="")
    assert business_proof_assessment(source, proof, scope())["outcome"] == "INSUFFICIENT"
    design = DesignVerification(candidate_id="c0", keep=True, reason="fixture", design_unit="NEW_OR_REBUILT_SYSTEM",
        construction_evidence=text, constraint_a="", constraint_b="")
    assert design_proof_assessment(source, design, query="工程系统设计", typed=True)["outcome"] == "INSUFFICIENT"


def test_canonical_only_legacy_caller_keeps_its_explicit_fallback_contract():
    source = {"canonical_text": "设计课程预约平台，学员选课并确认预约结果。"}
    proof = DesignVerification(candidate_id="c0", keep=True, reason="fixture", design_unit="NEW_OR_REBUILT_SYSTEM",
        construction_evidence="设计课程预约平台", constraint_a="", constraint_b="")
    assert design_proof_assessment(source, proof, query="工程系统设计", typed=True)["outcome"] == "POSITIVE"


@pytest.mark.parametrize("marker", [{"source_context_required": True}, {"source_context_version": "source_context_v3"},
                                   {"source_context": []}, {"source_context": [{"source_context_status": "VERIFIED",
                                    "original_question": "", "context_before": "介绍已有项目"}]}])
def test_source_required_marker_survives_prompt_and_repair_without_canonical_task_options(marker):
    source = {"canonical_question_id": "missing-original", "canonical_text": "设计志愿服务平台，居民预约并确认服务状态。", **marker}
    provider = Provider({"志愿服务平台": "BUSINESS_APPLICATION"}, primary_overrides={
        "unit_evidence": "q0", "business_goal_evidence": "q1", "task_evidence": "q0"})
    ranker = LLMReranker(client=provider, model="fixture")
    rows = ranker.rerank(QUERY, [source], relevance_scope=scope())
    assert rows[0]["relevance_scope_status"] == "PENDING"
    assert len(provider.requests) == 1  # source absence has no model repair
    for request in provider.requests:
        payload = json.loads(request["messages"][-1]["content"])["candidates"][0]
        for key, value in marker.items():
            assert payload[key] == value or key == "source_context"  # verified text is represented by excerpt IDs
        assert payload["business_evidence_options"] == []
        schema = request["response_format"]["json_schema"]["schema"]
        assert schema["$defs"]["CompactScopeProof"]["properties"]["e"]["enum"] == [""]
        assert schema["$defs"]["CompactBusinessCandidateRank"]["properties"]["task_evidence"]["enum"] == [""]
        assert payload["question"]["q0"] == "设计志愿服务平台，"  # remains available for object/focus
