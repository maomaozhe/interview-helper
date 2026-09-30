from types import SimpleNamespace

import pytest

from interview_intelligence.dedup.provider import ArkMultimodalEncoder, OpenAICompatibleEncoder


class Response:
    def raise_for_status(self):
        pass

    def json(self):
        return {"model": "embedding-revision-test", "data": {"embedding": [0.6, 0.8]},
                "usage": {"prompt_tokens": 11}}


@pytest.mark.parametrize("kind", ["openai", "ark"])
def test_embedding_records_response_usage_and_model_revision(kind):
    logs = []
    if kind == "openai":
        client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **_: SimpleNamespace(
            model="embedding-revision-test", usage=SimpleNamespace(prompt_tokens=11),
            data=[SimpleNamespace(embedding=[0.6, 0.8])],
        )))
        item = OpenAICompatibleEncoder(model="embedding-test", dimension=2, client=client,
                                        on_call=logs.append)
    else:
        item = ArkMultimodalEncoder(model="embedding-test", dimension=2, api_key="local-test",
                                    client=SimpleNamespace(post=lambda *args, **kwargs: Response()),
                                    on_call=logs.append)
    assert item.embed("Redis") == [0.6, 0.8]
    assert len(logs) == 1
    assert logs[0]["operation_type"] == "EMBEDDING"
    assert logs[0]["model"] == "embedding-test"
    assert logs[0]["model_revision"] == "embedding-revision-test"
    assert logs[0]["input_tokens"] == 11
    assert logs[0]["output_tokens"] is None
    assert logs[0]["prompt_version"] == item.version
    assert logs[0]["status"] == "SUCCEEDED"
    assert logs[0]["latency_ms"] >= 0


@pytest.mark.parametrize("kind", ["openai", "ark"])
def test_embedding_records_failed_requests_without_masking_exception(kind):
    logs = []

    def fail(*args, **kwargs):
        raise RuntimeError("local request failure")

    if kind == "openai":
        item = OpenAICompatibleEncoder(model="embedding-test", dimension=2,
                                        client=SimpleNamespace(embeddings=SimpleNamespace(create=fail)),
                                        on_call=logs.append)
    else:
        item = ArkMultimodalEncoder(model="embedding-test", dimension=2, api_key="local-test",
                                    client=SimpleNamespace(post=fail), on_call=logs.append)
    with pytest.raises(RuntimeError, match="local request failure"):
        item.embed("Redis")
    assert len(logs) == 1
    assert logs[0]["status"] == "FAILED"
    assert logs[0]["error_code"] == "RuntimeError"
    assert logs[0]["input_tokens"] is None
