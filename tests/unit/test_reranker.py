import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from interview_intelligence.search.reranker import LLMReranker, _model_error_code


class Client:
    def __init__(self, ids, grades=None, evidence=None, scopes=None, query_evidence=None):
        self.ids = ids
        self.grades = grades or {}
        self.evidence = evidence or {}
        self.scopes = scopes or {}
        self.query_evidence = query_evidence or {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        request = json.loads(kwargs["messages"][-1]["content"])
        texts = {row["id"]: row["question"] for row in request["candidates"]}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"query_object": "测试对象", "query_focus": "测试目标",
                "query_task": self.query_evidence.get("task", "OTHER"),
                "query_task_evidence": self.query_evidence.get("task_evidence", request["query"]),
                "query_object_evidence": self.query_evidence.get("object", request["query"]),
                "query_focus_evidence": self.query_evidence.get("focus", request["query"]), "rankings": [
                {"candidate_id": item,
                 "candidate_task": "DESIGN_TASK", "task_evidence": texts.get(item, "")[:160],
                 "object_scope": "SAME" if self.grades.get(item, 3) >= 2 else "UNKNOWN",
                 "focus_scope": {3: "SAME", 2: "CORE", 1: "ADJACENT", 0: "UNKNOWN"}[self.grades.get(item, 3)],
                 "object_evidence": self.evidence.get(item, {}).get("object", texts.get(item, "")),
                 "focus_evidence": self.evidence.get(item, {}).get("focus", texts.get(item, "")),
                 **self.scopes.get(item, {})}
                for item in self.ids]})
        ))])


def test_reranker_must_return_exact_candidate_set():
    candidates = [{"canonical_question_id": "a", "canonical_text": "Redis 过期"},
                  {"canonical_question_id": "b", "canonical_text": "缓存击穿"}]
    reranker = LLMReranker(client=Client(["c1", "c0"]), model="test")
    assert [item["canonical_question_id"] for item in reranker._rank_candidates("击穿", candidates)] == ["b", "a"]
    with pytest.raises(ValueError, match="candidate set"):
        LLMReranker(client=Client(["c1"]), model="test")._rank_candidates("击穿", candidates)


def test_source_neighbor_cannot_ground_current_candidate_evidence():
    captured = []
    client = Client(["c0"], evidence={"c0": {"object": "排行榜", "focus": "排行榜"}})
    create = client.chat.completions.create
    def record(**kwargs):
        captured.append(json.loads(kwargs["messages"][-1]["content"]))
        return create(**kwargs)
    client.chat.completions.create = record
    candidate = {"canonical_question_id": "a", "canonical_text": "请讲已有权限表结构。",
        "source_context": [{"original_question": "请讲已有权限表结构。",
                            "context_after": "下一题：设计一个排行榜", "source_context_status": "VERIFIED"}]}
    rows = LLMReranker(client=client, model="test")._rank_candidates("排行榜", [candidate])
    assert captured[0]["candidates"][0]["source_context"] == candidate["source_context"]
    assert rows == []
    assert rows.audit["candidates"][0]["decision"] == "INVALID_EVIDENCE"


@pytest.mark.parametrize("task", ["PROJECT_REPORT", "CODE_TASK", "LOCAL_DETAIL", "KNOWLEDGE", "UNKNOWN"])
def test_independent_design_task_fence_rejects_other_deliverables_despite_direct_score(task):
    candidate = {"canonical_question_id": "a", "canonical_text": "设计一个缓存服务。"}
    client = Client(["c0"], scopes={"c0": {"candidate_task": task}},
                    query_evidence={"task": "INDEPENDENT_SYSTEM_DESIGN"})
    rows = LLMReranker(client=client, model="test")._rank_candidates("独立系统设计题", [candidate])
    assert rows == []
    assert rows.audit["candidates"][0]["decision"] == "TASK_REJECTED"


def test_design_task_proof_cannot_be_borrowed_from_neighbor_or_unverified_original():
    candidate = {"canonical_question_id": "a", "canonical_text": "并发数多少？", "source_context": [
        {"source_context_status": "VERIFIED", "original_question": "并发数多少？", "context_before": "设计一个缓存服务。"},
        {"source_context_status": "UNAVAILABLE", "original_question": "设计一个缓存服务。"}]}
    client = Client(["c0"], scopes={"c0": {"task_evidence": "设计一个缓存服务。"}},
                    query_evidence={"task": "INDEPENDENT_SYSTEM_DESIGN"})
    rows = LLMReranker(client=client, model="test")._rank_candidates("独立系统设计题", [candidate])
    assert rows == []
    assert rows.audit["candidates"][0]["task_grounded"] is False


def test_verified_original_can_prove_new_design_and_other_queries_keep_their_task_types():
    candidate = {"canonical_question_id": "a", "canonical_text": "如何保证吞吐？", "source_context": [
        {"source_context_status": "VERIFIED", "original_question": "重新设计消息服务以满足新的吞吐要求。"}]}
    client = Client(["c0"], scopes={"c0": {"task_evidence": "重新设计消息服务"}},
                    query_evidence={"task": "INDEPENDENT_SYSTEM_DESIGN"})
    assert LLMReranker(client=client, model="test")._rank_candidates("独立系统设计题", [candidate])
    client = Client(["c0"], scopes={"c0": {"candidate_task": "CODE_TASK"}})
    assert LLMReranker(client=client, model="test")._rank_candidates("手写线程安全缓存", [candidate])


def test_reranker_does_not_present_unrelated_or_only_broadly_related_questions():
    candidates = [{"canonical_question_id": "a", "canonical_text": "OOM 排查"},
                  {"canonical_question_id": "b", "canonical_text": "JVM 类加载"},
                  {"canonical_question_id": "c", "canonical_text": "Redis 数据类型"}]
    reranker = LLMReranker(client=Client(["c0", "c1", "c2"], {"c0": 3, "c1": 1, "c2": 0}), model="test")
    rows = reranker._rank_candidates("OOM 问法", candidates)
    assert [row["canonical_question_id"] for row in rows] == ["a"]
    assert rows[0]["relevance_grade"] == 3
    assert LLMReranker(client=Client(["c0"], {"c0": 0}), model="test")._rank_candidates("OOM", candidates[:1]) == []


