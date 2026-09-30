import pytest

from interview_intelligence.dedup.provider import (
    ArkMultimodalEncoder, OpenAICompatibleEncoder, OpenAICompatibleJudge,
)
from interview_intelligence.extraction.provider import OpenAICompatibleExtractor
from interview_intelligence.search.reranker import LLMReranker


@pytest.mark.parametrize("adapter,extra", [
    (OpenAICompatibleExtractor, {}),
    (OpenAICompatibleEncoder, {"dimension": 2}),
    (ArkMultimodalEncoder, {"dimension": 1024}),
    (OpenAICompatibleJudge, {}),
    (LLMReranker, {}),
])
def test_model_clients_connect_directly_despite_system_socks_proxy(monkeypatch, adapter, extra):
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "socks5://127.0.0.1:1")
    item = adapter(model="local-test", api_key="local-test",
                   base_url="https://example.invalid/v3", **extra)
    item.client.close()


@pytest.mark.parametrize("adapter,extra", [
    (OpenAICompatibleExtractor, {}),
    (OpenAICompatibleEncoder, {"dimension": 2}),
    (ArkMultimodalEncoder, {"dimension": 1024}),
    (OpenAICompatibleJudge, {}),
    (LLMReranker, {}),
])
def test_model_clients_allow_slow_serial_responses(adapter, extra):
    item = adapter(model="local-test", api_key="local-test",
                   base_url="https://example.invalid/v3", timeout_seconds=180, **extra)
    if isinstance(item, ArkMultimodalEncoder):
        assert item.client.timeout.read == 180
    else:
        assert item.client.timeout == 180
    item.client.close()
