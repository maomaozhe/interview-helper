import json
import time
from contextlib import closing
from copy import deepcopy
from types import SimpleNamespace

from openai.types.chat import ChatCompletionChunk
import pytest

from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.providers.qwen_identity import QwenIdentityClient, INVALID_RESPONSE
from interview_intelligence.providers.runtime import RequestLimits, current_limits
from interview_intelligence.search.reranker import LLMReranker, RerankResult, ScopeVerifyResult, CandidateRank


def schema(field, count):
    model, definition = (RerankResult, "CandidateRank") if field == "rankings" else (ScopeVerifyResult, "CandidateVerify")
    value = model.model_json_schema()
    value["$defs"][definition]["properties"]["candidate_id"]["enum"] = [f"c{i}" for i in range(count)]
    value["properties"][field].update(minItems=count, maxItems=count)
    return value


def row():
    value = {"candidate_task": "KNOWLEDGE", "task_evidence": "Redis",
        "object_evidence": "Redis", "focus_evidence": "缓存"}
    if "object_scope" in CandidateRank.model_fields:
        value.update(object_scope="SAME", focus_scope="SAME")
    else:
        value.update(object_relation="EXPLICIT", focus_relation="DIRECT")
    return value


def mapped(field="rankings", count=2):
    if field == "verifications":
        return {field: {f"c{i}": {"keep": True, "reason": "题干明确缓存"} for i in range(count)}}
    result = {"query_task": "OTHER", "query_task_evidence": "Redis", "query_object": "Redis", "query_focus": "缓存",
        field: {f"c{i}": {**row(), "rank": count - i} for i in range(count)}}
    if "query_object_evidence" in RerankResult.model_fields:
        result.update(query_object_evidence="Redis", query_focus_evidence="缓存")
    return result


class Stream:
    def __init__(self, raw, finish="stop", before_chunk=None):
        self.raw, self.finish, self.before_chunk = raw, finish, before_chunk
        self.closed, self.yielded = False, 0

    def __iter__(self):
        for index, part in enumerate((self.raw[:8], self.raw[8:])):
            self.yielded += 1
            if self.before_chunk:
                self.before_chunk(index)
            yield ChatCompletionChunk(id="reply", created=1, model="qwen3-test", object="chat.completion.chunk",
                choices=[{"index": 0, "delta": {"content": part}, "finish_reason": self.finish if index else None}])
        yield ChatCompletionChunk(id="reply", created=1, model="qwen3-test", object="chat.completion.chunk",
            choices=[], usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})

    def close(self):
        self.closed = True


class Client:
    def __init__(self, raw=None, finish="stop", producer=None):
        self.raw, self.finish, self.producer = raw, finish, producer
        self.calls, self.streams = [], []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **payload):
        self.calls.append(payload)
        raw = self.producer(payload) if self.producer else self.raw
        stream = Stream(raw, self.finish)
        self.streams.append(stream)
        return stream


def request(client, field="rankings", count=2):
    return QwenIdentityClient(client).chat.completions.create(model="qwen3-test", stream=True,
        messages=[{"role": "system", "content": "Original evidence rules."}],
        response_format={"type": "json_schema", "json_schema": {"name": "rerank_test" if field == "rankings" else "scope_verify_test",
            "strict": True, "schema": schema(field, count)}})


