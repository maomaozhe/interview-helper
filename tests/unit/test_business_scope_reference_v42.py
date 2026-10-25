"""Explicit reference frames supplement targets, never the current task."""
from copy import deepcopy
import hashlib
import json

import pytest

from test_business_scope_verification import Provider, QUERY, scope
from interview_intelligence.search.business_scope import business_candidate_payload
from interview_intelligence.search.citations import resolve_reference_option
from interview_intelligence.search.referents import (
    build_reference_frame, seal_source_record, validate_reference_claim, validated_reference_view,
)
from interview_intelligence.search.reranker import LLMReranker
from interview_intelligence.search.evidence import _source_record, _assemble_contexts


def source_frame(current="你提到该平台，具体准备如何组合这些服务？", *, technical=False, ai=False):
    before = ("设计一个通用传输缓冲组件，支持并发投递与超时重试。" if technical else
              "设计一个AI阅读预约助手，读者预约阅览时段并收到受理结果。" if ai else
              "设计一个阅读预约平台，读者预约阅览时段并收到受理结果。")
    raw = before + "\n" + current
    digest = hashlib.sha256(raw.encode()).hexdigest()
    common = {"revision_id": "rev-neutral", "source_interview_id": "interview-neutral", "build_id": "build-neutral",
        "source_sha256": digest, "decoded_text_sha256": digest, "original_question_complete": True,
        "source_context_status": "VERIFIED", "source_active": True}
    previous = seal_source_record({**common, "occurrence_id": "previous", "question_order": 2,
        "original_question": before, "source_reference_preceding_ids": [],
        "source_spans": [{"start_char": 0, "end_char": len(before), "quote": before}]})
    record = seal_source_record({**common, "occurrence_id": "current", "question_order": 3,
        "original_question": current, "source_reference_preceding_ids": ["previous"],
        "source_spans": [{"start_char": len(before) + 1, "end_char": len(raw), "quote": current}]})
    record["reference_frame"] = build_reference_frame(record, [previous], text_budget=500-len(current.encode()))
    candidate = {"canonical_question_id": "neutral-reference", "canonical_text": current, "source_context_required": True,
        "source_context": [record], "source_context_coverage": {"qualified_occurrence_count": 1,
            "qualified_occurrence_ids": ["current"], "complete": True}}
    assert validated_reference_view(candidate)["status"] == "VERIFIED"
    return candidate


def claim(u="UNIQUE"):
    return {"a": "s0o0", "r": ["s0r0"], "u": u}


def provider(*, technical=False, ai=False, overrides=None):
    return Provider({"你提到": "AI_INFRASTRUCTURE" if technical and ai else "FOUNDATION_COMPONENT" if technical else
                     "AI_APPLICATION" if ai else "BUSINESS_WORKFLOW"}, scope_overrides={
        "unit_evidence": "s0r0o0", "business_goal_evidence": "s0r0o1", "business_activity_evidence": "" if technical else "s0r0o1",
        **({"technical_goal_evidence": "s0r0o1"} if technical else {}), "construction_evidence": "s0o1",
        "scope_task_evidence": "s0o1", "scope_candidate_task": "D", "source_reference_claim": claim(),
        "scope_query_relation": None if technical else {"o": "INSTANCE", "f": "SAME", "e": "s0r0o0", "h": "s0r0o1", "g": 3},
        **(overrides or {})})


def run(fixture, source, host=None):
    return LLMReranker(client=fixture, model="fixture").rerank(QUERY, [source], relevance_scope=host or scope())


