"""Bounded source-ID recovery: malformed evidence is not a negative label."""
import json
import time
from types import SimpleNamespace

import pytest

from interview_intelligence.providers.budget import CallBudget
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.providers.runtime import RequestLimits, current_limits
from interview_intelligence.search.reranker import LLMReranker


QUERY = "列出所有工程系统设计题"


def candidate(identity, text, original=None):
    return {"canonical_question_id": identity, "canonical_text": text,
        "retrieval_provenance": {"stage_ranks": {"lexical": 7}},
        "source_context": [{"source_context_status": "VERIFIED",
            "original_question": original or text, "original_question_complete": True}]}


class Provider:
    def __init__(self, first_error, *, repair_error=None, scope_error=None):
        self.first_error, self.repair_error, self.scope_error = first_error, repair_error, scope_error
        self.requests, self.primary_calls, self.scope_calls = [], 0, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        request = json.loads(kwargs["messages"][-1]["content"])
        primary = "rankings" in kwargs["response_format"]["json_schema"]["schema"]["properties"]
        if primary:
            self.primary_calls += 1
            error = self.first_error if self.primary_calls == 1 else self.repair_error
            if error == "transport":
                raise RuntimeError("fixture transport failed")
            rows = []
            for item in request["candidates"]:
                assert isinstance(item["question"], dict)
                row = {"candidate_id": item["id"], "candidate_task": "DESIGN_TASK",
                    "task_evidence": "s0o0", "object_evidence": "q0", "focus_evidence": "q0",
                    "object_scope": "INSTANCE", "focus_scope": "SAME"}
                if error == "long_task":
                    row["task_evidence"] = "".join(item["question"].values()) * 8
                elif error == "joined_focus":
                    parts = list(item["question"].values())
                    row["focus_evidence"] = parts[0] + parts[-1]
                elif error == "wrong_scope":
                    row["object_scope"] = "INVALID_SCOPE"
                elif error == "wrong_type":
                    row["task_evidence"] = {"text": "q0"}
                elif error == "negative_with_bad_quote":
                    row.update(candidate_task="KNOWLEDGE", task_evidence="不存在的引文")
                elif error == "unknown_id":
                    row["candidate_id"] = "unknown"
                elif error == "negative":
                    row.update(candidate_task="KNOWLEDGE", task_evidence="s0o0")
                rows.append(row)
            result = {"query_task": "INDEPENDENT_SYSTEM_DESIGN", "query_task_evidence": QUERY,
                "query_object": QUERY, "query_focus": QUERY, "query_object_evidence": QUERY,
                "query_focus_evidence": QUERY, "rankings": rows}
        else:
            self.scope_calls += 1
            rows = []
            for item in request["candidates"]:
                row = {"candidate_id": item["id"], "keep": True, "reason": "当前原问有明确建设交付要求",
                    "design_unit": "NEW_OR_REBUILT_SYSTEM", "construction_evidence": "s0o0",
                    "constraint_a": "", "constraint_b": ""}
                if self.scope_calls == 1 and self.scope_error == "long_quote":
                    row["construction_evidence"] = "原问证据" * 100
                elif self.scope_calls == 1 and self.scope_error == "wrong_scope":
                    row["design_unit"] = "INVALID_SCOPE"
                rows.append(row)
            result = {"verifications": rows}
        return SimpleNamespace(model="fixture", usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(result)))])


