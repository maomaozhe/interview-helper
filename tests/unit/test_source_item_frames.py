from copy import deepcopy
import hashlib
import pytest

from interview_intelligence.search.evidence import _assemble_contexts, _source_record
from interview_intelligence.search.referents import content_hash, validated_reference_view, validate_reference_claim


def item_candidate(qualifiers, *, raw="如何设计这个服务？", prefix="1. ", after="2. 另一道独立题？"):
    text = prefix + raw + "\n" + "\n".join(qualifiers) + "\n" + after
    start = len(prefix)
    occurrence = {"id": "current", "interview_id": "interview", "question_order": 1,
        "raw_question": raw, "source_spans": [{"revision_id": "revision", "start_char": start,
            "end_char": start + len(raw), "quote": raw}]}
    revision = {"id": "revision", "raw_file_hash": hashlib.sha256(text.encode()).hexdigest()}
    record = _source_record(occurrence, revision, "build", text)
    prepared = _assemble_contexts({"cid": [record]}, {record["occurrence_id"]: record}, [], {"revision": text})["cid"]
    return {"canonical_question_id": "cid", **prepared}, text


@pytest.mark.parametrize("qualifiers", [
    ["用户预约、取消与变更时段的流程和状态。", "预约冲突及失败后的恢复方式。"],
    ["客服Agent支持查询订单、申请退款和查询物流。"],
    ["组件包含接入层、调度器和执行器。"],
    ["  - 用户提交申请后如何查看办理进度？", "  - 失败后允许重试并防止重复提交。"],
])
def test_numbered_item_source_position_requires_independent_claim(qualifiers):
    candidate, _ = item_candidate(qualifiers)
    source = candidate["source_context"][0]
    assert source["original_question"] == "如何设计这个服务？"
    view = validated_reference_view(candidate)
    assert view["status"] == "VERIFIED" and view["semantic_resolution"] == "UNASSESSED"
    assert [value["qualifier_text"] for value in view["slots"].values()] == qualifiers
    assert all("original_question" not in value for value in view["slots"].values())
    assert not validate_reference_claim(candidate, None)["valid"]
    claim = validate_reference_claim(candidate, {"a": "s0o0", "r": list(view["slots"]), "u": "UNIQUE"})
    assert claim["valid"] and claim["frame_kind"] == "SOURCE_ITEM_QUALIFIER"
    assert claim["semantic_resolution"] == "MODEL_ASSERTED_UNIQUE"
    assert sum(len(source[key].encode()) for key in ("original_question", "context_before", "context_after")) + (
        source["source_item_frame"]["frame_text_bytes"]) <= 500


@pytest.mark.parametrize("qualifiers", [
    ["另外一个系统如何设计？", "不能跨过独立问借这句业务描述。"],
    ["", "不能跨过段落借业务目标。"],
    ["2.下一道题缺空格", "不能跨过不明确编号。"],
    ["2. 下一道题", "不能跨过下一编号。"],
    ["# 下一个章节", "不能跨过章节。"],
])
def test_independent_question_peer_blank_or_ambiguous_structure_stops_without_skipping(qualifiers):
    candidate, _ = item_candidate(qualifiers)
    assert "source_item_frame" not in candidate["source_context"][0]
    assert not validate_reference_claim(candidate, {"a": "s0o0", "r": ["s0i0"], "u": "UNIQUE"})["valid"]


def test_unindented_component_requirement_stops_before_a_separate_following_ask():
    candidate, _ = item_candidate(["划分为入口、工具服务、执行器。", "安全隔离怎么做？", "用户办理业务的流程。"])
    frame = candidate["source_context"][0]["source_item_frame"]
    assert [item["qualifier_text"] for item in frame["qualifiers"]] == ["划分为入口、工具服务、执行器。"]
    assert frame["source_item_structure"]["boundary_kind"] == "INDEPENDENT_FOLLOWING_QUESTION"


def test_plain_context_after_is_never_promoted_to_qualifier():
    candidate, _ = item_candidate(["用户预约和支付。"], prefix="")
    assert "source_item_frame" not in candidate["source_context"][0]


def test_item_and_reference_slots_cannot_be_mixed_or_cherry_picked():
    candidate, _ = item_candidate(["用户预约和支付。", "失败恢复。"])
    for slots in (["s0i1"], ["s0i0", "s0r0"], ["s0i0", "s0i0"]):
        assert not validate_reference_claim(candidate, {"a": "s0o0", "r": slots, "u": "UNIQUE"})["valid"]