@pytest.mark.parametrize("ai", [False, True])
def test_reference_bound_business_target_preserves_current_task_and_distinct_origins(ai):
    source, fixture = source_frame(ai=ai), provider(ai=ai)
    result = run(fixture, source)
    assert result[0]["relevance_scope_status"] == "CONFIRMED" and len(fixture.requests) == 2
    row = result.audit["candidates"][0]
    assert row["scope_task_evidence"] == "具体准备如何组合这些服务？"
    assert row["business_evidence_origins"]["unit_evidence"] == ("reference_question", 0, "s0r0")
    assert row["business_evidence_origins"]["scope_task_evidence"] == ("original_question", 0)
    assert row["design_assessment"]["host_reason"] == "REFERENCE_BOUND_CURRENT_DELIVERY"
    assert row["source_reference_assessment"]["semantic_resolution"] == "MODEL_ASSERTED_UNIQUE"
    assert row["business_assessment"]["semantic_certification"] is False
    primary = json.loads(fixture.requests[0]["messages"][1]["content"])
    secondary = json.loads(fixture.requests[1]["messages"][1]["content"])
    assert "reference_frame" not in primary["candidates"][0]["source_context"][0]
    frame = secondary["candidates"][0]["source_context"][0]["reference_frame"]
    assert frame["semantic_resolution"] == "UNASSESSED"
    assert "s0r0o0" in frame["antecedents"][0]["original_question"]
    serialized = json.dumps(secondary, ensure_ascii=False)
    assert "source_spans" not in serialized and "source_identity_hash" not in serialized
    assert "source_sha256" not in serialized and "reference_evidence_options" not in serialized


def test_explicit_referential_technical_delivery_can_be_independently_excluded():
    source = source_frame("你提到该组件，具体准备如何实现？", technical=True)
    fixture = provider(technical=True)
    result = run(fixture, source)
    assert not result and len(fixture.requests) == 2
    row = result.audit["candidates"][0]
    assert row["scope_adjudication_status"] == "VERIFIED" and row["business_assessment"]["outcome"] == "NEGATIVE"
    assert row["source_verified_negative_occurrence_ids"] == ["current"]


@pytest.mark.parametrize("text,role,task", [
    ("你提到该平台，介绍去年如何实现的。", "HISTORICAL_REPORT", "H"),
    ("你提到该平台，解释其状态转换原理。", "EXPLANATION", "K"),
])
def test_current_history_or_knowledge_may_be_negative_without_borrowing_task_from_antecedent(text, role, task):
    fixture = provider(overrides={"delivery_role": role, "scope_candidate_task": task, "negative_task_evidence": "s0o1",
        "business_activity_evidence": "", "rejection_basis": "TASK_TYPE", "keep": False,
        "scope_query_relation": None})
    result = run(fixture, source_frame(text))
    assert not result
    assert result.audit["candidates"][0]["business_assessment"]["outcome"] == "NEGATIVE"


@pytest.mark.parametrize("overrides", [
    {"source_reference_claim": None}, {"source_reference_claim": claim("AMBIGUOUS")},
    {"source_reference_claim": {"a": "s0o0", "r": ["s0r1"], "u": "UNIQUE"}},
    {"scope_task_evidence": "s0r0o0"}, {"construction_evidence": "s0r0o0"},
    {"business_activity_evidence": "s0r9o1"}, {"scope_query_relation": None},
])
def test_unresolved_or_foreign_reference_proofs_stay_pending(overrides):
    fixture = provider(overrides=overrides)
    result = run(fixture, source_frame())
    assert result[0]["relevance_scope_status"] == "PENDING" and len(fixture.requests) == 3


@pytest.mark.parametrize("current", [
    "你提到该平台，介绍去年如何组合这些服务的。",
    "你提到该平台，解释这些服务的组合原理。",
    "你提到该平台，只是提及这个对象。",
    "你提到该平台，不要求你设计或实现它。",
])
def test_antecedent_delivery_cannot_turn_a_non_current_task_into_a_design(current):
    result = run(provider(), source_frame(current))
    assert result[0]["relevance_scope_status"] == "PENDING"


@pytest.mark.parametrize("bad", ["source", "coverage", "ambiguous"])
def test_reference_negative_with_missing_source_coverage_or_ambiguous_target_cannot_remove_candidate(bad):
    source = source_frame("你提到该组件，具体准备如何实现？", technical=True)
    fixture = provider(technical=True, overrides={"source_reference_claim": claim("AMBIGUOUS")} if bad == "ambiguous" else None)
    if bad == "source":
        source["source_context"][0]["original_question_complete"] = False
    if bad == "coverage":
        source["source_context_coverage"]["complete"] = False
    result = run(fixture, source)
    assert result[0]["relevance_scope_status"] == "PENDING"


def test_reference_query_proof_keeps_explicit_independence_and_ai_exclusion_conditions():
    for host in [scope(["具体业务系统设计，只要独立系统"]), scope(["具体业务系统设计，不要AI应用"])]:
        result = run(provider(ai=True), source_frame(ai=True), host)
        assert result[0]["relevance_scope_status"] == "PENDING"