def test_ark_rerank_disables_unbounded_reasoning_and_caps_output():
    captured = []
    underlying = Client(["c0"])
    def create(**kwargs):
        captured.append(kwargs)
        return underlying.create(**kwargs)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    candidates = [{"canonical_question_id": "a", "canonical_text": "OOM排查"}]
    LLMReranker(client=client, model="test", base_url="https://ark.cn-beijing.volces.com/api/coding/v3")._rank_candidates("OOM", candidates)
    assert captured[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert captured[0]["max_tokens"] == 6144
    LLMReranker(client=client, model="test", base_url="https://another.example/v1")._rank_candidates("OOM", candidates)
    assert "extra_body" not in captured[1]


@pytest.mark.parametrize("ids", [["c0", "c0"], ["c0", "unknown"]])
def test_reranker_rejects_duplicate_or_invented_ids(ids):
    candidates = [{"canonical_question_id": "a"}, {"canonical_question_id": "b"}]
    with pytest.raises(ValueError, match="candidate set"):
        LLMReranker(client=Client(ids), model="test")._rank_candidates("OOM", candidates)


@pytest.mark.parametrize("finish_reason", ["stop", None, "length"])
def test_streamed_reranker_requires_complete_output_and_keeps_usage(finish_reason):
    payload = json.dumps({"query_object": "内存", "query_focus": "OOM排查",
        "query_task": "OTHER", "query_task_evidence": "OOM",
        "query_object_evidence": "OOM", "query_focus_evidence": "OOM", "rankings": [
        {"candidate_id": "c0",
         "candidate_task": "KNOWLEDGE", "task_evidence": "OOM排查",
         "object_scope": "SAME", "focus_scope": "SAME", "object_evidence": "OOM", "focus_evidence": "OOM排查"}]})
    chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=part),
                 finish_reason=None)], usage=None, model="resolved-test")
              for part in [payload[:20], payload[20:]]]
    if finish_reason:
        chunks.append(SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None),
            finish_reason=finish_reason)], usage=None, model="resolved-test"))
    chunks.append(SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20),
                                  model="resolved-test"))
    class Stream:
        closed = False
        def __iter__(self):
            return iter(chunks)
        def close(self):
            self.closed = True
    response = Stream()
    requests, logs = [], []
    def create(**kwargs):
        requests.append(kwargs)
        return response
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    reranker = LLMReranker(client=client, model="test", stream=True, on_call=logs.append)
    if finish_reason == "stop":
        assert reranker._rank_candidates("OOM", [{"canonical_question_id": "a", "canonical_text": "OOM排查"}])[0]["canonical_question_id"] == "a"
        assert logs[0]["input_tokens"] == 50
        assert logs[0]["model_revision"] == "resolved-test"
    else:
        with pytest.raises(ValueError, match="STREAM_INCOMPLETE|MODEL_OUTPUT_TRUNCATED"):
            reranker._rank_candidates("OOM", [{"canonical_question_id": "a"}])
        assert logs[0]["status"] == "FAILED"
    assert requests[0]["stream"] is True
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert response.closed


@pytest.mark.parametrize("evidence", [
    {"object": "不存在的Agent", "focus": "内存泄漏排查"},
    {"object": "内存", "focus": "内存...排查"},
    {"object": "", "focus": "内存泄漏排查"},
    {"object": "内存", "focus": "   "},
])
def test_high_score_cannot_replace_grounded_object_and_focus_evidence(evidence):
    candidates = [{"canonical_question_id": "a", "canonical_text": "线上内存泄漏排查"},
                  {"canonical_question_id": "b", "canonical_text": "OOM排查"}]
    rows = LLMReranker(client=Client(["c0", "c1"], evidence={"c0": evidence}), model="test")._rank_candidates("内存泄漏", candidates)
    assert [row["canonical_question_id"] for row in rows] == ["b"]
    assert rows.audit["candidates"][0]["decision"] == "INVALID_EVIDENCE"
    assert rows.audit["candidates"][0]["invalid_quotes"] == {key: value.strip() for key, value in evidence.items()}
    assert rows.audit["candidates"][1]["decision"] == "ACCEPTED"
    assert rows[0]["relevance_evidence"] == {"object": "OOM排查", "focus": "OOM排查"}


def test_evidence_and_diagnostics_are_bound_to_each_rerank_call():
    reranker = LLMReranker(client=Client(["c0"]), model="test")
    first = reranker._rank_candidates("Agent记忆", [{"canonical_question_id": "a", "canonical_text": "Agent记忆设计"}])
    second = reranker._rank_candidates("秒杀", [{"canonical_question_id": "b", "canonical_text": "秒杀库存设计"}])
    assert first.audit["candidates"][0]["canonical_question_id"] == "a"
    assert second.audit["candidates"][0]["canonical_question_id"] == "b"
    assert first[0]["relevance_evidence"]["object"] == "Agent记忆设计"


@pytest.mark.parametrize("scope", [
    {"object_scope": "PARENT"},
    {"object_scope": "COMPONENT"},
    {"focus_scope": "BROAD"},
    {"focus_scope": "ADJACENT"},
    {"focus_scope": "UNKNOWN"},
    {"object_scope": "FLOW"},  # Covering flow is not direct object identity.
])
def test_grounded_quotes_cannot_override_nonmatching_scope(scope):
    candidate = {"canonical_question_id": "a", "canonical_text": "Agent质量评分下降怎么排查？"}
    rows = LLMReranker(client=Client(["c0"], scopes={"c0": scope}), model="test")._rank_candidates(
        "Agent工具结果质量诊断", [candidate])
    assert rows == []
    diagnostic = rows.audit["candidates"][0]
    assert diagnostic["quote_grounded"] is True
    assert diagnostic["relevance_grade"] < 2
    assert diagnostic["decision"] == "BELOW_THRESHOLD"


@pytest.mark.parametrize("object_scope,focus_scope", [
    ("PARENT", "CORE"),
    ("FLOW", "CORE"),
    ("SAME", "BROAD"),
    ("SAME", "ADJACENT"),
    ("SAME", "UNKNOWN"),
])
def test_rejected_object_and_task_scope_pairs_remain_below_threshold(object_scope, focus_scope):
    candidate = {"canonical_question_id": "a", "canonical_text": "Agent执行失败后怎样反思学习？"}
    rows = LLMReranker(client=Client(["c0"], grades={"c0": 2}, scopes={"c0": {
        "object_scope": object_scope, "focus_scope": focus_scope}}), model="test")._rank_candidates(
            "Agent API调用失败怎么处理", [candidate])
    assert rows == []
    assert rows.audit["candidates"][0]["quote_grounded"] is True
    assert rows.audit["candidates"][0]["relevance_grade"] < 2


def test_whole_flow_diagnosis_and_tight_recovery_remain_eligible_without_literal_query_overlap():
    candidates = [
        {"canonical_question_id": "trace", "canonical_text": "当Agent输出结果不符合预期时，如何完整捞取一次调用的全链路信息以定位问题？"},
        {"canonical_question_id": "retry", "canonical_text": "工具调用失败时，Agent是怎么重试和降级的？"},
        {"canonical_question_id": "reliability", "canonical_text": "如何保障Agent Function Calling的可靠性？如果模型一直调用同一个错误工具该如何处理？"},
    ]
    trace = LLMReranker(client=Client(["c0"], grades={"c0": 2}, scopes={"c0": {
        "object_scope": "FLOW", "focus_scope": "TRACE"}}), model="test")._rank_candidates(
            "Agent工具结果质量诊断", candidates[:1])
    assert [r["canonical_question_id"] for r in trace] == ["trace"]
    assert trace[0]["relevance_grade"] == 2
    recovery = LLMReranker(client=Client(["c0", "c1"], grades={"c0": 2, "c1": 2}), model="test")._rank_candidates(
        "Agent API没成功后怎么处理", candidates[1:])
    assert [r["canonical_question_id"] for r in recovery] == ["retry", "reliability"]


def test_wide_agent_design_accepts_explicit_application_and_subsystem_instances():
    candidates = [
        {"canonical_question_id": "application", "canonical_text": "怎么设计一个Agent智能客服助手？"},
        {"canonical_question_id": "memory", "canonical_text": "Agent的上下文记忆该如何分层维护？"},
    ]
    rows = LLMReranker(client=Client(["c0", "c1"], grades={"c1": 2}, scopes={
        "c0": {"object_scope": "INSTANCE"}, "c1": {"object_scope": "INSTANCE"}}), model="test")._rank_candidates(
            "Agent应用与运行框架设计", candidates)
    assert [r["canonical_question_id"] for r in rows] == ["application", "memory"]


