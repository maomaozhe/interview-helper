import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from interview_intelligence.dedup.provider import OpenAICompatibleJudge


def response(decisions):
    return SimpleNamespace(model="ark-model-revision-test", choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps({"decisions": decisions})))])


def decision(candidate_id, label):
    return {"candidate_id": candidate_id, "decision": label,
            "reason_code": "different_scope" if label == "RELATED" else "scope_matches", "confidence": 0.9}


def test_judge_many_decides_exact_candidate_set_in_one_strict_call():
    calls, logs = [], []

    def create(**kwargs):
        calls.append(kwargs)
        return response([decision("c1", "RELATED"), decision("c0", "SAME")])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    judge = OpenAICompatibleJudge(client=client, model="ark-code-latest", on_call=logs.append)
    result = judge.judge_many("Redis为什么快？", [("a", "Redis为何高性能？"), ("b", "Redis为什么单线程？")])
    assert result["a"].decision == "SAME"
    assert result["b"].decision == "RELATED"
    assert len(calls) == 1
    assert calls[0]["response_format"]["json_schema"]["strict"] is True
    assert json.loads(calls[0]["messages"][1]["content"])["candidates"][1]["candidate_id"] == "c1"
    assert logs[0]["model"] == "ark-code-latest"
    assert logs[0]["model_revision"] == "ark-model-revision-test"


@pytest.mark.parametrize("bad_decisions", [
    [decision("c0", "SAME")],
    [decision("c0", "SAME"), decision("c2", "DIFFERENT")],
    [decision("c0", "SAME"), decision("c0", "DIFFERENT")],
])
def test_judge_many_retries_invalid_ids_under_gate(bad_decisions):
    state = {"active": False, "gate_calls": 0, "requests": 0}

    class Gate:
        @contextmanager
        def call(self):
            state["active"] = True
            state["gate_calls"] += 1
            try:
                yield
            finally:
                state["active"] = False

    def create(**kwargs):
        assert state["active"]
        state["requests"] += 1
        return response(bad_decisions if state["requests"] == 1 else
                        [decision("c0", "SAME"), decision("c1", "RELATED")])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    judge = OpenAICompatibleJudge(client=client, model="local-test", max_attempts=2,
                                   sleep=lambda _: None, call_gate=Gate())
    result = judge.judge_many("Redis", [("a", "Redis 性能"), ("b", "Redis 单线程")])
    assert set(result) == {"a", "b"}
    assert state["gate_calls"] == state["requests"] == 2


def test_judge_many_empty_and_duplicate_candidates_do_not_call_network():
    def create(**kwargs):
        raise AssertionError("no network call expected")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    judge = OpenAICompatibleJudge(client=client, model="local-test")
    assert judge.judge_many("Redis", []) == {}
    with pytest.raises(ValueError, match="duplicate"):
        judge.judge_many("Redis", [("a", "Redis"), ("a", "Redis 单线程")])


def test_batch_schema_binds_short_ids_and_count_to_each_request():
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        candidates = json.loads(kwargs["messages"][1]["content"])["candidates"]
        return response([decision(item["candidate_id"], "DIFFERENT") for item in reversed(candidates)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    judge = OpenAICompatibleJudge(client=client, model="local-test", max_attempts=1)
    first_ids = ["23e55680-efc7-41d3-b6a7-9bc7aff9c252", "36f70ed5-f899-432a-8b24-4291a92e62a2"]
    first = judge.judge_many("Redis为什么快？", [(key, f"候选 {index}") for index, key in enumerate(first_ids)])
    second = judge.judge_many("事务隔离级别？", [("third-canonical-id", "隔离级别有哪些？")])
    assert set(first) == set(first_ids)
    assert set(second) == {"third-canonical-id"}
    for request, expected_aliases in zip(calls, (["c0", "c1"], ["c0"])):
        schema = request["response_format"]["json_schema"]["schema"]
        items = schema["properties"]["decisions"]
        definition = schema["$defs"]["CandidateJudgeDecision"]
        assert definition["properties"]["candidate_id"]["enum"] == expected_aliases
        assert items["minItems"] == items["maxItems"] == len(expected_aliases)
        user_input = json.loads(request["messages"][1]["content"])
        assert [item["candidate_id"] for item in user_input["candidates"]] == expected_aliases
    assert all(key not in json.dumps(calls) for key in first_ids)


def test_invalid_batch_retry_gets_explicit_correction_without_accepting_partial_result():
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return response([decision("c0", "SAME")] if len(calls) == 1 else
                        [decision("c1", "RELATED"), decision("c0", "SAME")])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    judge = OpenAICompatibleJudge(client=client, model="local-test", max_attempts=2, sleep=lambda _: None)
    result = judge.judge_many("Redis", [("first-id", "Redis性能"), ("second-id", "Redis单线程")])
    assert result["first-id"].decision == "SAME"
    assert result["second-id"].decision == "RELATED"
    assert calls[0]["messages"] != calls[1]["messages"]
    correction = calls[1]["messages"][-1]["content"]
    assert "c0" in correction and "c1" in correction
    assert "first-id" not in correction and "second-id" not in correction
