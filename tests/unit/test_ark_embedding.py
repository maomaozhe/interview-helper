from interview_intelligence.dedup.provider import ArkMultimodalEncoder


class Response:
    def raise_for_status(self):
        pass

    def json(self):
        return {"data": {"embedding": [0.6, 0.8]}}


class Client:
    def __init__(self):
        self.calls = []

    def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return Response()


def test_ark_vision_embedding_uses_symmetric_semantic_similarity_instruction():
    client = Client()
    encoder = ArkMultimodalEncoder(model="doubao-embedding-vision", dimension=2,
                                   api_key="local-test", client=client)
    assert encoder.embed_query("Redis") == [0.6, 0.8]
    assert encoder.embed("Redis") == [0.6, 0.8]
    assert all(path == "/embeddings/multimodal" for path, _ in client.calls)
    assert client.calls[0][1]["json"]["input"] == [{"type": "text", "text": "Redis"}]
    assert client.calls[0][1]["json"]["instructions"] == client.calls[1][1]["json"]["instructions"]
    assert client.calls[0][1]["json"]["instructions"] == (
        "Target_modality: text.\nInstruction: Retrieve semantically similar interview questions.\nQuery:"
    )
    assert encoder.version.endswith(":instructions_v2")


def test_ark_rejects_unsupported_dimension_before_real_client_creation():
    import pytest
    with pytest.raises(ValueError, match="1024 or 2048"):
        ArkMultimodalEncoder(model="doubao-embedding-vision", dimension=3,
                             api_key="local-test", base_url="https://example.invalid/v3")