@pytest.mark.parametrize("query_evidence", [
    {"object": "Redis库存"}, {"focus": "不存在的调用故障"}, {"focus": ""},
])
def test_query_intent_quotes_are_validated_against_query_not_candidate_text(query_evidence):
    logs = []
    client = Client(["c0"], query_evidence=query_evidence)
    reranker = LLMReranker(client=client, model="test", on_call=logs.append)
    with pytest.raises(ValueError, match="query intent|validation error"):
        reranker._rank_candidates("团购库存快速耗完怎么办", [{"canonical_question_id": "a", "canonical_text": "Redis库存问题"}])
    assert logs[0]["status"] == "FAILED"


def test_r14_schema_requests_only_scopes_and_query_quotes_but_not_numeric_self_grade():
    requests = []
    underlying = Client(["c0"])
    def create(**kwargs):
        requests.append(kwargs)
        return underlying.create(**kwargs)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    rows = LLMReranker(client=client, model="test")._rank_candidates("内存泄漏排查", [
        {"canonical_question_id": "a", "canonical_text": "内存泄漏的原因和预防"}])
    schema = requests[0]["response_format"]["json_schema"]
    assert schema["name"] == "rerank_v19_design_unit_source_context"
    assert {"query_object_evidence", "query_focus_evidence"} <= set(schema["schema"]["required"])
    item = schema["schema"]["$defs"]["CandidateRank"]
    assert {"object_scope", "focus_scope"} <= set(item["required"])
    assert "relevance_grade" not in item["properties"]
    assert rows.audit["scope_contract_version"] == "object_task_scope_v2"
    assert rows.audit["query_focus_evidence"] == "内存泄漏排查"


@pytest.mark.parametrize("failure,expected_code", [
    ("missing_query_field", "query_focus_evidence:missing"),
    ("extra_top_field", "?:extra_forbidden"),
    ("invalid_json", "?:json_invalid"),
    ("multiple_errors", "query_focus_evidence:missing"),
])
def test_global_schema_failures_keep_bounded_safe_diagnostics_usage_and_fail_closed(failure, expected_code):
    requests, logs = [], []
    underlying = Client(["c0"])
    def create(**kwargs):
        requests.append(kwargs)
        response = underlying.create(**kwargs)
        payload = json.loads(response.choices[0].message.content)
        item = payload["rankings"][0]
        if failure == "missing_query_field":
            del payload["query_focus_evidence"]
        elif failure == "extra_top_field":
            payload["SECRET_FIELD_NAME"] = "SECRET_INPUT"
        elif failure == "multiple_errors":
            del payload["query_focus_evidence"]
            item["object_scope"], item["focus_scope"] = "INVALID_SCOPE", "INVALID_SCOPE"
        response.choices[0].message.content = "{SECRET_INPUT" if failure == "invalid_json" else json.dumps(payload)
        response.usage = SimpleNamespace(prompt_tokens=50, completion_tokens=20)
        response.model = "resolved-test"
        return response
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    reranker = LLMReranker(client=client, model="test", on_call=logs.append)
    with pytest.raises(ValidationError) as raised:
        reranker._rank_candidates("OOM", [{"canonical_question_id": "a", "canonical_text": "OOM排查"}])
    assert len(requests) == len(logs) == 1  # No repair/retry request was introduced.
    assert requests[0]["max_tokens"] == 6144
    log = logs[0]
    code = log["error_code"]
    assert code.startswith("ValidationError@") and expected_code in code
    assert code.endswith("+" + str(raised.value.error_count()))
    assert len(code) <= 64 and code.isascii() and "SECRET" not in code
    assert log["status"] == "FAILED" and log["retry_count"] == 0
    assert log["input_tokens"] == 50 and log["output_tokens"] == 20
    assert log["model_revision"] == "resolved-test"


def test_diagnostic_bounds_and_whitelist_never_include_model_input_context_or_unknown_keys():
    error = ValidationError.from_exception_data("SECRET_TITLE", [{
        "type": "literal_error", "loc": ("rankings", 49, *(["query_object_evidence"] * 10), "SECRET_FIELD"),
        "input": "SECRET_INPUT", "ctx": {"expected": "SECRET_CONTEXT"},
    }, {"type": "missing", "loc": ("SECRET_FIELD",), "input": "SECRET_INPUT"}])
    code = _model_error_code(error)
    assert len(code) <= 64 and code.endswith("+2") and code.isascii()
    assert code.startswith("ValidationError@r[49].") and ":literal_error" in code
    assert "SECRET" not in code
    unknown = ValidationError.from_exception_data("SECRET_TITLE", [{
        "type": "extra_forbidden", "loc": ("SECRET_FIELD",), "input": "SECRET_INPUT"}])
    assert _model_error_code(unknown) == "ValidationError@?:extra_forbidden+1"
    assert _model_error_code(ValueError("SECRET_INPUT")) == "ValueError"
    assert _model_error_code(None) is None