def test_keyed_identity_preserves_all_50_rank_order_schema_and_real_stream_usage():
    original = Client(json.dumps(mapped(count=50), ensure_ascii=False))
    with closing(request(original, count=50)) as response:
        chunks = list(response)
    body = original.calls[0]
    wire = body["response_format"]["json_schema"]["schema"]
    assert len(wire["properties"]["rankings"]["required"]) == 50
    assert wire["properties"]["rankings"]["additionalProperties"] is False
    assert "candidate_id" not in wire["$defs"]["CandidateRank"]["properties"]
    assert "rank" in wire["$defs"]["CandidateRank"]["required"]
    assert body["messages"][0]["content"].startswith("Original evidence rules.")
    assert len(original.calls) == 1 and original.streams[0].closed
    assert all(not chunk.choices for chunk in chunks[:-1])
    complete = chunks[-1]
    assert complete.model == "qwen3-test"
    assert complete.usage.total_tokens == 150 and complete.choices[0].finish_reason == "stop"
    parsed = json.loads(complete.choices[0].delta.content)
    assert [item["candidate_id"] for item in parsed["rankings"]] == [f"c{i}" for i in reversed(range(50))]
    assert parsed["rankings"][0]["object_evidence"] == "Redis"
    assert "rank" not in parsed["rankings"][0]


def test_scope_keyed_identity_preserves_keep_reason_and_alias_coverage():
    value = mapped("verifications")
    value["verifications"]["c1"]["keep"] = False
    original = Client(json.dumps(value, ensure_ascii=False))
    chunks = list(request(original, "verifications"))
    assert json.loads(chunks[-1].choices[0].delta.content)["verifications"] == [
        {"candidate_id": "c0", "keep": True, "reason": "题干明确缓存"},
        {"candidate_id": "c1", "keep": False, "reason": "题干明确缓存"}]
    assert "rank" not in original.calls[0]["response_format"]["json_schema"]["schema"]["$defs"]["CandidateVerify"]["properties"]


@pytest.mark.parametrize("mutation", ["missing", "extra", "bad_rank", "bool_rank", "id_override"])
def test_invalid_identity_maps_fail_closed_without_retry_or_provider_text(mutation):
    value = mapped()
    rows = value["rankings"]
    if mutation == "missing":
        rows.pop("c1")
    elif mutation == "extra":
        rows["private-provider-key"] = deepcopy(rows["c0"])
    elif mutation == "bad_rank":
        rows["c0"]["rank"] = 0
    elif mutation == "bool_rank":
        rows["c0"]["rank"] = True
    elif mutation == "id_override":
        rows["c0"]["candidate_id"] = "c1"
    original = Client(json.dumps(value, ensure_ascii=False))
    with pytest.raises(ValueError, match="^" + INVALID_RESPONSE + "$"):
        list(request(original))
    assert len(original.calls) == 1 and original.streams[0].closed


def test_duplicate_json_aliases_are_not_silently_overwritten():
    value = mapped(count=1)
    one = json.dumps(value["rankings"]["c0"])
    value["rankings"] = "replace-marker"
    raw = json.dumps(value).replace('"replace-marker"', '{"c0":' + one + ',"c0":' + one + '}')
    original = Client(raw)
    with pytest.raises(ValueError, match="^" + INVALID_RESPONSE + "$"):
        list(request(original, count=1))
    assert len(original.calls) == 1 and original.streams[0].closed


@pytest.mark.parametrize("finish", ["length", None])
def test_length_and_incomplete_finish_are_preserved_for_host_fail_closed_checks(finish):
    original = Client('{"rankings":', finish)
    chunks = list(request(original))
    assert chunks[-1].choices[0].finish_reason == finish
    assert chunks[-1].choices[0].delta.content == ""
    assert chunks[-1].usage.total_tokens == 150 and original.streams[0].closed


@pytest.mark.parametrize("cancel", [True, False])
def test_buffering_checks_cancel_and_deadline_each_chunk_and_closes_without_output(cancel):
    limits = RequestLimits(time.monotonic() + 20)
    original = Client(json.dumps(mapped(), ensure_ascii=False))
    response = request(original)
    def interrupt(index):
        if index == 1:
            if cancel:
                limits.cancelled.set()
            else:
                limits.deadline = time.monotonic() - 1
    original.streams[0].before_chunk = interrupt
    token = current_limits.set(limits)
    try:
        iterator = iter(response)
        assert next(iterator).choices == []
        with pytest.raises(ValueError, match="^QUERY_CANCELLED$" if cancel else "^QUERY_DEADLINE_EXCEEDED$"):
            next(iterator)
        assert original.streams[0].yielded == 2 and original.streams[0].closed
    finally:
        current_limits.reset(token)


