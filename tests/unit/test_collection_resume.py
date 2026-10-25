"""Crash boundaries must preserve evidence and budgets without replaying paid calls."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from eval.budget import EvaluationBudget
from eval.checkpoint import CollectionCheckpoint
from eval.collect import collect_durable
from eval.common import digest, read_json, write_json
from eval.validate_gold import GoldValidationError


def identity(adapter="provider", ids=("a", "b")):
    return {"adapter": adapter, "section": "extraction", "split": "dev", "review_policy": "human",
            "snapshot": {"corpus_revision": 1}, "sample_ids": list(ids), "dataset_sha256": "dataset",
            "selection_sha256": digest(list(ids)), "settings_sha256": "settings", "options_sha256": "options",
            "provenance": {"source_sha256": "source"}, "candidate_binding": None}


def state(output):
    envelope = read_json(output.with_suffix(".checkpoint.json"))
    assert digest(envelope["state"]) == envelope["sha256"]
    return envelope["state"]


def bind(journal):
    journal.bind_snapshot(journal.identity["snapshot"])


def row(key, **extra):
    return {"id": key, "failed": False, "result": {}, "model_calls": [], **extra}


@pytest.mark.parametrize("invalid", [-1, True, False, 1.5, "1", None])
def test_invalid_call_usage_and_estimates_do_not_release_reservation(invalid):
    persisted = []
    budget = EvaluationBudget(3, 100000, on_change=persisted.append)
    budget.before_call()
    before = budget.state()
    with pytest.raises(GoldValidationError, match="integer call usage"):
        budget.reconcile(calls=invalid, tokens=1)
    assert budget.state() == before and persisted == [before]
    with pytest.raises(GoldValidationError, match="integer input estimate"):
        budget.before_call(estimated_input_tokens=invalid)
    assert budget.state() == before and persisted == [before]


@pytest.mark.parametrize("invalid", [-1, True, False, 1.5, "1", None])
def test_invalid_token_usage_retains_charge_and_restores_exactly(invalid):
    persisted = []
    budget = EvaluationBudget(3, 100000, on_change=persisted.append)
    budget.before_call()
    budget.reconcile(calls=1, tokens=invalid)
    assert budget.used_tokens == 17408 and budget.used_calls == 1
    assert budget.reservation is None
    restored = EvaluationBudget(3, 100000)
    restored.restore(persisted[-1])
    assert restored.state() == budget.state()


@pytest.mark.parametrize("input_tokens,output_tokens", [(-1, 10), (10, -1), (True, 10), (10, False)])
def test_invalid_provider_usage_is_unknown_even_when_sum_would_be_positive(input_tokens, output_tokens):
    budget = EvaluationBudget(3, 100000)
    budget.before_call()
    budget.after_call(input_tokens=input_tokens, output_tokens=output_tokens)
    assert budget.used_calls == 1 and budget.used_tokens == 17408


def test_reservations_persist_before_call_and_resume_never_repeats_completed_rows(tmp_path):
    output, seen = tmp_path / "predictions.jsonl", []
    budget = EvaluationBudget(10, 100000)

    def provider(gold, calls):
        budget.before_call()
        saved = state(output)
        assert saved["inflight"] == {"id": gold["id"]}
        assert saved["budget"]["reservation"] == [1, 17408]
        seen.append(gold["id"])
        budget.after_call(input_tokens=None, output_tokens=None)
        return {"result": {}, "model_calls": []}

    with CollectionCheckpoint(output, identity(), budget=budget) as journal:
        bind(journal)
        append = journal.append

        def interrupt_after_row(result, **kwargs):
            append(result, **kwargs)
            raise KeyboardInterrupt()

        journal.append = interrupt_after_row
        with pytest.raises(KeyboardInterrupt):
            collect_durable([{"id": "a"}, {"id": "b"}], provider, lambda: identity()["snapshot"], journal)
    assert state(output)["budget"]["charged_tokens"] == 17408
    budget = EvaluationBudget(10, 100000)
    with CollectionCheckpoint(output, identity(), budget=budget, resume=True) as journal:
        assert budget.used_calls == 1 and budget.used_tokens == 17408
        receipt = collect_durable([{"id": "a"}, {"id": "b"}], provider, lambda: identity()["snapshot"], journal)
    assert seen == ["a", "b"]
    assert receipt["budget"]["charged_calls"] == 2
    assert receipt["budget"]["charged_tokens"] == 34816


@pytest.mark.parametrize("adapter", ["provider", "retrieval", "http"])
def test_interrupted_call_is_refused_and_reservation_remains_charged(tmp_path, adapter):
    output = tmp_path / "predictions.jsonl"
    budget = EvaluationBudget(3, 100000)
    with CollectionCheckpoint(output, identity(adapter), budget=budget) as journal:
        bind(journal)
        journal.begin("a")
        budget.before_call()
        # Process ends without a returned observation; no evidence permits replay.
    before = output.with_suffix(".checkpoint.json").read_bytes()
    restored = EvaluationBudget(3, 100000)
    with pytest.raises(GoldValidationError, match="INFLIGHT_UNRESOLVED"):
        with CollectionCheckpoint(output, identity(adapter), budget=restored, resume=True):
            pytest.fail("an uncertain call must not be replayed")
    assert restored.used_calls == 1 and restored.used_tokens == 17408
    assert output.with_suffix(".checkpoint.json").read_bytes() == before


def test_failed_completed_call_preserves_unknown_usage_on_resume(tmp_path):
    output = tmp_path / "predictions.jsonl"
    budget = EvaluationBudget(3, 100000)
    with CollectionCheckpoint(output, identity(), budget=budget) as journal:
        bind(journal)
        journal.begin("a")
        budget.before_call()
        journal.append(row("a", failed=True, error_code="TimeoutError"))
    restored = EvaluationBudget(3, 100000)
    with CollectionCheckpoint(output, identity(), budget=restored, resume=True) as journal:
        journal.begin("b")
        restored.before_call()
        assert restored.used_calls == 2 and restored.used_tokens == 34816
        restored.after_call(input_tokens=10, output_tokens=20)
        journal.append(row("b"))
        receipt = journal.finalize()
    assert receipt["failed"] == 1 and receipt["budget"]["charged_tokens"] == 17438


@pytest.mark.parametrize("partial_ledger", [False, True])
def test_failed_http_receipt_with_missing_call_logs_keeps_whole_charge_and_stops_resume(tmp_path, partial_ledger):
    import httpx
    from eval.collect import HttpQueryCollector
    output, posts = tmp_path / "predictions.jsonl", []
    base = {"evaluation_host": True, "user_id": "eval-ledger", "snapshot": identity()["snapshot"],
            "state_count": 0, "event_count": 0, "conversation_count": 0, "states": [], "write_events": [],
            "model_gate_shared": True, "query_planning_max_calls": 4,
            "request_max_model_calls": 6, "request_max_tokens": 96000}
    observed = [{"id": "recorded-call", "operation_type": "QUERY_PLAN", "input_tokens": 7, "output_tokens": 5}] if partial_ledger else []
    budget = EvaluationBudget(72, 1152000)
    def handle(request):
        if request.method == "POST":
            assert budget.reservation == (6, 96000)
            posts.append("provider called but final call log failed")
            return httpx.Response(500, json={"error": {"code": "CALL_LOG_WRITE_FAILED"}})
        requested = bool(request.url.params.get("request_id"))
        return httpx.Response(200, json={**base, "run_status": "FAILED" if requested else None,
                                        "model_calls": observed if requested else []})
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handle)) as client:
        collector = HttpQueryCollector(client, {"snapshot": base["snapshot"]}, budget)
        with CollectionCheckpoint(output, identity("http"), budget=budget) as journal:
            bind(journal)
            with pytest.raises(GoldValidationError, match="UNCERTAIN"):
                collect_durable([{"id": "a", "message": "first"}, {"id": "b", "message": "next"}],
                                collector, lambda: base["snapshot"], journal)
    persisted = state(output)
    assert len(posts) == 1 and persisted["rows"][0]["model_calls"] == observed
    assert persisted["budget"]["charged_calls"] == 6 and persisted["budget"]["charged_tokens"] == 96000
    restored = EvaluationBudget(72, 1152000)
    with pytest.raises(GoldValidationError, match="UNCERTAIN"):
        with CollectionCheckpoint(output, identity("http"), budget=restored, resume=True):
            pass
    assert restored.used_calls == 6 and restored.used_tokens == 96000


def test_unfinished_read_only_sample_can_resume(tmp_path):
    output = tmp_path / "predictions.jsonl"
    with CollectionCheckpoint(output, identity("sql", ("a",))) as journal:
        bind(journal)
        journal.begin("a")
    with CollectionCheckpoint(output, identity("sql", ("a",)), resume=True) as journal:
        assert journal.state["inflight"] is None
        receipt = collect_durable([{"id": "a"}], lambda gold, calls: {"result": {}},
                                  lambda: identity()["snapshot"], journal)
    assert receipt["samples"] == 1


def test_last_committed_row_can_finalize_without_a_collector(tmp_path):
    output = tmp_path / "predictions.jsonl"
    specification = identity("http", ("a",))
    with CollectionCheckpoint(output, specification, budget=EvaluationBudget(2, 100000)) as journal:
        bind(journal)
        journal.begin("a")
        journal.append(row("a"))
        # Crash before final prediction/receipt materialization.
    with CollectionCheckpoint(output, specification, budget=EvaluationBudget(2, 100000), resume=True) as journal:
        assert journal.complete
        first = journal.finalize()
    with CollectionCheckpoint(output, specification, budget=EvaluationBudget(2, 100000), resume=True) as journal:
        assert journal.finalize() == first
    assert first["runtime_verification"] == "REMOTE_SOURCE_NOT_VERIFIED"


def test_checkpoint_corruption_and_input_or_candidate_drift_are_rejected(tmp_path):
    output = tmp_path / "predictions.jsonl"
    original = identity("sql")
    with CollectionCheckpoint(output, original):
        pass
    for field in ("dataset_sha256", "selection_sha256", "settings_sha256", "provenance", "candidate_binding"):
        changed = deepcopy(original)
        changed[field] = {"changed": True}
        with pytest.raises(GoldValidationError, match="IDENTITY_CHANGED"):
            with CollectionCheckpoint(output, changed, resume=True):
                pass
    envelope = read_json(output.with_suffix(".checkpoint.json"))
    envelope["state"]["rows"] = [row("a")]
    write_json(output.with_suffix(".checkpoint.json"), envelope)
    with pytest.raises(GoldValidationError, match="CHECKPOINT_CORRUPT"):
        with CollectionCheckpoint(output, original, resume=True):
            pass


def test_partial_is_a_verified_derived_prefix_and_changed_rows_are_rejected(tmp_path):
    output = tmp_path / "predictions.jsonl"
    with CollectionCheckpoint(output, identity("sql")) as journal:
        bind(journal)
        journal.begin("a")
        journal.append(row("a"))
    output.with_suffix(".partial.jsonl").write_text('{"id":"a","result":"tampered"}\n')
    with pytest.raises(GoldValidationError, match="PREDICTIONS_CHANGED"):
        with CollectionCheckpoint(output, identity("sql"), resume=True):
            pass


def test_existing_output_and_concurrent_writer_are_rejected(tmp_path):
    output = tmp_path / "predictions.jsonl"
    with CollectionCheckpoint(output, identity("sql")):
        with pytest.raises(GoldValidationError, match="ALREADY_RUNNING"):
            with CollectionCheckpoint(output, identity("sql"), resume=True):
                pass
    with pytest.raises(GoldValidationError, match="OUTPUT_ALREADY_EXISTS"):
        with CollectionCheckpoint(output, identity("sql")):
            pass


def test_http_runtime_and_actor_state_cannot_drift(tmp_path):
    output = tmp_path / "predictions.jsonl"
    context = {"user_id": "eval-one", "conversation_count": 0, "state_count": 0, "event_count": 0,
               "states": [], "write_events": [], "snapshot": identity()["snapshot"], "query_prompt_version": "v1",
               "conversation_ledger_sha256": digest({"turns": []})}
    with CollectionCheckpoint(output, identity("http")) as journal:
        assert journal.bind_runtime(context, "stable-run-id") == "stable-run-id"
    with CollectionCheckpoint(output, identity("http"), resume=True) as journal:
        assert journal.bind_runtime(context, "new-run-id") == "stable-run-id"
        with pytest.raises(GoldValidationError, match="RUNTIME_CHANGED"):
            journal.bind_runtime({**context, "query_prompt_version": "v2"}, "new")
        with pytest.raises(GoldValidationError, match="HTTP_STATE_CHANGED"):
            journal.bind_runtime({**context, "conversation_count": 1}, "new")
        with pytest.raises(GoldValidationError, match="HTTP_STATE_CHANGED"):
            journal.bind_runtime({**context, "conversation_ledger_sha256": digest({"turns": ["external"]})}, "new")
        journal.begin("a")
        with pytest.raises(GoldValidationError, match="RUNTIME_CHANGED"):
            journal.append(row("a"), context={**context, "query_prompt_version": "v2"})
    assert state(output)["rows"][0]["error_code"] == "COLLECTION_RUNTIME_CHANGED"


def test_legacy_http_checkpoint_is_readable_but_cannot_resume_without_ledger(tmp_path):
    output = tmp_path / "predictions.jsonl"
    context = {"user_id": "eval-one", "conversation_ledger_sha256": digest([])}
    with CollectionCheckpoint(output, identity("http")) as journal:
        journal.bind_runtime(context, "original")
        journal.state["http_context"].pop("conversation_ledger_sha256")
        journal.save()
    with CollectionCheckpoint(output, identity("http"), resume=True) as journal:
        assert journal.state["runtime"]["request_namespace"] == "original"
        with pytest.raises(GoldValidationError, match="LEGACY_HTTP_CHECKPOINT"):
            journal.bind_runtime(context, "resumed")


def test_completed_legacy_http_checkpoint_finalizes_offline_without_ledger(tmp_path):
    output = tmp_path / "predictions.jsonl"
    specification = identity("http", ("a",))
    with CollectionCheckpoint(output, specification) as journal:
        journal.bind_runtime({"user_id": "eval-one", "conversation_ledger_sha256": digest([])}, "original")
        journal.begin("a")
        journal.append(row("a"))
        journal.state["http_context"].pop("conversation_ledger_sha256")
        journal.save()
    with CollectionCheckpoint(output, specification, resume=True) as journal:
        assert journal.finalize()["samples"] == 1


def test_unresolved_http_telemetry_blocks_further_work(tmp_path):
    output = tmp_path / "predictions.jsonl"
    with CollectionCheckpoint(output, identity("http")) as journal:
        journal.begin("a")
        with pytest.raises(GoldValidationError, match="UNCERTAIN"):
            journal.append(row("a", failed=True, telemetry_unavailable=True))
    with pytest.raises(GoldValidationError, match="UNCERTAIN"):
        with CollectionCheckpoint(output, identity("http"), resume=True):
            pass


def test_candidate_model_mismatch_keeps_observation_but_refuses_finalization(tmp_path):
    output = tmp_path / "predictions.jsonl"
    specification = identity("provider", ("a",))
    specification["candidate_binding"] = {"components": {"extraction": {"version": "candidate", "model": "expected"}}}
    with CollectionCheckpoint(output, specification) as journal:
        journal.begin("a")
        journal.append(row("a", model_calls=[{"operation_type": "EXTRACTION", "model": "different"}]))
        with pytest.raises(GoldValidationError, match="CANDIDATE_OBSERVATION_MISMATCH"):
            journal.finalize()
    assert state(output)["rows"][0]["model_calls"][0]["model"] == "different"
    assert not output.exists()


@pytest.mark.parametrize("declaration,operation", [
    ("model_revision", "EXTRACTION"), ("embedding_model_revision", "EMBEDDING"),
    ("reranker_model_revision", "RERANK"),
])
@pytest.mark.parametrize("observed", ["expected-revision", "different-revision", None, "no-call"])
def test_candidate_revision_requires_matching_observed_revision(tmp_path, declaration, operation, observed):
    output = tmp_path / "predictions.jsonl"
    specification = identity("provider", ("a",))
    specification["candidate_binding"] = {"components": {"extraction": {
        "version": "candidate", declaration: "expected-revision"}}}
    calls = [] if observed == "no-call" else [{"operation_type": operation,
        "model": "same-requested-alias", **({"model_revision": observed} if observed is not None else {})}]
    with CollectionCheckpoint(output, specification) as journal:
        bind(journal)
        journal.begin("a")
        journal.append(row("a", model_calls=calls))
        if observed == "expected-revision":
            assert journal.finalize()["version_binding"] == "observed_models"
        else:
            with pytest.raises(GoldValidationError, match="CANDIDATE_OBSERVATION_MISMATCH: " + declaration):
                journal.finalize()
            assert not output.exists()
    assert state(output)["rows"][0]["model_calls"] == calls


def test_declared_revision_allows_collector_preflight_before_observations(tmp_path):
    output = tmp_path / "predictions.jsonl"
    specification = identity("provider", ("a",))
    specification["candidate_binding"] = {"components": {"extraction": {
        "version": "candidate", "model_revision": "expected-revision"}}}
    with CollectionCheckpoint(output, specification) as journal:
        bind(journal)
        # _build_collector validates declarations before it can call a model.
        assert journal.verify_candidate() == "declared_only"
        journal.begin("a")
        journal.append(row("a", model_calls=[{"operation_type": "EXTRACTION",
            "model": "requested-alias", "model_revision": "expected-revision"}]))
        assert journal.verify_candidate() == "observed_models"
        assert journal.finalize()["version_binding"] == "observed_models"


def test_atomic_write_failure_preserves_old_evidence_and_cleans_temporary_file(tmp_path, monkeypatch):
    import eval.common
    output = tmp_path / "state.json"
    write_json(output, {"saved": True})

    def failure(*_):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(eval.common.os, "replace", failure)
    with pytest.raises(OSError, match="replace failure"):
        write_json(output, {"saved": False})
    assert read_json(output) == {"saved": True}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_main_can_finish_after_receipt_write_crash_without_reopening_services(tmp_path, monkeypatch):
    import eval.checkpoint
    import eval.collect
    import interview_intelligence.config

    manifest = {"version": "fixture", "snapshot": {"corpus_revision": 1}, "sql": [{"id": "a", "split": "dev"}]}
    monkeypatch.setattr(eval.collect, "validate_gold", lambda *a, **k: manifest)
    monkeypatch.setattr(eval.collect, "provenance", lambda: {"source_sha256": "fixed", "created_at": "ignored"})
    monkeypatch.setattr(interview_intelligence.config, "load_settings",
                        lambda: SimpleNamespace(model_dump=lambda **k: {"fixture": True}))
    opened = []

    def factory(args, manifest, settings, budget, journal):
        opened.append(True)
        journal.bind_snapshot(manifest["snapshot"])
        return lambda gold, calls: {"result": {}}, lambda: manifest["snapshot"], None, None

    monkeypatch.setattr(eval.collect, "_build_collector", factory)
    original = eval.checkpoint.write_json

    def crash(path, value):
        if path.name.endswith(".collection.json"):
            raise KeyboardInterrupt()
        original(path, value)

    monkeypatch.setattr(eval.checkpoint, "write_json", crash)
    args = ["--dataset", str(tmp_path), "--section", "sql", "--adapter", "sql",
            "--output", str(tmp_path / "predictions.jsonl")]
    with pytest.raises(KeyboardInterrupt):
        eval.collect.main(args)
    assert (tmp_path / "predictions.jsonl").exists()
    monkeypatch.setattr(eval.checkpoint, "write_json", original)
    receipt = eval.collect.main([*args, "--resume"])
    assert opened == [True] and receipt["samples"] == 1


def test_persistence_failure_prevents_network_call_and_keeps_existing_reservation(tmp_path, monkeypatch):
    output = tmp_path / "predictions.jsonl"
    budget = EvaluationBudget(3, 100000)
    calls = []
    with CollectionCheckpoint(output, identity(), budget=budget) as journal:
        journal.begin("a")

        def fail_write():
            raise OSError("disk unavailable")

        monkeypatch.setattr(journal, "save", fail_write)
        with pytest.raises(OSError, match="disk unavailable"):
            budget.before_call()
            calls.append("network")
    assert calls == []
    assert state(output)["inflight"] == {"id": "a"}


def test_pause_at_scenario_boundary_preserves_whole_request_budget_and_resume(tmp_path):
    output, seen = tmp_path / "predictions.jsonl", []
    specification = identity("http", ("a", "b", "c"))
    golds = [{"id": key, "turns": [{"message": "first"}, {"message": "follow-up"}]} for key in ("a", "b", "c")]
    budget = EvaluationBudget(72, 1152000)

    def collector(gold, calls):
        for index, _ in enumerate(gold["turns"]):
            budget.reserve(calls=6, tokens=96000)
            assert state(output)["budget"]["reservation"] == [6, 96000]
            seen.append((gold["id"], index))
            budget.reconcile(calls=1, tokens=None)
        return {"model_calls": []}

    with CollectionCheckpoint(output, specification, budget=budget) as journal:
        bind(journal)
        progress = collect_durable(golds, collector, lambda: specification["snapshot"], journal, stop_after_samples=2)
        assert progress["status"] == "PAUSED" and progress["samples"] == 2
        assert not output.exists() and not output.with_suffix(".collection.json").exists()
    assert seen == [("a", 0), ("a", 1), ("b", 0), ("b", 1)]
    budget = EvaluationBudget(72, 1152000)
    with CollectionCheckpoint(output, specification, budget=budget, resume=True) as journal:
        assert budget.used_calls == 4 and budget.used_tokens == 384000
        receipt = collect_durable(golds, collector, lambda: specification["snapshot"], journal)
    assert seen[-2:] == [("c", 0), ("c", 1)] and len(seen) == 6
    assert receipt["budget"]["charged_calls"] == 6 and receipt["budget"]["charged_tokens"] == 576000


def test_cookie_value_is_not_part_of_collection_identity(tmp_path, monkeypatch):
    from eval.collect import collection_identity, load_http_cookies
    import eval.collect
    secret = "private-session-fixture"
    path = tmp_path / "private-cookies.json"
    write_json(path, {"ii_tenant_session": secret})
    path.chmod(0o600)
    assert load_http_cookies(path) == {"ii_tenant_session": secret}
    args = SimpleNamespace(adapter="http", section="routing", split="dev", review_policy="human",
        candidate_binding=None, snapshot_export=None, deployment_evidence=None, http_cookie_file=path,
        stop_after_samples=2, output=tmp_path / "predictions.jsonl", resume=False)
    monkeypatch.setattr(eval.collect, "provenance", lambda: {"source_sha256": "source"})
    settings = SimpleNamespace(model_dump=lambda **kwargs: {})
    first = collection_identity(args, {"snapshot": {}}, [{"id": "a"}], settings)
    args.stop_after_samples, args.resume = None, True
    assert collection_identity(args, {"snapshot": {}}, [{"id": "a"}], settings) == first
    assert first["http_cookie_file_sha256"] == digest(path.read_bytes())
    assert secret not in str(first) and str(path) not in str(first)
    write_json(path, {"other_cookie": secret})
    with pytest.raises(GoldValidationError, match="HTTP_COOKIE_FILE_INVALID"):
        load_http_cookies(path)