def test_r14_provider_contract_keeps_two_axes_with_strict_enums_and_complete_required_fields():
    requests = []
    underlying = Client(["c0"], grades={"c0": 0})
    def create(**kwargs):
        requests.append(kwargs)
        return underlying.create(**kwargs)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    assert LLMReranker(client=client, model="test")._rank_candidates("OOM", [
        {"canonical_question_id": "a", "canonical_text": "Redis数据类型"}]) == []
    request = requests[0]
    schema = request["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"query_task", "query_task_evidence", "query_object", "query_focus", "query_object_evidence", "query_focus_evidence", "rankings"}
    candidate = schema["$defs"]["CandidateRank"]
    assert candidate["additionalProperties"] is False
    assert set(candidate["required"]) == {"candidate_id", "candidate_task", "task_evidence", "object_evidence", "focus_evidence", "object_scope", "focus_scope"}
    assert set(candidate["properties"]) == set(candidate["required"])
    assert candidate["properties"]["object_scope"]["enum"] == ["SAME", "INSTANCE", "FLOW", "PARENT", "COMPONENT", "NONE", "UNKNOWN"]
    assert candidate["properties"]["focus_scope"]["enum"] == ["SAME", "CORE", "TRACE", "BROAD", "ADJACENT", "NONE", "UNKNOWN"]
    assert "object_relation" not in candidate["properties"] and "focus_relation" not in candidate["properties"]
    prompt = request["messages"][0]["content"]
    assert "仅输出一个JSON对象" in prompt and "不得输出额外字段" in prompt
    assert "object_relation" not in prompt and "focus_relation" not in prompt
    assert "NONE表示该轴明确无关；UNKNOWN表示缺少证据或无法判断" in prompt
    assert request["max_tokens"] == 6144


@pytest.mark.parametrize("object_scope,accepted_grades,object_relation", [
    ("SAME", {"SAME": 3, "CORE": 2, "TRACE": 2}, "EXPLICIT"),
    ("INSTANCE", {"SAME": 3, "CORE": 2, "TRACE": 2}, "EXPLICIT"),
    ("FLOW", {"TRACE": 2}, "EXPLICIT"),
    ("PARENT", {}, "INFERRED"),
    ("COMPONENT", {}, "INFERRED"),
    ("NONE", {}, "NONE"),
    ("UNKNOWN", {}, "NONE"),
])
@pytest.mark.parametrize("focus_scope,focus_relation", [
    ("SAME", "DIRECT"), ("CORE", "SUBTASK"), ("TRACE", "SUBTASK"),
    ("BROAD", "NEIGHBOR"), ("ADJACENT", "NEIGHBOR"), ("NONE", "NONE"), ("UNKNOWN", "NONE"),
])
def test_two_axis_acceptance_matrix_and_host_derived_compatibility_audit(
        object_scope, accepted_grades, object_relation, focus_scope, focus_relation):
    rows = LLMReranker(client=Client(["c0"], scopes={"c0": {
        "object_scope": object_scope, "focus_scope": focus_scope}}), model="test")._rank_candidates(
            "Agent调用故障", [{"canonical_question_id": "a", "canonical_text": "Agent调用故障排查"}])
    diagnostic = rows.audit["candidates"][0]
    assert diagnostic["quote_grounded"] is True
    if focus_scope in accepted_grades:
        assert [row["canonical_question_id"] for row in rows] == ["a"]
        assert diagnostic["relevance_grade"] == accepted_grades[focus_scope]
    else:
        assert rows == [] and diagnostic["relevance_grade"] < 2
    if object_scope in {"NONE", "UNKNOWN"} or focus_scope in {"NONE", "UNKNOWN"}:
        assert diagnostic["relevance_grade"] == 0
    assert diagnostic["object_relation"] == object_relation
    assert diagnostic["focus_relation"] == focus_relation
    assert diagnostic["relation_source"] == rows.audit["relation_source"] == "host_derived_from_scopes"


@pytest.mark.parametrize("removed_field,old_value", [("object_relation", "EXPLICIT"), ("focus_relation", "DIRECT")])
def test_removed_redundant_model_relations_are_not_silently_accepted(removed_field, old_value):
    logs = []
    reranker = LLMReranker(client=Client(["c0"], scopes={"c0": {removed_field: old_value}}),
                          model="test", on_call=logs.append)
    rows = reranker._rank_candidates("OOM", [{"canonical_question_id": "a", "canonical_text": "OOM排查"}])
    assert rows == []
    assert rows.audit["candidates"][0]["decision"] == "INVALID_SCHEMA"
    assert rows.audit["candidates"][0]["schema_error_code"] == "ValidationError@r[0].?:extra_forbidden+1"
    assert logs[0]["status"] == "SUCCEEDED"
    assert logs[0]["error_code"] == "PARTIAL_SCHEMA_REJECTED:1"


@pytest.mark.parametrize("axis", ["object_scope", "focus_scope"])
def test_two_axis_illegal_enum_is_never_mapped_to_none_or_unknown(axis):
    logs = []
    rows = LLMReranker(client=Client(["c0"], scopes={"c0": {axis: "INVALID_SCOPE"}}), model="test",
                    on_call=logs.append)._rank_candidates("OOM", [{"canonical_question_id": "a", "canonical_text": "OOM排查"}])
    assert rows == []
    diagnostic = rows.audit["candidates"][0]
    assert diagnostic["schema_error_code"] == f"ValidationError@r[0].{axis}:literal_error+1"
    assert diagnostic["decision"] == "INVALID_SCHEMA" and diagnostic["relevance_grade"] == 0
    assert axis not in diagnostic  # Neither guessing nor mapping to UNKNOWN.
    assert rows.audit["invalid_candidate_count"] == 1
    assert rows.audit["candidate_verification_status"] == "PARTIAL"
    assert logs[0]["status"] == "SUCCEEDED" and logs[0]["error_code"] == "PARTIAL_SCHEMA_REJECTED:1"


def test_given_output_quality_scopes_execution_adjacency_is_rejected_but_output_trace_remains():
    """Injected labels exercise the host contract, not the real model's understanding."""
    candidates = [
        {"canonical_question_id": "a", "canonical_text": "工具调用超时以后如何重试和降级？"},
        {"canonical_question_id": "b", "canonical_text": "如何验证工具调用的输入参数格式？"},
        {"canonical_question_id": "c", "canonical_text": "怎样评估工具返回内容的准确性并定位错误输出？"},
        {"canonical_question_id": "d", "canonical_text": "如何完整采集Agent调用链以定位输出异常？"},
    ]
    rows = LLMReranker(client=Client(["c0", "c1", "c2", "c3"], scopes={
        "c0": {"object_scope": "PARENT", "focus_scope": "ADJACENT"},
        "c1": {"object_scope": "PARENT", "focus_scope": "ADJACENT"},
        "c2": {"object_scope": "SAME", "focus_scope": "SAME"},
        "c3": {"object_scope": "FLOW", "focus_scope": "TRACE"},
    }), model="test")._rank_candidates("Agent工具成功调用后返回的内容质量很差，如何定位原因？", candidates)
    assert [item["canonical_question_id"] for item in rows] == ["c", "d"]
    audit = rows.audit["candidates"]
    assert all(item["quote_grounded"] for item in audit)
    assert [item["decision"] for item in audit] == ["BELOW_THRESHOLD", "BELOW_THRESHOLD", "ACCEPTED", "ACCEPTED"]
    assert [item["relevance_grade"] for item in audit[2:]] == [3, 2]


@pytest.mark.parametrize("query,focus_scopes,expected_ids", [
    ("工具API调用失败后如何恢复？", ["CORE", "ADJACENT"], ["a"]),
    ("工具系统的整体可靠性如何保障，包括执行和返回内容？", ["CORE", "CORE"], ["a", "b"]),
])
def test_given_failure_and_wide_reliability_scopes_preserve_the_requested_stage(query, focus_scopes, expected_ids):
    """The fixture supplies semantic scopes; provider accuracy needs real evaluation."""
    candidates = [
        {"canonical_question_id": "a", "canonical_text": "工具调用超时后如何重试和降级？"},
        {"canonical_question_id": "b", "canonical_text": "怎样评估工具返回内容的准确性？"},
    ]
    rows = LLMReranker(client=Client(["c0", "c1"], scopes={
        f"c{i}": {"object_scope": "INSTANCE", "focus_scope": focus} for i, focus in enumerate(focus_scopes)
    }), model="test")._rank_candidates(query, candidates)
    assert [item["canonical_question_id"] for item in rows] == expected_ids


@pytest.mark.parametrize("failure,expected_location", [
    ("scope_enum", "object_scope:literal_error+1"),
    ("missing_scope", "focus_scope:missing+1"),
    ("extra_key", "?:extra_forbidden+1"),
    ("long_quote", "focus_evidence:string_too_long+1"),
    ("wrong_evidence_type", "object_evidence:string_type+1"),
    ("multiple_scope_errors", "object_scope:literal_error+2"),
])
def test_known_id_row_schema_failure_is_isolated_without_relaxing_valid_rows(failure, expected_location):
    requests, logs = [], []
    underlying = Client(["c1", "c0"])
    def create(**kwargs):
        requests.append(kwargs)
        response = underlying.create(**kwargs)
        payload = json.loads(response.choices[0].message.content)
        item = payload["rankings"][0]
        if failure == "scope_enum":
            item["object_scope"] = "INVALID_SCOPE"
        elif failure == "missing_scope":
            del item["focus_scope"]
        elif failure == "extra_key":
            item["SECRET_FIELD_NAME"] = "SECRET_INPUT"
        elif failure == "long_quote":
            item["focus_evidence"] = "SECRET_INPUT" * 30
        elif failure == "wrong_evidence_type":
            item["object_evidence"] = {"SECRET_FIELD": "SECRET_INPUT"}
        else:
            item["object_scope"], item["focus_scope"] = "INVALID_SCOPE", "INVALID_SCOPE"
        response.choices[0].message.content = json.dumps(payload)
        response.usage = SimpleNamespace(prompt_tokens=50, completion_tokens=20)
        response.model = "resolved-test"
        return response
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    rows = LLMReranker(client=client, model="test", on_call=logs.append)._rank_candidates("OOM", [
        {"canonical_question_id": "a", "canonical_text": "OOM排查"},
        {"canonical_question_id": "b", "canonical_text": "OOM预防"},
    ])
    assert [row["canonical_question_id"] for row in rows] == ["a"]
    assert rows[0]["relevance_grade"] == 3 and rows[0]["rerank_rank"] == 2
    assert rows[0]["relevance_evidence"] == {"object": "OOM排查", "focus": "OOM排查"}
    assert rows.audit["candidate_verification_status"] == "PARTIAL"
    assert rows.audit["invalid_candidate_count"] == 1  # Count rows, not validation errors.
    invalid, valid = rows.audit["candidates"]
    assert invalid["canonical_question_id"] == "b" and invalid["input_rank"] == 2 and invalid["model_rank"] == 1
    assert invalid["decision"] == invalid["verification_status"] == "INVALID_SCHEMA"
    assert invalid["relevance_grade"] == 0
    assert invalid["quote_grounded"] is invalid["object_quote_grounded"] is invalid["focus_quote_grounded"] is False
    assert invalid["schema_error_code"] == f"ValidationError@r[0].{expected_location}"
    assert len(invalid["schema_error_code"]) <= 64
    assert "SECRET" not in json.dumps(invalid)
    assert "object_scope" not in invalid and "focus_scope" not in invalid
    assert valid["decision"] == "ACCEPTED" and valid["quote_grounded"] is True
    assert len(requests) == len(logs) == 1
    assert requests[0]["response_format"]["json_schema"]["strict"] is True
    assert requests[0]["max_tokens"] == 6144
    assert logs[0]["status"] == "SUCCEEDED" and logs[0]["error_code"] == "PARTIAL_SCHEMA_REJECTED:1"
    assert logs[0]["retry_count"] == 0
    assert logs[0]["input_tokens"] == 50 and logs[0]["output_tokens"] == 20
    assert logs[0]["model_revision"] == "resolved-test"


def test_all_invalid_rows_remain_partial_empty_not_verified_no_match():
    logs = []
    rows = LLMReranker(client=Client(["c1", "c0"], scopes={
        "c0": {"object_scope": "INVALID_SCOPE"}, "c1": {"focus_scope": "INVALID_SCOPE"}}), model="test",
        on_call=logs.append)._rank_candidates("OOM", [
            {"canonical_question_id": "a", "canonical_text": "OOM排查"},
            {"canonical_question_id": "b", "canonical_text": "OOM预防"},
        ])
    assert rows == [] and rows.audit["candidate_verification_status"] == "PARTIAL"
    assert rows.audit["invalid_candidate_count"] == 2
    assert [item["decision"] for item in rows.audit["candidates"]] == ["INVALID_SCHEMA", "INVALID_SCHEMA"]
    assert logs[0]["error_code"] == "PARTIAL_SCHEMA_REJECTED:2" and logs[0]["status"] == "SUCCEEDED"


@pytest.mark.parametrize("identity_failure", ["missing", "unknown", "duplicate", "nonstr", "padded", "missing_row"])
def test_row_isolation_never_repairs_candidate_identity_or_permutation(identity_failure):
    logs = []
    underlying = Client(["c0", "c1"], scopes={"c0": {"object_scope": "INVALID_SCOPE"}})
    def create(**kwargs):
        response = underlying.create(**kwargs)
        payload = json.loads(response.choices[0].message.content)
        item = payload["rankings"][0]
        if identity_failure == "missing":
            del item["candidate_id"]
        elif identity_failure == "unknown":
            item["candidate_id"] = "invented"
        elif identity_failure == "duplicate":
            item["candidate_id"] = "c1"
        elif identity_failure == "nonstr":
            item["candidate_id"] = ["c0"]
        elif identity_failure == "padded":
            item["candidate_id"] = " c0 "
        else:
            payload["rankings"].pop()
        response.choices[0].message.content = json.dumps(payload)
        return response
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(ValueError, match="candidate set"):
        LLMReranker(client=client, model="test", on_call=logs.append)._rank_candidates("OOM", [
            {"canonical_question_id": "a", "canonical_text": "OOM排查"},
            {"canonical_question_id": "b", "canonical_text": "OOM预防"},
        ])
    assert logs[0]["status"] == "FAILED" and logs[0]["error_code"] == "ValueError"


@pytest.mark.parametrize("global_failure", ["query_quote", "empty_query_quote", "extra_top", "nonobject_row"])
def test_batch_envelope_and_query_evidence_are_not_row_isolated(global_failure):
    logs = []
    underlying = Client(["c0", "c1"], scopes={"c0": {"object_scope": "INVALID_SCOPE"}})
    def create(**kwargs):
        response = underlying.create(**kwargs)
        payload = json.loads(response.choices[0].message.content)
        if global_failure == "query_quote":
            payload["query_focus_evidence"] = "候选中的原文而非query"
        elif global_failure == "empty_query_quote":
            payload["query_focus_evidence"] = ""
        elif global_failure == "extra_top":
            payload["SECRET_FIELD_NAME"] = "SECRET_INPUT"
        else:
            payload["rankings"][1] = "not an object"
        response.choices[0].message.content = json.dumps(payload)
        return response
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises((ValueError, ValidationError)):
        LLMReranker(client=client, model="test", on_call=logs.append)._rank_candidates("OOM", [
            {"canonical_question_id": "a", "canonical_text": "OOM排查"},
            {"canonical_question_id": "b", "canonical_text": "OOM预防"},
        ])
    assert logs[0]["status"] == "FAILED" and not logs[0]["error_code"].startswith("PARTIAL")
    assert "SECRET" not in logs[0]["error_code"]


def test_valid_rows_in_partial_batch_still_need_grounded_quotes_and_matching_scopes():
    rows = LLMReranker(client=Client(["c0", "c1", "c2"], scopes={
        "c0": {"focus_scope": "INVALID_SCOPE"}, "c2": {"focus_scope": "ADJACENT"}},
        evidence={"c1": {"focus": "不存在的证据"}}), model="test")._rank_candidates("OOM", [
            {"canonical_question_id": "a", "canonical_text": "OOM排查"},
            {"canonical_question_id": "b", "canonical_text": "OOM预防"},
            {"canonical_question_id": "c", "canonical_text": "内存分配原理"},
        ])
    assert rows == []
    assert rows.audit["invalid_candidate_count"] == 1
    assert [item["decision"] for item in rows.audit["candidates"]] == [
        "INVALID_SCHEMA", "INVALID_EVIDENCE", "BELOW_THRESHOLD"]


def test_host_envelope_preserves_all_provider_global_field_constraints():
    from interview_intelligence.search.reranker import RerankEnvelope, RerankResult
    expected, envelope = RerankResult.model_json_schema(), RerankEnvelope.model_json_schema()
    assert set(expected["required"]) == set(envelope["required"])
    assert envelope["additionalProperties"] is False
    for name in set(expected["required"]) - {"rankings"}:
        assert envelope["properties"][name] == expected["properties"][name]
    assert envelope["properties"]["rankings"]["minItems"] == 1
    assert envelope["properties"]["rankings"]["maxItems"] == 50


@pytest.mark.parametrize("query,scopes,expected", [
    ("医院预约名额很快用完怎么处置？", [("SAME", "CORE"), ("PARENT", "ADJACENT"), ("COMPONENT", "ADJACENT")], ["a"]),
    ("资源预约系统的整体设计，包括容量与并发分配？", [("INSTANCE", "CORE"), ("INSTANCE", "CORE"), ("COMPONENT", "BROAD")], ["a", "b"]),
])
def test_given_narrow_incident_and_broad_resource_scopes_keep_distinct_object_and_task(query, scopes, expected):
    """Host behavior with supplied scopes; this does not prove model semantic accuracy."""
    candidates = [
        {"canonical_question_id": "a", "canonical_text": "医院预约名额耗尽后如何排队与补充名额？"},
        {"canonical_question_id": "b", "canonical_text": "售票系统如何并发分配票数以防止超卖？"},
        {"canonical_question_id": "c", "canonical_text": "通用计数器如何保证原子扣减？"},
    ]
    rows = LLMReranker(client=Client(["c0", "c1", "c2"], scopes={
        f"c{i}": {"object_scope": obj, "focus_scope": focus} for i, (obj, focus) in enumerate(scopes)
    }), model="test")._rank_candidates(query, candidates)
    assert [item["canonical_question_id"] for item in rows] == expected
    assert rows.audit["candidate_verification_status"] == "COMPLETE"
    assert rows.audit["invalid_candidate_count"] == 0


class TwoStageClient:
    def __init__(self, *, primary=None, verdicts=None, mutate_verifier=None, finish=None):
        self.primary = primary or Client(["c0"])
        self.verdicts = verdicts or {}
        self.mutate_verifier = mutate_verifier
        self.finish = finish or {}
        self.requests, self.streams = [], []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        verifier = kwargs["response_format"]["json_schema"]["name"] == "scope_verify_v5_design_unit_clarity_source_context"
        stage = "verify" if verifier else "primary"
        if verifier:
            request = json.loads(kwargs["messages"][-1]["content"])
            payload = {"verifications": [{"candidate_id": row["id"],
                "keep": self.verdicts.get(row["id"], True), "reason": "题干明确覆盖当前对象及任务。"}
                for row in request["candidates"]]}
            if self.mutate_verifier:
                self.mutate_verifier(payload)
            content = payload if isinstance(payload, str) else json.dumps(payload)
        else:
            content = self.primary.create(**kwargs).choices[0].message.content
        usage = SimpleNamespace(prompt_tokens=200 if verifier else 100, completion_tokens=20 if verifier else 10)
        finish = self.finish.get(stage, "stop")
        if not kwargs.get("stream"):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)],
                                   usage=usage, model=f"resolved-{stage}")
        chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content[:20]), finish_reason=None)],
                                  usage=None, model=f"resolved-{stage}"),
                  SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content[20:]), finish_reason=finish)],
                                  usage=None, model=f"resolved-{stage}"),
                  SimpleNamespace(choices=[], usage=usage, model=f"resolved-{stage}")]
        class Stream:
            closed = False
            def __iter__(self):
                return iter(chunks)
            def close(self):
                self.closed = True
        response = Stream()
        self.streams.append(response)
        return response