def test_reference_range_stays_in_one_selected_original_and_rejects_cross_slot_or_unknown_endpoints():
    source = source_frame()
    payload = business_candidate_payload([source], include_reference_frames=True)[0]
    valid = validate_reference_claim(source, claim())
    assert resolve_reference_option("s0r0o0:s0r0o1", payload, valid)["text"].startswith("设计一个阅读预约平台")
    for reference in ["s0r0o0:s0r1o1", "s0r0o0:s0r0o999999999", "s1r0o0", "s0r0o1:s0r0o0"]:
        assert resolve_reference_option(reference, payload, valid) is None


def item_source_frame(current="阅读预约平台架构怎么设计？", qualifier="要求读者预约阅览时段并收到受理结果。"):
    raw = f"1. {current}\n{qualifier}\n2. 解释不相关概念。\n"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    occurrence = {"id": "item-current", "interview_id": "item-interview", "question_order": 1,
        "raw_question": current, "source_spans": [{"revision_id": "item-rev", "start_char": 3,
            "end_char": 3 + len(current), "quote": current}]}
    record = _source_record(occurrence, {"id": "item-rev", "raw_file_hash": digest}, "item-build", raw)
    context = _assemble_contexts({"item-case": [record]}, {"item-current": record}, [], {"item-rev": raw})["item-case"]
    source = {"canonical_question_id": "item-case", "canonical_text": current, **context,
        "source_context_coverage": {"qualified_occurrence_count": 1, "qualified_occurrence_ids": ["item-current"], "complete": True}}
    assert validated_reference_view(source)["status"] == "VERIFIED"
    return source


def item_provider(unit="BUSINESS_WORKFLOW", overrides=None):
    business = unit in {"BUSINESS_WORKFLOW", "BUSINESS_APPLICATION", "AI_APPLICATION"}
    return Provider({"架构怎么设计": unit}, scope_overrides={
        "unit_evidence": "s0o0", "business_goal_evidence": "s0i0o0", "business_activity_evidence": "s0i0o0" if business else "",
        **({"technical_goal_evidence": "s0i0o0"} if not business else {}),
        "scope_task_evidence": "s0o0", "construction_evidence": "s0o0",
        "source_reference_claim": {"a": "s0o0", "r": ["s0i0"], "u": "UNIQUE"},
        "scope_query_relation": {"o": "INSTANCE", "f": "SAME", "e": "s0o0", "h": "s0i0o0", "g": 3} if business else None,
        **(overrides or {})})


@pytest.mark.parametrize("unit,current", [
    ("BUSINESS_WORKFLOW", "阅读预约平台架构怎么设计？"),
    ("AI_APPLICATION", "辅助审批Agent架构怎么设计？"),
])
def test_formal_item_qualifier_supplies_activity_with_a_distinct_source_kind(unit, current):
    fixture = item_provider(unit)
    result = run(fixture, item_source_frame(current))
    assert result[0]["relevance_scope_status"] == "CONFIRMED" and len(fixture.requests) == 2
    row = result.audit["candidates"][0]
    assert row["business_evidence_origins"]["business_activity_evidence"] == ("source_item_qualifier", 0, "s0i0")
    assert row["scope_task_evidence"] == current and row["source_reference_assessment"]["frame_kind"] == "SOURCE_ITEM_QUALIFIER"
    body = json.loads(fixture.requests[1]["messages"][1]["content"])
    frame = body["candidates"][0]["source_context"][0]["source_item_frame"]
    assert "s0i0o0" in frame["qualifiers"][0]["qualifier_text"]
    assert "reference_frame" not in body["candidates"][0]["source_context"][0]


def test_formal_item_technical_goal_can_support_independent_negative_adjudication():
    fixture = item_provider("AI_INFRASTRUCTURE")
    result = run(fixture, item_source_frame("模型执行服务架构怎么设计？", "要求支持动态批处理、显存配额和跨节点恢复。"))
    assert result == [] and len(fixture.requests) == 2
    assert result.audit["candidates"][0]["scope_adjudication_status"] == "VERIFIED"


