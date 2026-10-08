import importlib
import json
from types import SimpleNamespace

import pytest


def test_judge_uses_strict_three_way_schema():
    mod = importlib.import_module("interview_intelligence.dedup.provider")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        content = json.dumps({"decision": "RELATED", "reason_code": "different_scope", "confidence": 0.9})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    judge = mod.OpenAICompatibleJudge(client=client, model="test-model", sleep=lambda _: None)
    decision = judge.judge("Redis为什么快？", "Redis为什么单线程？")
    assert decision.decision == "RELATED"
    assert decision.reason_code == "different_scope"
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[0]["response_format"]["json_schema"]["strict"] is True


def test_encoder_rejects_wrong_vector_dimension():
    mod = importlib.import_module("interview_intelligence.dedup.provider")
    client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **_: SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2])],
    )))
    encoder = mod.OpenAICompatibleEncoder(client=client, model="test-embedding", dimension=3)
    with pytest.raises(ValueError, match="dimension"):
        encoder.embed("Redis为什么快？")


def test_embedding_usage_releases_generation_reservation_and_keeps_unknown_input():
    from eval.budget import EvaluationBudget
    from interview_intelligence.dedup.provider import OpenAICompatibleEncoder
    calls = []
    budget = EvaluationBudget(3, 25000)
    known = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **_: SimpleNamespace(
        data=[SimpleNamespace(embedding=[1.0, 0.0])], usage=SimpleNamespace(prompt_tokens=7))))
    encoder = OpenAICompatibleEncoder(client=known, model="fixture", dimension=2, budget=budget, on_call=calls.append)
    encoder.embed("first"); encoder.embed("second")
    assert budget.used_calls == 2 and budget.used_tokens == 14
    assert calls[-1]["output_tokens"] == 0
    unknown = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **_: SimpleNamespace(
        data=[SimpleNamespace(embedding=[1.0, 0.0])], usage=None)))
    encoder.client = unknown
    encoder.embed("third")
    assert budget.used_tokens > 16000


def test_same_equivalence_guard_blocks_unresolved_scope_with_high_initial_confidence():
    from interview_intelligence.dedup.provider import OpenAICompatibleJudge
    results=[{"decision":"SAME","reason_code":"same_topic","confidence":.99},
        {"same_subject":True,"same_operation":True,"same_answer_scope":False,"compatible_constraints":True,
         "referents_resolved":False,"reason_code":"missing_context"}]
    calls=[]
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(results.pop(0))))])
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    judge=OpenAICompatibleJudge(client=client,model="fixture",verify_equivalence=True,max_attempts=1)
    result=judge.judge("这个任务怎么并行", "线程池怎样并行执行任务")
    assert result.decision=="RELATED" and result.confidence<=.5 and len(calls)==2
    assert judge.version=="dedup_judge_v8_normalized_scope"
    assert calls[0]["messages"][0]["content"] != calls[1]["messages"][0]["content"]