def test_public_rerank_uses_two_calls_without_primary_judgment_in_verifier_input():
    candidates = [
        {"canonical_question_id": "a", "canonical_text": "泛Agent评分如何改进？"},
        {"canonical_question_id": "b", "canonical_text": "完整采集调用链以定位工具输出异常" + "长" * 500},
        {"canonical_question_id": "c", "canonical_text": "无关主题"},
    ]
    client = TwoStageClient(primary=Client(["c1", "c0", "c2"], grades={"c2": 0},
        evidence={"c1": {"object": "调用链", "focus": "定位工具输出异常"}},
        scopes={"c1": {"object_scope": "FLOW", "focus_scope": "TRACE"}}),
                            verdicts={"c0": True, "c1": False})
    logs = []
    rows = LLMReranker(client=client, model="test", on_call=logs.append).rerank("工具返回内容质量诊断", candidates)
    assert [row["canonical_question_id"] for row in rows] == ["b"]
    assert rows[0]["rerank_rank"] == 1
    assert rows[0]["relevance_grade"] == 2
    assert len(client.requests) == 2
    request = client.requests[1]
    body = json.loads(request["messages"][-1]["content"])
    assert body == {"query": "工具返回内容质量诊断", "candidates": [
        {"id": "c0", "question": candidates[1]["canonical_text"][:500]},
        {"id": "c1", "question": candidates[0]["canonical_text"]}]}
    schema = request["response_format"]["json_schema"]
    assert schema["strict"] is True and schema["name"] == "scope_verify_v5_design_unit_clarity_source_context"
    item_schema = schema["schema"]["$defs"]["CandidateVerify"]
    assert set(item_schema["properties"]) == {"candidate_id", "keep", "reason"}
    assert item_schema["additionalProperties"] is False
    assert item_schema["properties"]["keep"]["type"] == "boolean"
    assert item_schema["properties"]["reason"]["maxLength"] == 140
    assert schema["schema"]["properties"]["verifications"]["minItems"] == 2
    assert schema["schema"]["properties"]["verifications"]["maxItems"] == 2
    audit = rows.audit
    assert audit["scope_verifier_version"] == "scope_verify_v5_design_unit_clarity_source_context"
    assert audit["scope_verification_status"] == "COMPLETE"
    assert len(audit["candidates"]) == 3
    assert [item["decision"] for item in audit["candidates"]] == ["ACCEPTED", "SCOPE_REJECTED", "BELOW_THRESHOLD"]
    assert [item["scope_verification_status"] for item in audit["candidates"]] == [
        "CONFIRMED", "SCOPE_REJECTED", "NOT_REQUIRED"]
    assert audit["candidates"][1]["primary_diagnostic"]["decision"] == "ACCEPTED"
    assert audit["candidates"][1]["scope_verification_keep"] is False
    assert [log["operation_type"] for log in logs] == ["RERANK", "RERANK_VERIFY"]
    assert [log["prompt_version"] for log in logs] == ["rerank_v19_design_unit_source_context", "scope_verify_v5_design_unit_clarity_source_context"]
    assert [log["input_tokens"] for log in logs] == [100, 200]
    assert [log["output_tokens"] for log in logs] == [10, 20]
    assert [log["model_revision"] for log in logs] == ["resolved-primary", "resolved-verify"]


