from copy import deepcopy
import hashlib
import pytest

from interview_intelligence.search.evidence import _source_record, _assemble_contexts
from interview_intelligence.search.referents import (
    content_hash, validated_reference_view, validate_reference_claim, seal_source_record,
)


def candidate_frame(questions=None, *, current=None):
    questions = questions or ["设计预约服务，用户选择时段并完成预约。", "预约失败时怎样反馈？"]
    current = current or "你提到可异步提交，具体准备如何组合？"
    all_questions = [*questions, current]
    text = "\n".join(all_questions)
    digest = hashlib.sha256(text.encode()).hexdigest()
    revision = {"id": "revision", "raw_file_hash": digest}
    records, offset = {}, 0
    for index, raw in enumerate(all_questions):
        occurrence = {"id": f"o{index}", "interview_id": "interview", "question_order": index + 1,
            "raw_question": raw, "source_spans": [{"revision_id": "revision", "start_char": offset,
                "end_char": offset + len(raw), "quote": raw}]}
        records[occurrence["id"]] = _source_record(occurrence, revision, "build", text)
        offset += len(raw) + 1
    cid = "candidate"
    contexts = _assemble_contexts({cid: [records[f"o{len(questions)}"]]}, records, [], {"revision": text})[cid]
    return {"canonical_question_id": cid, "canonical_text": "不得用此改写补任务", **contexts}, records, text


def reseal_frame(candidate):
    source = candidate["source_context"][0]
    source["source_identity_hash"] = seal_source_record(source)["source_identity_hash"]
    frame = source["reference_frame"]
    frame["current_source_identity_hash"] = source["source_identity_hash"]
    frame["frame_hash"] = content_hash({key: value for key, value in frame.items() if key != "frame_hash"})


@pytest.mark.parametrize("questions", [
    ["设计预约服务，用户完成预约及取消。", "如何组织时段和预约状态？"],
    ["设计独立的任务队列服务。", "如何实现持久化和重试？"],
    ["设计客服Agent，用户查询订单并办理退款。", "如何安排工具调用？"],
    ["Design a booking application for customers.", "How should retries work?"],
])
def test_identity_frame_never_certifies_business_or_semantic_uniqueness(questions):
    candidate, _, _ = candidate_frame(questions)
    view = validated_reference_view(candidate)
    assert view["status"] == "VERIFIED" and view["semantic_resolution"] == "UNASSESSED"
    assert [item["original_question"] for item in view["slots"].values()] == list(reversed(questions))
    assert all(item["relation_provenance"] == "CANDIDATE_REFERENCE" for item in view["slots"].values())
    claim = validate_reference_claim(candidate, {"a": "s0o0", "r": ["s0r0", "s0r1"], "u": "UNIQUE"})
    assert claim["valid"] and claim["grounded"] and claim["model_unique_claim"]
    assert claim["semantic_resolution"] == "MODEL_ASSERTED_UNIQUE"
    assert candidate["source_context"][0]["original_question"] == "你提到可异步提交，具体准备如何组合？"
    assert "source_context_status" in claim["slots"]["s0r0"]
    assert "context_before" not in claim["slots"]["s0r0"]


@pytest.mark.parametrize("claim", [None, {}, {"a": "s0o0", "r": ["s0r0"], "u": "UNKNOWN"},
    {"a": "s0o0", "r": ["s0r0"], "u": "AMBIGUOUS"},
    {"a": "s0o0", "r": ["s0r1"], "u": "UNIQUE"},
    {"a": "s0o0", "r": ["s0r0", "s0r0"], "u": "UNIQUE"},
    {"a": "s1o0", "r": ["s0r0"], "u": "UNIQUE"},
    {"a": "q0", "r": ["s0r0"], "u": "UNIQUE"},
    {"a": "s0b0", "r": ["s0r0"], "u": "UNIQUE"},
    {"a": "s0o999", "r": ["s0r0"], "u": "UNIQUE"},
    {"a": "s0o0", "r": [["s0r0"]], "u": "UNIQUE"},
    {"a": "s0o0", "r": ["s0r0"], "u": "UNIQUE", "extra": True},
])
def test_reference_claim_requires_current_anchor_and_unique_contiguous_prefix(claim):
    candidate, _, _ = candidate_frame()
    actual = validate_reference_claim(candidate, claim)
    assert actual["valid"] is False and actual["slots"] == {}


def test_self_contained_current_cannot_borrow_previous_business_goal():
    candidate, _, _ = candidate_frame(current="如何设计独立缓存服务？")
    assert "reference_frame" not in candidate["source_context"][0]
    assert not validate_reference_claim(candidate, {"a": "s0o0", "r": ["s0r0"], "u": "UNIQUE"})["valid"]


