"""CODE_TASK may enter broad engineering verification, never bypass it."""
import json
from types import SimpleNamespace

import pytest

from interview_intelligence.search.reranker import LLMReranker


class EngineeringCodeClient:
    """Return fixed schema responses; no provider, service or network exists."""
    def __init__(self, *, task_reference="s0o1", construction_reference="s0o1",
                 focus_scope="SAME", scope_keep=True, design_unit="COMPONENT_OR_WORKFLOW",
                 candidate_task="CODE_TASK"):
        self.task_reference = task_reference
        self.construction_reference = construction_reference
        self.focus_scope = focus_scope
        self.scope_keep = scope_keep
        self.design_unit = design_unit
        self.candidate_task = candidate_task
        self.phases = []
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        request = json.loads(kwargs["messages"][-1]["content"])
        schema = kwargs["response_format"]["json_schema"]["schema"]
        self.requests.append(request)
        if "rankings" in schema["properties"]:
            self.phases.append("PRIMARY")
            query = request["query"]
            result = {"query_object": "工程", "query_focus": "交付", "query_task": "OTHER",
                      "query_object_evidence": query, "query_focus_evidence": query, "query_task_evidence": query,
                      "rankings": [{"candidate_id": "c0", "candidate_task": self.candidate_task,
                          "task_evidence": self.task_reference, "object_evidence": "q0", "focus_evidence": "q0",
                          "object_scope": "SAME", "focus_scope": self.focus_scope}]}
        else:
            self.phases.append("SCOPE")
            result = {"verifications": [{"candidate_id": "c0", "keep": self.scope_keep,
                       "reason": "fixed offline judgment", "design_unit": self.design_unit,
                       "construction_evidence": self.construction_reference, "constraint_a": "", "constraint_b": ""}]}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(result)))])


def candidate(raw="编程题：实现一个缓存服务。", *, canonical="实现一个缓存服务。", before="", status="VERIFIED"):
    return {"canonical_question_id": "synthetic-engineering", "canonical_text": canonical,
            "source_context_version": "candidate_source_context_v3", "source_context": [{
                "occurrence_id": "synthetic-occurrence", "revision_id": "synthetic-revision",
                "source_context_status": status, "original_question": raw,
                "original_question_complete": True, "context_before": before}]}


def execute(query="工程设计题", *, row=None, **client_options):
    client = EngineeringCodeClient(**client_options)
    rows = LLMReranker(client=client, model="offline-fixed-stub").rerank(query, [row or candidate()])
    return rows, rows.audit["candidates"][0], client


@pytest.mark.parametrize("query", ["工程设计题", "工程场景设计题", "组件设计题"])
@pytest.mark.parametrize("focus_scope,grade", [("SAME", 3), ("CORE", 2)])
def test_source_grounded_engineering_code_enters_scope_before_acceptance(query, focus_scope, grade):
    rows, diagnostic, client = execute(query, focus_scope=focus_scope)
    assert len(rows) == 1
    assert client.phases == ["PRIMARY", "SCOPE"]
    assert diagnostic["primary_diagnostic"]["candidate_task"] == "CODE_TASK"
    assert diagnostic["primary_diagnostic"]["decision"] == "ACCEPTED"
    assert diagnostic["relevance_grade"] == grade
    assert diagnostic["scope_verification_status"] == "CONFIRMED"
    assert diagnostic["scope_policy"] == "ENGINEERING_DELIVERY"
    assert diagnostic["construction_evidence"] == "实现一个缓存服务。"
    assert rows.audit["candidate_verification_status"] == "COMPLETE"


@pytest.mark.parametrize("query", ["独立系统设计题", "只要完整系统架构题", "工程架构设计题"])
def test_explicit_independent_or_architecture_scope_still_rejects_code(query):
    rows, diagnostic, client = execute(query)
    assert not rows
    assert client.phases == ["PRIMARY"]
    assert diagnostic["decision"] == "TASK_REJECTED"
    assert diagnostic["task_grounded"] is True
    assert diagnostic["scope_verification_status"] == "NOT_REQUIRED"


@pytest.mark.parametrize("raw,canonical", [
    ("编程题：实现一个数组排序算法。", "实现一个数组排序算法。"),
    ("编程题：实现一个系统中的链表反转算法。", "实现一个系统中的链表反转算法。"),
    ("编程题：介绍现有服务的内部实现原理。", "介绍现有服务的内部实现原理。"),
])
def test_code_form_without_current_engineering_delivery_does_not_enter_scope(raw, canonical):
    rows, diagnostic, client = execute(row=candidate(raw, canonical=canonical))
    assert not rows
    assert client.phases == ["PRIMARY"]
    assert diagnostic["decision"] == "TASK_REJECTED"
    assert diagnostic["relevance_grade"] == 3


@pytest.mark.parametrize("task_reference", ["q0", "s0b0"])
def test_canonical_or_neighbor_task_cannot_replace_the_current_original(task_reference):
    row = candidate("缓存的原理是什么？", canonical="实现一个缓存服务。", before="实现一个缓存服务。")
    rows, diagnostic, client = execute(row=row, task_reference=task_reference)
    assert not rows
    assert client.phases == ["PRIMARY"]
    assert diagnostic["decision"] in {"TASK_REJECTED", "INVALID_TASK_EVIDENCE"}
    assert diagnostic["scope_verification_status"] == "NOT_REQUIRED"


