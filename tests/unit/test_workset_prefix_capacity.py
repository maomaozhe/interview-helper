"""Prepared request capacity is independent of semantic labels and providers."""
from copy import deepcopy
import hashlib
import json

import pytest

from interview_intelligence.relevance_scope import compile_relevance_scope
from interview_intelligence.search.reranker import LLMReranker


class NoProvider:
    base_url = ""

    def __getattr__(self, name):
        raise AssertionError("capacity preparation touched provider: " + name)


def source(identity, text="设计一个资源预约功能，使用者选择时段并取消预约。"):
    return {"canonical_question_id": identity, "canonical_text": text,
        "source_context": [{"source_context_status": "VERIFIED", "original_question": text,
                            "original_question_complete": True}]}


def scope(*extra):
    return compile_relevance_scope(["列出所有工程系统设计题", "具体业务系统设计", *extra], candidate_mode=True)


def ranker():
    return LLMReranker(client=NoProvider(), model="not-called")


def prepared_pair(reranker, query, rows, constraints):
    return {"primary": reranker._primary_request(query, rows, relevance_scope=constraints),
            "scope": reranker._scope_request(query, rows, relevance_scope=constraints)}


def pair_upper(prepared):
    return sum(stage["token_upper_bound"] for stage in prepared.values())


def assert_request_identity(metadata, prepared):
    raw = json.dumps({"messages": prepared["messages"], "response_format": prepared["response_format"]},
        ensure_ascii=False, separators=(",", ":")).encode()
    assert metadata == {"prepared_sha256": hashlib.sha256(raw).hexdigest(),
        "input_token_upper_bound": len(raw), "token_upper_bound": len(raw)+6144,
        "output_token_limit": 6144}


def test_exact_boundary_selects_maximum_prefix_and_preserves_all_remaining_budget():
    reranker, constraints = ranker(), scope()
    rows = [source("third"), source("first"), source("second"), source("tail")]
    original = deepcopy(rows)
    chosen = prepared_pair(reranker, "具体业务系统设计", rows[:3], constraints)
    upper = pair_upper(chosen)
    assert pair_upper(prepared_pair(reranker, "具体业务系统设计", rows, constraints)) > upper
    result = reranker.select_workset_prefix("具体业务系统设计", rows, relevance_scope=constraints,
        max_tokens=upper, max_calls=5)
    assert result["version"] == "engineering_workset_adaptive_prefix_v1"
    assert result["status"] == "READY" and result["reason"] == "MAXIMUM_FITTING_PREFIX"
    assert result["offered_candidate_ids"] == ["third", "first", "second", "tail"]
    assert result["selected_candidate_ids"] == ["third", "first", "second"]
    assert result["required_calls"] == 2 and result["remaining_calls"] == 3
    assert result["required_tokens"] == upper and result["remaining_tokens"] == 0
    assert result["max_tokens"] == upper and result["max_calls"] == 5
    assert result["scope_hash"] == constraints["scope_hash"]
    for stage, prepared in chosen.items():
        assert_request_identity(result["prepared"][stage], prepared)
    assert rows == original


def test_one_token_less_reduces_prefix_and_keeps_budget_arithmetic_exact():
    reranker, constraints = ranker(), scope()
    rows = [source(str(index)) for index in range(3)]
    upper = pair_upper(prepared_pair(reranker, "具体业务系统设计", rows, constraints))
    result = reranker.select_workset_prefix("具体业务系统设计", rows, relevance_scope=constraints,
        max_tokens=upper-1, max_calls=2)
    assert result["selected_candidate_ids"] == ["0", "1"]
    assert result["remaining_calls"] == 0
    assert result["required_tokens"] + result["remaining_tokens"] == upper-1
    assert result["required_tokens"] == pair_upper(prepared_pair(reranker, "具体业务系统设计", rows[:2], constraints))


def test_twenty_source_ready_items_overflow_without_changing_text_or_options():
    reranker, constraints = ranker(), scope()
    text = "设计一个资源预约功能，" + "".join("状态"+str(index)+"需同步确认。" for index in range(12))
    assert len(text.encode()) <= 500
    rows = [source("item-"+str(index), text) for index in range(20)]
    unchanged = deepcopy(rows)
    whole = prepared_pair(reranker, "具体业务系统设计", rows, constraints)
    assert pair_upper(whole) > 96000
    result = reranker.select_workset_prefix("具体业务系统设计", rows, relevance_scope=constraints,
        max_tokens=96000, max_calls=6)
    size = len(result["selected_candidate_ids"])
    assert result["status"] == "READY" and 0 < size < 20
    assert result["selected_candidate_ids"] == [row["canonical_question_id"] for row in rows[:size]]
    chosen = prepared_pair(reranker, "具体业务系统设计", rows[:size], constraints)
    assert result["required_tokens"] == pair_upper(chosen) <= 96000
    assert pair_upper(prepared_pair(reranker, "具体业务系统设计", rows[:size+1], constraints)) > 96000
    for stage, prepared in chosen.items():
        assert_request_identity(result["prepared"][stage], prepared)
    assert rows == unchanged


@pytest.mark.parametrize("calls", [0, 1])
def test_fewer_than_two_calls_never_prepares_or_reserves_a_partial_review(monkeypatch, calls):
    reranker = ranker()
    def forbidden(*args, **kwargs):
        raise AssertionError("no complete initial review can fit")
    monkeypatch.setattr(reranker, "_primary_request", forbidden)
    monkeypatch.setattr(reranker, "_scope_request", forbidden)
    result = reranker.select_workset_prefix("具体业务系统设计", [source("one")], relevance_scope=scope(),
        max_tokens=96000, max_calls=calls)
    assert result["status"] == "NO_CAPACITY" and result["reason"] == "INSUFFICIENT_MODEL_CALLS"
    assert result["selected_candidate_ids"] == [] and result["prepared"] == {}
    assert result["required_calls"] == result["required_tokens"] == 0
    assert result["remaining_tokens"] == 96000 and result["remaining_calls"] == calls