def test_public_rerank_skips_verifier_when_no_primary_acceptance():
    client = TwoStageClient(primary=Client(["c0"], grades={"c0": 0}))
    logs = []
    rows = LLMReranker(client=client, model="test", on_call=logs.append).rerank("OOM", [
        {"canonical_question_id": "a", "canonical_text": "无关主题"}])
    assert rows == [] and rows.audit["scope_verification_status"] == "NOT_REQUIRED"
    assert len(client.requests) == len(logs) == 1
    assert rows.audit["scope_invalid_candidate_count"] == 0


def test_public_empty_candidate_pool_is_audited_without_any_model_call_or_invented_quotes():
    client, logs = TwoStageClient(), []
    rows = LLMReranker(client=client, model="test", on_call=logs.append).rerank("无匹配候选", [])
    assert rows == [] and client.requests == logs == []
    assert rows.audit["scope_verifier_version"] == "scope_verify_v5_design_unit_clarity_source_context"
    assert rows.audit["scope_verification_status"] == "NOT_REQUIRED"
    assert rows.audit["candidate_verification_status"] == "COMPLETE"
    assert rows.audit["invalid_candidate_count"] == rows.audit["scope_invalid_candidate_count"] == 0
    assert rows.audit["candidates"] == []
    assert "query_object_evidence" not in rows.audit and "query_focus_evidence" not in rows.audit