@pytest.mark.parametrize("text,error", [
    ("设计一个停车场管理系统，处理车位分配、支付和并发请求，要求保证操作幂等。", "long_task"),
    ("设计一个购票系统，十万人竞争一千个座位，支持合理分配规则，要求高可用。", "joined_focus"),
    ("设计一个多租户对象存储服务，支持版本读取、容量限制和灾难恢复，要求数据持久化。", "long_task"),
    ("设计一个遥测收集服务，接收多种设备上报，限制端到端延迟，支持重复上报去重。", "joined_focus"),
])
def test_source_id_repair_recovers_historical_failure_classes_for_unseen_domains(tmp_path, text, error):
    source = candidate("source-id", text)
    provider, logs = Provider(error), []
    ranker = LLMReranker(client=provider, model="fixture", on_call=logs.append,
        call_gate=ModelCallGate(tmp_path / "shared.lock", minimum_interval_seconds=0))
    rows = ranker.rerank(QUERY, [source])
    assert [row["canonical_question_id"] for row in rows] == ["source-id"]
    assert rows[0]["retrieval_provenance"] == source["retrieval_provenance"]
    assert provider.primary_calls == 2 and provider.scope_calls == 1
    assert [log["operation_type"] for log in logs] == ["RERANK", "RERANK_PRIMARY_EVIDENCE_REPAIR", "RERANK_VERIFY"]
    assert logs[1]["retry_count"] == 0
    assert rows.audit["candidate_verification_status"] == "COMPLETE"
    assert rows.audit["primary_initial_invalid_candidate_count"] == 1
    assert rows.audit["primary_initial_invalid_schema_count"] == int(error == "long_task")
    assert rows.audit["primary_initial_invalid_evidence_count"] == int(error == "joined_focus")
    assert rows.audit["primary_evidence_repair"]["recovered_count"] == 1
    assert rows.audit["primary_evidence_repair"]["unresolved_count"] == 0
    diagnostic = rows.audit["candidates"][0]["primary_diagnostic"]
    assert diagnostic["initial_diagnostic"]["verification_resolution"] == "UNRESOLVED"
    assert diagnostic["initial_diagnostic"]["decision"] == ("INVALID_SCHEMA" if error == "long_task" else "INVALID_EVIDENCE")
    assert diagnostic["task_evidence_reference"] == "s0o0"
    assert diagnostic["task_evidence"] in text
    assert diagnostic["evidence_repair_status"] == "RECOVERED"
    assert ranker.repair_primary_evidence(QUERY, [source], rows) is rows


@pytest.mark.parametrize("error,decision", [
    ("wrong_scope", "INVALID_SCHEMA"), ("wrong_type", "INVALID_SCHEMA"),
    ("negative_with_bad_quote", "INVALID_TASK_EVIDENCE"),
])
def test_non_evidence_schema_and_semantic_negatives_have_no_repair_authority(error, decision):
    provider = Provider(error)
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY,
        [candidate("a", "设计一个通知服务，支持多终端投递和断线恢复。")])
    assert rows == [] and len(provider.requests) == 1
    diagnostic = rows.audit["candidates"][0]
    assert diagnostic["decision"] == decision
    assert diagnostic["verification_resolution"] == "UNRESOLVED"
    assert diagnostic["evidence_repair_eligible"] is False
    assert rows.audit["candidate_verification_status"] == "PARTIAL"


@pytest.mark.parametrize("repair_error,status", [
    ("joined_focus", "UNRESOLVED"), ("transport", "FAILED"), ("unknown_id", "FAILED"),
])
def test_failed_repair_is_single_call_and_retains_initial_failure(repair_error, status):
    provider = Provider("joined_focus", repair_error=repair_error)
    ranker = LLMReranker(client=provider, model="fixture")
    source = candidate("a", "设计一个通知服务，支持多终端投递，要求断线恢复。")
    rows = ranker.rerank(QUERY, [source])
    assert rows == [] and len(provider.requests) == 2
    assert rows.audit["primary_evidence_repair"]["status"] == status
    assert rows.audit["primary_evidence_repair"]["unresolved_count"] == 1
    assert rows.audit["unresolved_candidate_ids"] == ["a"]
    assert rows.audit["invalid_evidence_count"] == rows.audit["invalid_candidate_count"] == 1
    diagnostic = rows.audit["candidates"][0]
    assert diagnostic["initial_diagnostic"]["decision"] == "INVALID_EVIDENCE"
    assert diagnostic["verification_resolution"] == "UNRESOLVED"
    assert ranker.repair_primary_evidence(QUERY, [source], rows) is rows


@pytest.mark.parametrize("remaining,calls,tokens,expected", [
    (19, 1, 1000, "SKIPPED_TIME"), (100, 6, 1000, "SKIPPED_BUDGET"),
    (100, 1, 95999, "SKIPPED_BUDGET"),
])
def test_repair_uses_existing_remaining_time_calls_and_tokens(remaining, calls, tokens, expected):
    provider = Provider("long_task")
    ranker = LLMReranker(client=provider, model="fixture")
    source = candidate("a", "设计一个通知服务，支持多终端投递和断线恢复。")
    initial = ranker._rank_candidates(QUERY, [source])
    limits = RequestLimits(deadline=time.monotonic() + remaining, max_calls=6, calls=calls,
        max_tokens=96000, tokens=tokens)
    token = current_limits.set(limits)
    try:
        result = ranker.repair_primary_evidence(QUERY, [source], initial)
    finally:
        current_limits.reset(token)
    assert len(provider.requests) == 1
    assert (limits.calls, limits.tokens, limits.token_reservations) == (calls, tokens, 0)
    assert result.audit["primary_evidence_repair"]["status"] == expected
    assert result.audit["candidates"][0]["verification_resolution"] == "UNRESOLVED"