def test_adapter_keeps_real_reranker_rules_all_50_and_two_calls_without_relaxing_quotes(tmp_path):
    def produce(payload):
        value = json.loads(payload["messages"][-1]["content"])
        field = "rankings" if payload["response_format"]["json_schema"]["name"].startswith("rerank_") else "verifications"
        return json.dumps(mapped(field, len(value["candidates"])), ensure_ascii=False)
    original = Client(producer=produce)
    reranker = LLMReranker(model="qwen3-test", client=QwenIdentityClient(original), stream=True,
        call_gate=ModelCallGate(tmp_path / "gate", minimum_interval_seconds=0))
    candidates = [{"canonical_question_id": str(i), "canonical_text": f"Redis 缓存{i}"} for i in range(50)]
    limits = RequestLimits(time.monotonic() + 20, max_tokens=96000)
    token = current_limits.set(limits)
    try:
        ranked = reranker.rerank("Redis 缓存", candidates)
        assert len(ranked) == 50 and {item["canonical_question_id"] for item in ranked} == {str(i) for i in range(50)}
        assert [item["canonical_question_id"] for item in ranked] == [str(i) for i in reversed(range(50))]
        assert limits.calls == 2 and len(original.calls) == 2
        assert ranked.audit["scope_verification_status"] == "COMPLETE"
    finally:
        current_limits.reset(token)
    def invented(payload):
        result = mapped(count=1)
        result["rankings"]["c0"]["object_evidence"] = "invented-private-provider-quote"
        return json.dumps(result, ensure_ascii=False)
    ungrounded = Client(producer=invented)
    filtered = LLMReranker(model="qwen3-test", client=QwenIdentityClient(ungrounded), stream=True).rerank(
        "Redis 缓存", candidates[:1])
    assert filtered == [] and len(ungrounded.calls) == 1
    assert filtered.audit["candidates"][0]["quote_grounded"] is False


@pytest.mark.parametrize("invalid", ["bad_enum", "unknown_field"])
def test_r14_still_excludes_only_malformed_rows_and_returns_partial(invalid):
    def produce(payload):
        field = "rankings" if payload["response_format"]["json_schema"]["name"].startswith("rerank_") else "verifications"
        value = json.loads(payload["messages"][-1]["content"])
        result = mapped(field, len(value["candidates"]))
        if field == "rankings":
            if invalid == "bad_enum":
                result[field]["c0"]["candidate_task"] = "private-provider-key"
            else:
                result[field]["c0"]["private-provider-key"] = "secret"
        return json.dumps(result, ensure_ascii=False)
    original = Client(producer=produce)
    result = LLMReranker(model="qwen3-test", client=QwenIdentityClient(original), stream=True).rerank(
        "Redis 缓存", [{"canonical_question_id": "a", "canonical_text": "Redis 缓存A"},
                      {"canonical_question_id": "b", "canonical_text": "Redis 缓存B"}])
    assert [item["canonical_question_id"] for item in result] == ["b"]
    assert result.audit["candidate_verification_status"] == "PARTIAL"
    assert result.audit["invalid_candidate_count"] == 1
    invalid_row = next(row for row in result.audit["candidates"] if row["canonical_question_id"] == "a")
    assert invalid_row["decision"] == "INVALID_SCHEMA"
    assert "private-provider-key" not in invalid_row["schema_error_code"]