@pytest.mark.parametrize("query,object_scope,focus_scope,expected_calls", [
    ("harness相关问题", "INSTANCE", "CORE", 2),
    ("Agent架构相关的面试问法", "SAME", "SAME", 2),
    ("工具成功调用后返回内容质量如何诊断", "PARENT", "ADJACENT", 1),
])
def test_supplied_scope_levels_preserve_framework_modules_but_not_narrow_output_adjacency(
        query, object_scope, focus_scope, expected_calls):
    """Model-supplied labels test host admissibility and call flow, not LLM understanding."""
    candidates = [
        {"canonical_question_id": "framework", "canonical_text": "Agent项目如何选择和使用运行框架？"},
        {"canonical_question_id": "context", "canonical_text": "智能助手如何压缩上下文并分层维护长短期记忆？"},
        {"canonical_question_id": "monitor", "canonical_text": "Agent运行时怎样设计监控和告警？"},
    ]
    client = TwoStageClient(primary=Client(["c0", "c1", "c2"], scopes={
        f"c{i}": {"object_scope": object_scope, "focus_scope": focus_scope} for i in range(3)}))
    logs = []
    rows = LLMReranker(client=client, model="test", on_call=logs.append).rerank(query, candidates)
    assert len(client.requests) == len(logs) == expected_calls
    if expected_calls == 2:
        assert [item["canonical_question_id"] for item in rows] == ["framework", "context", "monitor"]
        assert all(item["scope_verification_status"] == "CONFIRMED" for item in rows.audit["candidates"])
        assert all(item["decision"] == "ACCEPTED" for item in rows.audit["candidates"])
    else:
        assert rows == [] and rows.audit["scope_verification_status"] == "NOT_REQUIRED"
        assert all(item["decision"] == "BELOW_THRESHOLD" for item in rows.audit["candidates"])
    assert rows.audit["invalid_candidate_count"] == 0


def test_critic_false_is_a_scope_rejection_not_schema_failure_or_positive_fallback():
    client = TwoStageClient(verdicts={"c0": False})
    rows = LLMReranker(client=client, model="test").rerank("名额很快耗尽怎么办", [
        {"canonical_question_id": "a", "canonical_text": "并发超卖如何避免"}])
    assert rows == [] and rows.audit["candidate_verification_status"] == "COMPLETE"
    assert rows.audit["invalid_candidate_count"] == 0
    assert rows.audit["candidates"][0]["decision"] == "SCOPE_REJECTED"
    assert rows.audit["candidates"][0]["scope_verification_reason"]


@pytest.mark.parametrize("bad_keep", ["true", 1, None, [], {}])
def test_critic_strict_bool_bad_row_is_isolated_and_unique_invalid_ids_accumulate(bad_keep):
    client = TwoStageClient(primary=Client(["c0", "c1", "c2"], scopes={"c0": {"focus_scope": "ILLEGAL"}}),
                            verdicts={"c0": bad_keep, "c1": True})
    logs = []
    rows = LLMReranker(client=client, model="test", on_call=logs.append).rerank("工具输出质量诊断", [
        {"canonical_question_id": "a", "canonical_text": "工具输出质量诊断甲"},
        {"canonical_question_id": "b", "canonical_text": "工具输出质量诊断乙"},
        {"canonical_question_id": "c", "canonical_text": "完整调用链定位工具输出异常"},
    ])
    assert [row["canonical_question_id"] for row in rows] == ["c"]
    audit = rows.audit
    assert audit["primary_invalid_candidate_count"] == audit["scope_invalid_candidate_count"] == 1
    assert audit["invalid_candidate_count"] == 2 and audit["candidate_verification_status"] == "PARTIAL"
    assert audit["scope_verification_status"] == "PARTIAL"
    invalid = audit["candidates"][1]
    assert invalid["scope_verification_status"] == invalid["decision"] == "INVALID_SCHEMA"
    assert invalid["relevance_grade"] == 0 and invalid["quote_grounded"] is False
    assert invalid["object_quote_grounded"] is invalid["focus_quote_grounded"] is False
    assert "scope_verification_keep" not in invalid
    assert invalid["primary_diagnostic"]["relevance_grade"] == 3
    assert invalid["primary_diagnostic"]["quote_grounded"] is True
    assert invalid["schema_error_code"] == "ValidationError@v[0].keep:bool_type+1"
    assert [log["status"] for log in logs] == ["SUCCEEDED", "SUCCEEDED"]
    assert [log["error_code"] for log in logs] == ["PARTIAL_SCHEMA_REJECTED:1", "PARTIAL_SCHEMA_REJECTED:1"]


@pytest.mark.parametrize("bad_reason", ["", " " * 3, "字" * 141, None])
def test_critic_invalid_reason_never_keeps_candidate_or_defaults_it(bad_reason):
    def mutate(payload):
        payload["verifications"][0]["reason"] = bad_reason
    rows = LLMReranker(client=TwoStageClient(mutate_verifier=mutate), model="test").rerank("OOM", [
        {"canonical_question_id": "a", "canonical_text": "OOM定位"}])
    assert rows == [] and rows.audit["candidate_verification_status"] == "PARTIAL"
    assert rows.audit["invalid_candidate_count"] == 1
    assert rows.audit["candidates"][0]["decision"] == "INVALID_SCHEMA"


@pytest.mark.parametrize("failure", ["missing_global", "extra_global", "nondict_row", "missing_id", "unknown_id", "duplicate_id", "nonstr_id", "missing_row"])
def test_critic_global_or_identity_failure_is_fatal_without_primary_fallback(failure):
    def mutate(payload):
        if failure == "missing_global":
            del payload["verifications"]
        elif failure == "extra_global":
            payload["SECRET_FIELD"] = "SECRET_INPUT"
        elif failure == "nondict_row":
            payload["verifications"][0] = "invalid"
        elif failure == "missing_id":
            del payload["verifications"][0]["candidate_id"]
        elif failure == "unknown_id":
            payload["verifications"][0]["candidate_id"] = "invented"
        elif failure == "duplicate_id":
            payload["verifications"][0]["candidate_id"] = "c1"
        elif failure == "nonstr_id":
            payload["verifications"][0]["candidate_id"] = ["c0"]
        else:
            payload["verifications"].pop()
    client = TwoStageClient(primary=Client(["c0", "c1"]), mutate_verifier=mutate)
    logs = []
    with pytest.raises((ValidationError, ValueError)):
        LLMReranker(client=client, model="test", on_call=logs.append).rerank("OOM", [
            {"canonical_question_id": "a", "canonical_text": "OOM定位"},
            {"canonical_question_id": "b", "canonical_text": "OOM预防"}])
    assert len(client.requests) == 2
    assert [log["status"] for log in logs] == ["SUCCEEDED", "FAILED"]
    assert logs[1]["operation_type"] == "RERANK_VERIFY" and logs[1]["retry_count"] == 0
    assert "SECRET" not in logs[1]["error_code"]