def test_per_run_budget_prevents_primary_repair_without_reset():
    provider = Provider("long_task")
    budget = CallBudget(max_calls=1, max_tokens=96000)
    rows = LLMReranker(client=provider, model="fixture", budget=budget).rerank(QUERY,
        [candidate("a", "设计一个通知服务，支持多终端投递和断线恢复。")])
    assert len(provider.requests) == budget.used_calls == 1
    assert rows.audit["primary_evidence_repair"]["status"] == "SKIPPED_BUDGET"


def test_scope_positive_overlong_evidence_is_repaired_but_scope_enum_is_unresolved():
    source = candidate("a", "设计一个通知服务，支持多终端投递和断线恢复。")
    provider = Provider(None, scope_error="long_quote")
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source])
    assert len(rows) == 1 and provider.scope_calls == 2
    assert rows.audit["evidence_repair"]["recovered_count"] == 1
    assert rows.audit["candidates"][0]["initial_diagnostic"]["decision"] == "INVALID_SCHEMA"
    provider = Provider(None, scope_error="wrong_scope")
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [source])
    assert rows == [] and provider.scope_calls == 1
    assert rows.audit["candidates"][0]["verification_resolution"] == "UNRESOLVED"


def test_repair_cannot_borrow_canonical_construction_when_original_is_project_report():
    provider = Provider("joined_focus")
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, [candidate("a",
        "设计一个通知服务，支持多终端投递，要求断线恢复。", original="介绍你已有项目的通知服务。")])
    assert rows == []
    assert not any(row["decision"] == "ACCEPTED" for row in rows.audit["candidates"])
    assert rows.audit["candidates"][0]["decision"] == "SCOPE_REJECTED"


def test_optional_primary_repair_preserves_required_scope_call_budget():
    provider = Provider("long_task")
    ranker = LLMReranker(client=provider, model="fixture")
    source = candidate("a", "设计一个通知服务，支持多终端投递和断线恢复。")
    initial = ranker._rank_candidates(QUERY, [source])
    limits = RequestLimits(deadline=time.monotonic() + 100, max_calls=2, calls=1,
        max_tokens=96000, tokens=1000)
    token = current_limits.set(limits)
    try:
        rows = ranker.repair_primary_evidence(QUERY, [source], initial)
    finally:
        current_limits.reset(token)
    assert len(provider.requests) == 1 and limits.calls == 1
    assert rows.audit["primary_evidence_repair"]["status"] == "SKIPPED_BUDGET"
    assert rows.audit["primary_evidence_repair"]["time_policy"]["mandatory_scope_reserved_calls"] == 1


def test_mixed_batch_repairs_only_broken_positive_and_preserves_order_and_negative():
    class MixedProvider(Provider):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            result = json.loads(response.choices[0].message.content)
            if self.primary_calls == 1 and "rankings" in result:
                positive, broken, negative = result["rankings"]
                broken["task_evidence"] = "超长的原问证据" * 100
                negative.update(candidate_task="KNOWLEDGE", task_evidence="s0o0",
                    object_scope="NONE", focus_scope="NONE", object_evidence="", focus_evidence="")
                result["rankings"] = [negative, broken, positive]
                response.choices[0].message.content = json.dumps(result)
            return response

    sources = [candidate("valid", "设计一个缓存服务，要求支持并发更新和持久化。"),
        candidate("broken", "设计一个遥测服务，要求支持事件去重和断线恢复。"),
        candidate("negative", "介绍你用过的缓存原理。")]
    provider = MixedProvider(None)
    rows = LLMReranker(client=provider, model="fixture").rerank(QUERY, sources)
    assert [row["canonical_question_id"] for row in rows] == ["broken", "valid"]
    assert [row["rerank_rank"] for row in rows] == [2, 3]
    repaired_request = json.loads(provider.requests[1]["messages"][-1]["content"])
    assert len(repaired_request["candidates"]) == 1
    assert "遥测" in "".join(repaired_request["candidates"][0]["question"].values())
    diagnostics = {row["canonical_question_id"]: row for row in rows.audit["candidates"]}
    assert diagnostics["negative"]["decision"] == "TASK_REJECTED"
    assert "initial_diagnostic" not in diagnostics["negative"]
    assert diagnostics["broken"]["input_rank"] == 2 and diagnostics["broken"]["model_rank"] == 2
    assert rows.audit["primary_evidence_repair"]["candidate_ids"] == ["broken"]
    assert rows.audit["unresolved_candidate_ids"] == []