@pytest.mark.parametrize("field,value", [("revision_id", "foreign"), ("build_id", "inactive"),
    ("source_interview_id", "foreign"), ("source_sha256", "0" * 64),
    ("original_question_complete", False), ("source_context_status", "UNAVAILABLE"),
    ("source_active", False), ("question_order", 999)])
def test_source_and_same_interview_order_controls_survive_frame_resigning(field, value):
    candidate, _, _ = candidate_frame()
    source = candidate["source_context"][0]
    antecedent = source["reference_frame"]["antecedents"][0]
    antecedent[field] = value
    antecedent["source_identity_hash"] = seal_source_record(antecedent)["source_identity_hash"]
    reseal_frame(candidate)
    assert validated_reference_view(candidate)["status"] == "UNRESOLVED"


def test_frame_hash_and_contiguous_sequence_are_both_checked():
    candidate, _, _ = candidate_frame(["旧业务目标", "中间题", "最近题"])
    candidate["source_context"][0]["reference_frame"]["antecedents"][0]["original_question"] = "篡改"
    assert validated_reference_view(candidate)["reason"] == "INVALID_FRAME_HASH"
    candidate, _, _ = candidate_frame(["旧业务目标", "中间题", "最近题"])
    frame = candidate["source_context"][0]["reference_frame"]
    del frame["antecedents"][1]
    frame["antecedents"][1]["slot"] = "s0r1"
    reseal_frame(candidate)
    assert validated_reference_view(candidate)["reason"] == "NONCONTIGUOUS_SOURCE_FRAME"


def test_full_nearest_ancestor_exceeding_budget_blocks_older_short_business_question():
    candidate, _, _ = candidate_frame(["业务目标", "长" * 200])
    frame = candidate["source_context"][0]["reference_frame"]
    assert frame["status"] == "UNRESOLVED" and frame["reason"] == "REFERENCE_BYTE_BUDGET"
    assert frame["antecedents"] == []


def test_no_partial_current_or_antecedent_and_total_utf8_budget():
    candidate, _, _ = candidate_frame(["旧题" * 50, "近题" * 40], current="你提到" + "长" * 160)
    source = candidate["source_context"][0]
    assert source["original_question_complete"] and source["reference_frame"]["antecedents"] == []
    candidate, _, _ = candidate_frame(["旧题"], current="你提到" + "长" * 180)
    source = candidate["source_context"][0]
    assert source["original_question_complete"] is False
    assert source["reference_frame"]["status"] == "UNRESOLVED"


def test_source_edges_are_independent_facts_and_invalid_spans_never_fall_back():
    candidate, records, text = candidate_frame()
    edge = {"id": "edge", "relation_type": "OBSERVED_FOLLOWUP", "provenance": "SOURCE",
        "source_occurrence_id": "o1", "target_occurrence_id": "o2", "source_interview_id": "interview",
        "evidence_spans": deepcopy(records["o2"]["source_spans"])}
    actual = _assemble_contexts({"candidate": [records["o2"]]}, records, [edge], {"revision": text})["candidate"]
    assert actual["source_context"][0]["reference_frame"]["antecedents"][0]["relation_provenance"] == "SOURCE"
    edge["evidence_spans"][0]["quote"] = "伪造"
    invalid = _assemble_contexts({"candidate": [records["o2"]]}, records, [edge], {"revision": text})["candidate"]
    assert invalid["source_context"][0]["reference_frame"]["reason"] == "INVALID_RELATION_SPAN"


@pytest.mark.parametrize("fault", ["multiple", "cycle", "foreign", "hop"])
def test_ambiguous_foreign_cyclic_or_out_of_budget_edges_remain_unresolved(fault):
    candidate, records, text = candidate_frame(["q" + str(i) for i in range(6)])
    edge = {"id": "edge", "relation_type": "OBSERVED_FOLLOWUP", "provenance": "SOURCE",
        "source_occurrence_id": "o5", "target_occurrence_id": "o6", "source_interview_id": "interview",
        "evidence_spans": deepcopy(records["o6"]["source_spans"])}
    edges = [edge]
    if fault == "multiple":
        edges.append({**deepcopy(edge), "id": "second", "source_occurrence_id": "o4"})
    elif fault == "cycle":
        edge["source_occurrence_id"] = "o6"
    elif fault == "foreign":
        edge["source_interview_id"] = "other"
    else:
        edge["source_occurrence_id"] = "o0"
    result = _assemble_contexts({"candidate": [records["o6"]]}, records, edges, {"revision": text})["candidate"]
    assert result["source_context"][0]["reference_frame"]["status"] == "UNRESOLVED"
