import importlib
import json
import multiprocessing
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from interview_intelligence.dedup.provider import (
    ArkMultimodalEncoder, OpenAICompatibleEncoder, OpenAICompatibleJudge,
)
from interview_intelligence.extraction.provider import OpenAICompatibleExtractor
from interview_intelligence.search.reranker import LLMReranker


class CheckingGate:
    def __init__(self):
        self.active = False
        self.calls = 0

    @contextmanager
    def call(self):
        assert not self.active
        self.active = True
        self.calls += 1
        try:
            yield
        finally:
            self.active = False


def test_extract_and_judge_acquire_gate_for_every_retry():
    for adapter, invoke in [
        (OpenAICompatibleExtractor, lambda item: item.extract(text="面经", revision_id="r1")),
        (OpenAICompatibleJudge, lambda item: item.judge("Redis 快", "Redis 单线程")),
    ]:
        gate = CheckingGate()

        def create(**kwargs):
            assert gate.active
            raise RuntimeError("local request failure")

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        item = adapter(client=client, model="local-test", max_attempts=2,
                       sleep=lambda _: None, call_gate=gate)
        with pytest.raises(RuntimeError, match="local request failure"):
            invoke(item)
        assert gate.calls == 2
        assert not gate.active


def test_embedding_and_rerank_hold_gate_during_network_request():
    gate = CheckingGate()

    def create_embedding(**kwargs):
        assert gate.active
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.6, 0.8])])

    encoder = OpenAICompatibleEncoder(
        model="local-test", dimension=2, call_gate=gate,
        client=SimpleNamespace(embeddings=SimpleNamespace(create=create_embedding)),
    )
    assert encoder.embed("Redis") == [0.6, 0.8]

    def create_rerank(**kwargs):
        assert gate.active
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"ordered_ids": ["a"]}),
        ))])

    reranker = LLMReranker(
        model="local-test", call_gate=gate,
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_rerank))),
    )
    assert reranker.rerank("Redis", [{"canonical_question_id": "a"}])[0]["rerank_rank"] == 1

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"embedding": [0.6, 0.8]}}

    def post(*args, **kwargs):
        assert gate.active
        return Response()

    ark = ArkMultimodalEncoder(model="local-test", dimension=2, api_key="local-test",
                               client=SimpleNamespace(post=post), call_gate=gate)
    assert ark.embed_query("Redis") == [0.6, 0.8]
    assert gate.calls == 3
    assert not gate.active


def _gate_class():
    spec = importlib.util.find_spec("interview_intelligence.providers.gate")
    assert spec is not None, "a shared model call gate must be implemented"
    return importlib.import_module("interview_intelligence.providers.gate").ModelCallGate


def test_gate_enforces_interval_across_instances_after_failed_request(tmp_path):
    gate_type = _gate_class()
    path = tmp_path / "model-call.lock"
    first = gate_type(path, minimum_interval_seconds=0.05)
    second = gate_type(path, minimum_interval_seconds=0.05)
    with pytest.raises(RuntimeError):
        with first.call():
            finished_at = time.time()
            raise RuntimeError("failed network request")
    with second.call():
        assert time.time() - finished_at >= 0.045
    assert gate_type(path).minimum_interval_seconds == 2
    with pytest.raises(ValueError):
        gate_type(path, minimum_interval_seconds=-1)


def test_gate_serializes_different_instances_in_threads(tmp_path):
    gate_type = _gate_class()
    path = tmp_path / "model-call.lock"
    active = 0
    max_active = 0
    errors = []

    def run():
        nonlocal active, max_active
        try:
            with gate_type(path, minimum_interval_seconds=0).call():
                active += 1
                max_active = max(max_active, active)
                time.sleep(0.015)
                active -= 1
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert all(not thread.is_alive() for thread in threads)
    assert not errors
    assert max_active == 1


def _process_call(lock_path, ready, events):
    from interview_intelligence.providers.gate import ModelCallGate
    ready.wait(timeout=5)
    with ModelCallGate(lock_path, minimum_interval_seconds=0.05).call():
        start = time.time()
        time.sleep(0.04)
        events.put((start, time.time()))


def test_gate_serializes_separate_processes(tmp_path):
    _gate_class()
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    events = context.Queue()
    processes = [context.Process(target=_process_call, args=(str(tmp_path / "model-call.lock"), ready, events))
                 for _ in range(3)]
    for process in processes:
        process.start()
    ready.set()
    intervals = sorted(events.get(timeout=10) for _ in processes)
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    for previous, current in zip(intervals, intervals[1:]):
        assert current[0] - previous[1] >= 0.045