def test_conversion_failure_preserves_true_usage_model_and_closes_without_retry():
    bad = mapped()
    bad["rankings"].pop("c1")
    original = Client(json.dumps(bad, ensure_ascii=False))
    telemetry = []
    reranker = LLMReranker(model="qwen3-test", client=QwenIdentityClient(original), stream=True, on_call=telemetry.append)
    with pytest.raises(ValueError, match="^" + INVALID_RESPONSE + "$"):
        reranker._rank_candidates("Redis 缓存", [{"canonical_question_id": "a", "canonical_text": "Redis 缓存A"},
            {"canonical_question_id": "b", "canonical_text": "Redis 缓存B"}])
    assert len(original.calls) == 1 and original.streams[0].closed
    assert telemetry[0]["input_tokens"] == 100 and telemetry[0]["output_tokens"] == 50
    assert telemetry[0]["model_revision"] == "qwen3-test" and telemetry[0]["status"] == "FAILED"


def test_unrelated_response_formats_pass_through_unchanged():
    original = Client("plain")
    response = QwenIdentityClient(original).chat.completions.create(model="qwen3", stream=True,
        response_format={"type": "json_object"}, messages=[])
    assert response is original.streams[0]
    assert original.calls[0]["response_format"] == {"type": "json_object"}


def test_non_stream_response_and_rank_ties_are_deterministic_without_mutating_request():
    value = mapped()
    value["rankings"]["c0"]["rank"] = value["rankings"]["c1"]["rank"] = 1
    usage = SimpleNamespace(total_tokens=123)
    upstream = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
        message=SimpleNamespace(content=json.dumps(value, ensure_ascii=False)))], usage=usage, model="qwen3-test")
    seen = []
    def create(**payload):
        seen.append(payload)
        return upstream
    original = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    payload = {"stream": False, "messages": [{"role": "system", "content": "Original rules."}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "rerank_test", "schema": schema("rankings", 2)}}}
    saved = deepcopy(payload)
    response = QwenIdentityClient(original).chat.completions.create(**payload)
    assert payload == saved and len(seen) == 1
    assert response.usage is usage and upstream.choices[0].message.content == json.dumps(value, ensure_ascii=False)
    assert [row["candidate_id"] for row in json.loads(response.choices[0].message.content)["rankings"]] == ["c0", "c1"]


def test_routing_only_adapts_system_qwen_and_preserves_shared_encoder_and_personal_client(monkeypatch):
    from interview_intelligence import tenant_retriever as module
    from interview_intelligence.tenant import current_provider
    from interview_intelligence.search.elasticsearch import ElasticsearchRetriever
    clients = []
    def openai(**options):
        client = Client("unused")
        clients.append(client)
        return client
    monkeypatch.setattr(module, "OpenAI", openai)
    monkeypatch.setattr(module.httpx, "Client", lambda **options: SimpleNamespace())
    monkeypatch.setattr(module, "public_sync_transport", lambda: object())
    encoder, es = object(), object()
    base = ElasticsearchRetriever("", encoder, client=es)
    proxy = module.TenantRetriever(base, None, "tenant", None, None)
    settings = SimpleNamespace(model_api_key="secret", model_base_url="https://api.example.test/v1",
        reranker_model="qwen3-8b-interview", model_request_timeout_seconds=180)
    token = current_provider.set({"source": "system", "settings": settings})
    try:
        system = proxy.selected()
        assert isinstance(system.reranker.client, QwenIdentityClient)
        assert system.encoder is encoder and system.client is es
        assert proxy.selected() is system
        current_provider.set({"source": "personal", "settings": settings})
        personal = proxy.selected()
        assert isinstance(personal.reranker, module.PersonalReranker)
        assert personal.reranker.client is clients[1] and not isinstance(personal.reranker.client, QwenIdentityClient)
        assert personal.encoder is encoder and personal.client is es
        current_provider.set({"source": "system", "settings": SimpleNamespace(**{
            **vars(settings), "reranker_model": "other-model"})})
        other = proxy.selected()
        assert other.reranker.client is clients[2] and not isinstance(other.reranker.client, QwenIdentityClient)
        assert len(clients) == 3
    finally:
        current_provider.reset(token)