@pytest.mark.parametrize("overrides", [
    {"source_reference_claim": None},
    {"source_reference_claim": {"a": "s0o0", "r": ["s0i0"], "u": "UNKNOWN"}},
    {"source_reference_claim": {"a": "s0o0", "r": ["s0i0", "s0r0"], "u": "UNIQUE"}},
    {"scope_task_evidence": "s0i0o0"}, {"construction_evidence": "s0i0o0"},
    {"scope_query_relation": {"o": "COMPONENT", "f": "BROAD", "e": "s0o0", "h": "s0i0o0", "g": 1}},
])
def test_formal_item_claim_does_not_bypass_current_task_uniqueness_or_query_axes(overrides):
    result = run(item_provider(overrides=overrides), item_source_frame())
    assert result[0]["relevance_scope_status"] == "PENDING"


@pytest.mark.parametrize("keep", [False, True])
def test_valid_mixed_reference_boundary_remains_stable_pending_without_forcing_binary_repair(keep):
    fixture = provider(overrides={"semantic_unit": "MIXED_OR_AMBIGUOUS", "delivery_role": "TECHNICAL_CAPABILITY",
        "technical_goal_evidence": "s0r0o1", "business_activity_evidence": "", "keep": keep,
        "rejection_basis": "UNKNOWN", "scope_query_relation": None})
    result = run(fixture, source_frame())
    assert result[0]["relevance_scope_status"] == "PENDING" and len(fixture.requests) == 2
    assert result.audit["candidates"][0]["scope_uncertainty_status"] == "MIXED_BOUNDARY_UNRESOLVED"


def test_history_prefix_cannot_hard_exclude_a_current_referential_design_in_the_same_original():
    source = source_frame("你提到该平台，介绍去年如何实现的；如果现在由你重新设计它，你会如何组合这些服务？")
    fixture = provider(overrides={"delivery_role": "HISTORICAL_REPORT", "scope_candidate_task": "H",
        "negative_task_evidence": "s0o1", "scope_task_evidence": "s0o1", "business_activity_evidence": "",
        "rejection_basis": "TASK_TYPE", "keep": False, "scope_query_relation": None})
    result = run(fixture, source)
    assert result[0]["relevance_scope_status"] == "PENDING"
    assert result.audit["candidates"][0]["business_assessment"]["outcome"] == "INSUFFICIENT"


def test_current_referential_redesign_can_be_proved_without_relabeling_the_historical_prefix():
    source = source_frame("你提到该平台，介绍去年如何实现的；如果现在由你重新设计它，你会如何组合这些服务？")
    fixture = provider(overrides={"scope_task_evidence": "s0o2:s0o3", "construction_evidence": "s0o2:s0o3"})
    result = run(fixture, source)
    assert result[0]["relevance_scope_status"] == "CONFIRMED"
    assert result.audit["candidates"][0]["scope_task_evidence"].startswith("如果现在由你重新设计")


def test_explicit_current_independent_rebuild_keeps_its_existing_source_proof_policy():
    source = source_frame("你提到该平台，如果现在从头设计独立阅读预约平台，你会如何实现？")
    fixture = provider(overrides={"scope_task_evidence": "s0o1:s0o2", "construction_evidence": "s0o1:s0o2"})
    result = run(fixture, source, scope(["具体业务系统设计，只要独立系统"]))
    assert result[0]["relevance_scope_status"] == "CONFIRMED"


def test_unscoped_prompts_never_receive_new_raw_frames_or_host_identity_metadata():
    ranker = LLMReranker(client=object(), model="no-provider")
    source = source_frame()
    source["source_context"][0].update(context_before="可显示的原有前文。", context_after="可显示的原有后文。")
    primary = ranker._primary_request("找系统相关题", [source])
    secondary = ranker._scope_request("找系统相关题", [source])
    for prepared in (primary, secondary):
        body = json.loads(prepared["messages"][1]["content"])
        context = body["candidates"][0]["source_context"][0]
        assert set(context) == {"original_question", "original_question_complete", "source_context_status",
                                "context_before", "context_after"}
        assert "source_sha256" not in json.dumps(body) and "reference_frame" not in json.dumps(body)
    assert source["source_context"][0]["reference_frame"]["status"] == "VERIFIED"