def test_scope_model_negative_is_not_overridden_by_engineering_code_admission():
    rows, diagnostic, client = execute(scope_keep=False)
    assert not rows
    assert client.phases == ["PRIMARY", "SCOPE"]
    assert diagnostic["primary_diagnostic"]["decision"] == "ACCEPTED"
    assert diagnostic["model_scope_verification_keep"] is False
    assert diagnostic["decision"] == "SCOPE_REJECTED"
    assert "evidence_repair" not in rows.audit


def test_scope_unit_rejection_is_not_overridden_by_primary_engineering_code():
    rows, diagnostic, client = execute(design_unit="INTERNAL_COMPONENT")
    assert not rows
    assert client.phases == ["PRIMARY", "SCOPE"]
    assert diagnostic["primary_diagnostic"]["decision"] == "ACCEPTED"
    assert diagnostic["model_scope_verification_keep"] is True
    assert diagnostic["scope_host_reason"] == "DESIGN_UNIT_OUT_OF_SCOPE"
    assert diagnostic["decision"] == "SCOPE_REJECTED"


def test_below_threshold_code_does_not_reach_scope_even_with_real_delivery():
    rows, diagnostic, client = execute(focus_scope="ADJACENT")
    assert not rows
    assert client.phases == ["PRIMARY"]
    assert diagnostic["decision"] == "BELOW_THRESHOLD"
    assert diagnostic["relevance_grade"] == 1
    assert diagnostic["task_grounded"] is True
    assert diagnostic["scope_verification_status"] == "NOT_REQUIRED"


def test_scope_cannot_borrow_neighbor_construction_after_primary_admission():
    rows, diagnostic, client = execute(row=candidate(before="实现一个订单服务。"),
                                       construction_reference="s0b0")
    assert not rows
    assert client.phases == ["PRIMARY", "SCOPE"]
    assert diagnostic["primary_diagnostic"]["decision"] == "ACCEPTED"
    assert diagnostic["model_scope_verification_keep"] is True
    assert diagnostic["design_unit_grounded"] is False
    assert diagnostic["scope_verification_keep"] is False


@pytest.mark.parametrize("query", ["工程设计题", "独立系统设计题"])
def test_primary_code_cannot_extract_construction_from_an_explanation(query):
    row = candidate("代码题：介绍设计一个通知服务的原理。", canonical="设计一个通知服务")
    rows, diagnostic, client = execute(query, row=row, task_reference="q0", construction_reference="q0")
    assert not rows
    assert client.phases == ["PRIMARY"]
    assert diagnostic["decision"] == "TASK_REJECTED"
    assert diagnostic["scope_verification_status"] == "NOT_REQUIRED"


@pytest.mark.parametrize("raw", [
    "解释已有原理后设计一个通知服务。",
    "代码题：解释已有原理后设计一个通知服务。",
])
def test_primary_code_preserves_a_new_delivery_after_an_explanation(raw):
    rows, diagnostic, client = execute(row=candidate(raw, canonical="设计一个通知服务"),
                                       task_reference="q0", construction_reference="q0")
    assert len(rows) == 1
    assert client.phases == ["PRIMARY", "SCOPE"]
    assert diagnostic["primary_diagnostic"]["decision"] == "ACCEPTED"
    assert diagnostic["scope_verification_status"] == "CONFIRMED"
    assert diagnostic["scope_verification_keep"] is True


@pytest.mark.parametrize("unit", ["NEW_OR_REBUILT_SYSTEM", "COMPONENT_OR_WORKFLOW"])
def test_noncode_design_scope_rejects_explanation_around_canonical_construction(unit):
    rows, diagnostic, client = execute("工程场景设计题",
        row=candidate("介绍设计一个通知服务的原理。", canonical="设计一个通知服务"),
        candidate_task="DESIGN_TASK", task_reference="q0", construction_reference="q0", design_unit=unit)
    assert not rows
    assert client.phases == ["PRIMARY", "SCOPE"]
    assert diagnostic["primary_diagnostic"]["candidate_task"] == "DESIGN_TASK"
    assert diagnostic["primary_diagnostic"]["decision"] == "ACCEPTED"
    assert diagnostic["model_scope_verification_keep"] is True
    assert diagnostic["design_unit_grounded"] is False
    assert diagnostic["scope_verification_keep"] is False
    assert diagnostic["decision"] == "SCOPE_REJECTED"


@pytest.mark.parametrize("raw,canonical,unit", [
    ("解释已有原理后设计一个通知服务。", "设计一个通知服务", "NEW_OR_REBUILT_SYSTEM"),
    ("解释已有原理后实现一个审批工作流。", "实现一个审批工作流", "COMPONENT_OR_WORKFLOW"),
])
def test_noncode_design_scope_preserves_mixed_explanation_and_real_delivery(raw, canonical, unit):
    rows, diagnostic, client = execute(row=candidate(raw, canonical=canonical),
        candidate_task="DESIGN_TASK", task_reference="q0", construction_reference="q0", design_unit=unit)
    assert len(rows) == 1
    assert client.phases == ["PRIMARY", "SCOPE"]
    assert diagnostic["primary_diagnostic"]["candidate_task"] == "DESIGN_TASK"
    assert diagnostic["primary_diagnostic"]["decision"] == "ACCEPTED"
    assert diagnostic["scope_verification_status"] == "CONFIRMED"
    assert diagnostic["scope_verification_keep"] is True
    assert rows.audit["candidate_verification_status"] == "COMPLETE"