def test_item_qualifier_rawhash_span_or_text_tampering_cannot_be_reused():
    candidate, _ = item_candidate(["用户预约和支付。"])
    candidate["source_context"][0]["source_item_frame"]["qualifiers"][0]["qualifier_text"] = "偷换"
    assert validated_reference_view(candidate)["status"] == "UNRESOLVED"
    candidate, _ = item_candidate(["用户预约和支付。"])
    frame = candidate["source_context"][0]["source_item_frame"]
    frame["qualifiers"][0]["source_sha256"] = "f" * 64
    frame["frame_hash"] = content_hash({key: value for key, value in frame.items() if key != "frame_hash"})
    assert validated_reference_view(candidate)["status"] == "UNRESOLVED"


def test_item_budget_preserves_current_and_never_clips_or_skips_a_qualifier():
    candidate, _ = item_candidate(["长" * 200, "不应越过超长前项拿业务目标。"])
    source = candidate["source_context"][0]
    assert source["original_question_complete"] and "source_item_frame" not in source
    candidate, _ = item_candidate(["短要求。", "长" * 200, "不应选这句。"])
    source = candidate["source_context"][0]
    assert [row["qualifier_text"] for row in source["source_item_frame"]["qualifiers"]] == ["短要求。"]
    assert source["source_item_frame"]["source_item_structure"]["boundary_kind"] == "ITEM_BYTE_BUDGET"

@pytest.mark.parametrize('line', [
    '另一个系统如何设计','为什么要使用缓存','你会怎么实现该功能','是否支持取消',
    '设计另一个服务','请解释这个原理','How would you design another service',
    'Why use caching','What are the tradeoffs','Design another service','Please explain the protocol',
    '另一个系统如何设计？','Why use caching?','用户提交申请后如何查看办理进度？',
])
@pytest.mark.parametrize('prefix',['','- '])
def test_independent_peer_ask_stops_with_or_without_punctuation_or_bare_bullet(line,prefix):
    candidate,_=item_candidate([prefix+line,'后续用户登记并办理申请。'])
    assert 'source_item_frame' not in candidate['source_context'][0]
    assert not validate_reference_claim(candidate,{'a':'s0o0','r':['s0i0'],'u':'UNIQUE'})['valid']


@pytest.mark.parametrize('line', ['另一个系统如何设计','为什么要使用缓存','How would you design another service',
    'Design another service','用户提交申请后如何查看办理进度？'])
@pytest.mark.parametrize('prefix',['  ','  - '])
def test_formal_deeper_item_preserves_question_as_unassessed_candidate_context(line,prefix):
    candidate,_=item_candidate([prefix+line])
    view=validated_reference_view(candidate)
    assert view['status']=='VERIFIED' and view['semantic_resolution']=='UNASSESSED'
    assert view['slots']['s0i0']['qualifier_text']==prefix+line
    assert validate_reference_claim(candidate,None)['valid'] is False


@pytest.mark.parametrize('line',['  另一个系统如何设计','  - 为什么要使用缓存',
    '  Explain the protocol',' \t用户办理业务。','用户办理业务。'])
def test_same_level_questions_or_unclear_dedented_structure_cannot_join_a_nested_parent(line):
    candidate,_=item_candidate([line],prefix='  1. ')
    assert 'source_item_frame' not in candidate['source_context'][0]


@pytest.mark.parametrize('line',[
    '要求用户登记申请后能够查询办理状态。','必须支持预约确认和取消。',
    '组件划分为接入层、调度器和执行器。','设计目标是支持登记、办理及结果查询。',
    'The service must support booking and cancellation.',
    'Design goals are booking confirmation and cancellation.',
])
@pytest.mark.parametrize('prefix',['','  ','- '])
def test_declarative_requirements_remain_candidate_context_not_certified_ownership(line,prefix):
    candidate,_=item_candidate([prefix+line])
    view=validated_reference_view(candidate)
    assert view['status']=='VERIFIED' and view['semantic_resolution']=='UNASSESSED'
    assert list(view['slots'])==['s0i0']
    assert validate_reference_claim(candidate,None)['valid'] is False
