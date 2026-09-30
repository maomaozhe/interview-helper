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