def test_first_item_cannot_fit_is_not_skipped_for_a_smaller_tail_item():
    reranker, constraints = ranker(), scope()
    rows = [source("large", "设计资源预约功能，" + "使用者选择预约时段并查看状态。"*12), source("small")]
    minimum = pair_upper(prepared_pair(reranker, "具体业务系统设计", rows[:1], constraints))
    assert pair_upper(prepared_pair(reranker, "具体业务系统设计", rows[1:], constraints)) < minimum
    result = reranker.select_workset_prefix("具体业务系统设计", rows, relevance_scope=constraints,
        max_tokens=minimum-1, max_calls=6)
    assert result["status"] == "NO_CAPACITY" and result["reason"] == "FIRST_CANDIDATE_EXCEEDS_TOKEN_BUDGET"
    assert result["selected_candidate_ids"] == [] and result["prepared"] == {}
    assert result["remaining_tokens"] == minimum-1 and result["remaining_calls"] == 6


def test_empty_window_does_not_prepare_requests_and_returns_no_capacity():
    result = ranker().select_workset_prefix("具体业务系统设计", [], relevance_scope=scope(), max_tokens=0, max_calls=0)
    assert result["status"] == "NO_CAPACITY" and result["reason"] == "EMPTY_WINDOW"
    assert result["selected_candidate_ids"] == result["offered_candidate_ids"] == []
    assert result["prepared"] == {}


def test_constraints_are_bound_to_the_exact_initial_scope_request():
    reranker = ranker()
    constraints = scope("只要独立完整系统，排除AI")
    assert constraints["independent_only"] and constraints["excluded_domains"] == ["AI"]
    rows = [source("constraint", "设计一个独立的场地预约系统，使用者选择时段。")]
    query = "具体业务系统设计，独立完整且排除AI"
    result = reranker.select_workset_prefix(query, rows, relevance_scope=constraints, max_tokens=96000, max_calls=6)
    assert result["selected_candidate_ids"] == ["constraint"]
    actual = prepared_pair(reranker, query, rows, constraints)
    for stage, prepared in actual.items():
        assert_request_identity(result["prepared"][stage], prepared)
        assert json.loads(prepared["messages"][1]["content"])["relevance_scope"] == constraints
    ordinary = prepared_pair(reranker, query, rows, scope())
    assert result["prepared"]["scope"]["prepared_sha256"] != ordinary["scope"]["prepared_sha256"]


def test_prefix_search_does_not_assume_request_size_is_monotonic(monkeypatch):
    reranker = ranker()
    sizes = {1: 30000, 2: 50000, 3: 28000}
    prepared_sizes = []
    def prepare(query, rows, **kwargs):
        size = len(rows)
        prepared_sizes.append(size)
        upper = sizes[size]//2
        return {"prepared_sha256": str(size)*64, "input_token_upper_bound": upper-6144, "token_upper_bound": upper}
    monkeypatch.setattr(reranker, "_primary_request", prepare)
    monkeypatch.setattr(reranker, "_scope_request", prepare)
    result = reranker.select_workset_prefix("具体业务系统设计", [source(str(i)) for i in range(3)],
        relevance_scope=scope(), max_tokens=29000, max_calls=2)
    assert result["selected_candidate_ids"] == ["0", "1", "2"]
    assert result["required_tokens"] == 28000 and result["remaining_tokens"] == 1000
    assert prepared_sizes == [1, 1, 2, 2, 3, 3]


@pytest.mark.parametrize("tokens,calls", [(True, 6), (96000, False), (-1, 6), (96000, -1), (96000.0, 6), (96000, 2.5)])
def test_invalid_remaining_budget_is_rejected(tokens, calls):
    with pytest.raises(ValueError, match="WORKSET_PREFIX_BUDGET_INVALID"):
        ranker().select_workset_prefix("具体业务系统设计", [source("one")], relevance_scope=scope(),
            max_tokens=tokens, max_calls=calls)


@pytest.mark.parametrize("rows,reason", [
    ([source(str(i)) for i in range(21)], "WORKSET_PREFIX_WINDOW_INVALID"),
    ((source("one"),), "WORKSET_PREFIX_WINDOW_INVALID"),
    ([source("same"), source("same")], "WORKSET_PREFIX_CANDIDATE_IDS_INVALID"),
    ([source("")], "WORKSET_PREFIX_CANDIDATE_IDS_INVALID"),
    ([{}], "WORKSET_PREFIX_CANDIDATE_IDS_INVALID"),
    ([None], "WORKSET_PREFIX_CANDIDATE_IDS_INVALID"),
])
def test_invalid_offered_window_is_rejected(rows, reason):
    with pytest.raises(ValueError, match=reason):
        ranker().select_workset_prefix("具体业务系统设计", rows, relevance_scope=scope(), max_tokens=96000, max_calls=6)


def test_tampered_scope_is_not_used_for_capacity_or_request_identity():
    constraints = {**scope(), "independent_only": True}
    with pytest.raises(ValueError, match="RELEVANCE_SCOPE"):
        ranker().select_workset_prefix("具体业务系统设计", [source("one")], relevance_scope=constraints,
            max_tokens=96000, max_calls=6)