@pytest.mark.parametrize("finish", ["stop", None, "length"])
def test_critic_stream_completion_usage_and_close_are_independent(finish):
    client = TwoStageClient(finish={"verify": finish})
    logs = []
    reranker = LLMReranker(client=client, model="test", stream=True, on_call=logs.append)
    candidates = [{"canonical_question_id": "a", "canonical_text": "OOM定位"}]
    if finish == "stop":
        assert reranker.rerank("OOM", candidates)[0]["canonical_question_id"] == "a"
        assert logs[1]["status"] == "SUCCEEDED"
    else:
        with pytest.raises(ValueError, match="STREAM_INCOMPLETE|MODEL_OUTPUT_TRUNCATED"):
            reranker.rerank("OOM", candidates)
        assert logs[1]["status"] == "FAILED"
    assert len(client.requests) == len(client.streams) == len(logs) == 2
    assert all(stream.closed for stream in client.streams)
    assert [log["output_tokens"] for log in logs] == [10, 20]
    assert all(request["stream_options"] == {"include_usage": True} for request in client.requests)


def test_two_stages_record_separate_timings_gate_calls_and_legacy_budget(monkeypatch):
    from interview_intelligence.providers.budget import CallBudget
    import interview_intelligence.search.reranker as module
    clock = [100.0]
    monkeypatch.setattr(module.time, "perf_counter", lambda: clock[0])
    class Gate:
        calls, active = 0, False
        @contextmanager
        def call(self):
            assert not self.active
            self.calls += 1
            self.active = True
            try:
                yield {"queue_ms": self.calls, "interval_ms": self.calls * 2}
            finally:
                self.active = False
    gate, budget, logs = Gate(), CallBudget(max_calls=6, max_tokens=32000), []
    underlying = TwoStageClient()
    def create(**kwargs):
        assert gate.active
        clock[0] += 3 if len(underlying.requests) else 10
        return underlying.create(**kwargs)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    LLMReranker(client=client, model="test", call_gate=gate, budget=budget, on_call=logs.append).rerank("OOM", [
        {"canonical_question_id": "a", "canonical_text": "OOM定位"}])
    assert gate.calls == budget.used_calls == 2 and not gate.active
    assert budget.used_tokens == 330
    assert [log["provider_ms"] for log in logs] == [10000, 3000]
    assert [log["latency_ms"] for log in logs] == [10000, 3000]
    assert [log["queue_ms"] for log in logs] == [1, 2]


@pytest.mark.parametrize("prior_calls,should_succeed", [(4, True), (5, False)])
def test_critic_obeys_shared_six_call_limit_and_settles_reservations(tmp_path, prior_calls, should_succeed):
    import time
    from interview_intelligence.providers.gate import ModelCallGate
    from interview_intelligence.providers.runtime import RequestLimits, current_limits
    limits = RequestLimits(deadline=time.monotonic() + 90, calls=prior_calls)
    client, logs = TwoStageClient(), []
    token = current_limits.set(limits)
    try:
        reranker = LLMReranker(client=client, model="test", call_gate=ModelCallGate(tmp_path / "gate", minimum_interval_seconds=0),
                              on_call=logs.append)
        if should_succeed:
            assert reranker.rerank("OOM", [{"canonical_question_id": "a", "canonical_text": "OOM定位"}])
        else:
            with pytest.raises(ValueError, match="QUERY_MODEL_BUDGET_EXCEEDED"):
                reranker.rerank("OOM", [{"canonical_question_id": "a", "canonical_text": "OOM定位"}])
    finally:
        current_limits.reset(token)
    assert limits.calls == 6 and limits.token_reservations == 0
    assert len(client.requests) == (2 if should_succeed else 1)
    assert len(logs) == 2
    assert logs[1]["operation_type"] == "RERANK_VERIFY"
    assert logs[1]["status"] == ("SUCCEEDED" if should_succeed else "FAILED")
    assert limits.tokens == sum(log.get("_token_charge", 0) for log in logs)


@pytest.mark.parametrize("primary_seconds", [85, 91])
def test_critic_uses_remaining_original_ninety_second_deadline(tmp_path, monkeypatch, primary_seconds):
    from interview_intelligence.providers.gate import ModelCallGate
    from interview_intelligence.providers.runtime import RequestLimits, current_limits
    import interview_intelligence.search.reranker as module
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    underlying = TwoStageClient()
    def create(**kwargs):
        if not underlying.requests:
            assert kwargs["timeout"] == 90
            response = underlying.create(**kwargs)
            clock[0] += primary_seconds
            return response
        assert kwargs["timeout"] == 5  # No new 90-second window for the critic.
        raise TimeoutError("simulated provider timeout at remaining deadline")
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    limits = RequestLimits(deadline=190)
    token = current_limits.set(limits)
    logs = []
    try:
        with pytest.raises((TimeoutError, ValueError), match="timeout|QUERY_DEADLINE_EXCEEDED"):
            LLMReranker(client=client, model="test", call_gate=ModelCallGate(tmp_path / "gate", minimum_interval_seconds=0),
                        on_call=logs.append).rerank("OOM", [{"canonical_question_id": "a", "canonical_text": "OOM定位"}])
    finally:
        current_limits.reset(token)
    assert len(underlying.requests) == 1 and limits.token_reservations == 0
    assert [log["status"] for log in logs] == ["SUCCEEDED", "FAILED"]


def test_runtime_usage_reconciliation_charges_each_stage_once(tmp_path):
    import time
    from interview_intelligence.providers.gate import ModelCallGate
    from interview_intelligence.providers.runtime import RequestLimits, current_limits
    limits = RequestLimits(deadline=time.monotonic() + 90)
    logs = []
    def record(entry):
        logs.append(entry)
        limits.reconcile_tokens(entry["_token_charge"], entry["input_tokens"] + entry["output_tokens"])
    token = current_limits.set(limits)
    try:
        assert LLMReranker(client=TwoStageClient(), model="test", on_call=record,
            call_gate=ModelCallGate(tmp_path / "gate", minimum_interval_seconds=0)).rerank("OOM", [
                {"canonical_question_id": "a", "canonical_text": "OOM定位"}])
    finally:
        current_limits.reset(token)
    assert limits.calls == 2 and limits.tokens == 330 and limits.token_reservations == 0
    assert len(logs) == 2 and all(log["_token_charge"] > 6144 for log in logs)


def test_critic_cannot_bypass_token_reservation_budget(tmp_path):
    import time
    from interview_intelligence.providers.gate import ModelCallGate
    from interview_intelligence.providers.runtime import RequestLimits, current_limits
    # Enough for the primary's reservation, insufficient for a second call.
    limits = RequestLimits(deadline=time.monotonic() + 90, max_tokens=7000)
    client, logs = TwoStageClient(), []
    token = current_limits.set(limits)
    try:
        with pytest.raises(ValueError, match="QUERY_TOKEN_BUDGET_EXCEEDED"):
            LLMReranker(client=client, model="test", on_call=logs.append,
                call_gate=ModelCallGate(tmp_path / "gate", minimum_interval_seconds=0)).rerank("OOM", [
                    {"canonical_question_id": "a", "canonical_text": "OOM定位"}])
    finally:
        current_limits.reset(token)
    assert len(client.requests) == limits.calls == 1
    assert len(logs) == 2 and logs[1]["status"] == "FAILED"
    assert limits.token_reservations == 0 and limits.tokens == logs[0]["_token_charge"]
