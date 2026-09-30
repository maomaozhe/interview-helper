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
        return response([decision("b", "RELATED"), decision("a", "SAME")])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    judge = OpenAICompatibleJudge(client=client, model="ark-code-latest", on_call=logs.append)
    result = judge.judge_many("Redis为什么快？", [("a", "Redis为何高性能？"), ("b", "Redis为什么单线程？")])
    assert result["a"].decision == "SAME"
    assert result["b"].decision == "RELATED"
    assert len(calls) == 1
    assert calls[0]["response_format"]["json_schema"]["strict"] is True
    assert json.loads(calls[0]["messages"][1]["content"])["candidates"][1]["candidate_id"] == "b"
    assert logs[0]["model"] == "ark-code-latest"
    assert logs[0]["model_revision"] == "ark-model-revision-test"


@pytest.mark.parametrize("bad_decisions", [
    [decision("a", "SAME")],
    [decision("a", "SAME"), decision("c", "DIFFERENT")],
    [decision("a", "SAME"), decision("a", "DIFFERENT")],
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
                        [decision("a", "SAME"), decision("b", "RELATED")])

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
