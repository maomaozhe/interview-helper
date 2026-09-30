"""Persistent local batch worker. Each source has its own atomic publication."""

from __future__ import annotations

import time

from sqlalchemy import select, text
import httpx
from openai import APIStatusError

from interview_intelligence.config import load_settings, validate_backend_model_endpoint, validate_model_preflight
from interview_intelligence.dedup.provider import ArkMultimodalEncoder, OpenAICompatibleEncoder, OpenAICompatibleJudge
from interview_intelligence.dedup.service import DedupService
from interview_intelligence.domain.models import ModelCall, PipelineRun, create_database, now_utc
from interview_intelligence.extraction.provider import OpenAICompatibleExtractor
from interview_intelligence.ingestion.pipeline import IngestService
from interview_intelligence.providers.budget import CallBudget
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.search.elasticsearch import ElasticsearchRetriever, rebuild_index
from interview_intelligence.search.reranker import LLMReranker


def build_worker_services(database, settings, run_id: str | None = None):
    validate_model_preflight(
        api_key=settings.model_api_key, embedding_dimension=settings.embedding_dimension,
        max_calls=settings.max_model_calls, max_tokens=settings.max_model_tokens,
    )
    if not settings.model_base_url or not settings.extraction_model or not settings.embedding_model or not settings.judge_model:
        raise ValueError("MODEL_CONFIGURATION_INCOMPLETE")
    validate_backend_model_endpoint(settings.model_base_url)
    budget = CallBudget(max_calls=settings.max_model_calls, max_tokens=settings.max_model_tokens)
    if run_id is not None:
        with database.session() as session:
            previous_calls = list(session.scalars(select(ModelCall).where(ModelCall.run_id == run_id)))
            budget.used_calls = len(previous_calls)
            budget.used_tokens = sum((item.input_tokens or 0) + (item.output_tokens or 0)
                                     for item in previous_calls)
    gate = ModelCallGate(settings.model_lock_path,
                         minimum_interval_seconds=settings.model_min_interval_seconds)
    calls: list[dict] = []
    def record_call(entry: dict):
        calls.append(entry)
        if run_id is not None:
            _persist_model_calls(database, run_id, calls)
    encoder_class = ArkMultimodalEncoder if settings.embedding_model == "doubao-embedding-vision" else OpenAICompatibleEncoder
    encoder = encoder_class(model=settings.embedding_model,
                            dimension=settings.embedding_dimension,
                            api_key=settings.model_api_key, base_url=settings.model_base_url,
                            budget=budget, call_gate=gate, on_call=record_call,
                            timeout_seconds=settings.model_request_timeout_seconds)
    extractor = OpenAICompatibleExtractor(model=settings.extraction_model,
                                           api_key=settings.model_api_key,
                                           base_url=settings.model_base_url,
                                           on_call=record_call, budget=budget, call_gate=gate,
                                           timeout_seconds=settings.model_request_timeout_seconds,
                                           stream=settings.extraction_stream)
    judge = OpenAICompatibleJudge(model=settings.judge_model,
                                  api_key=settings.model_api_key,
                                  base_url=settings.model_base_url,
                                  on_call=record_call, budget=budget, call_gate=gate,
                                  timeout_seconds=settings.model_request_timeout_seconds)
    deduper = DedupService(encoder=encoder, judge=judge)
    ingestor = IngestService(database, settings.corpus_root, settings.snapshot_root,
                             extractor, deduper)
    reranker = (LLMReranker(model=settings.reranker_model, api_key=settings.model_api_key,
                            base_url=settings.model_base_url, budget=budget,
                            call_gate=gate, timeout_seconds=settings.model_request_timeout_seconds)
                if settings.reranker_model else None)
    retriever = ElasticsearchRetriever(settings.elasticsearch_url, encoder, reranker=reranker)
    return ingestor, lambda: rebuild_index(database, retriever), calls


def _persist_model_calls(database, run_id: str, calls: list[dict]):
    if not calls:
        return
    with database.session() as session:
        with session.begin():
            run = session.get(PipelineRun, run_id)
            for entry in calls:
                session.add(ModelCall(
                    request_id=run_id, run_id=run_id,
                    operation_type=entry["operation_type"], model=entry["model"],
                    model_revision=entry.get("model_revision"),
                    prompt_version=entry["prompt_version"],
                    input_tokens=entry["input_tokens"], output_tokens=entry["output_tokens"],
                    latency_ms=entry["latency_ms"], status=entry["status"],
                    retry_count=entry["retry_count"], error_code=entry["error_code"],
                    usage_source="PROVIDER" if entry["input_tokens"] is not None else "UNAVAILABLE",
                ))
                run.input_tokens += entry["input_tokens"] or 0
                run.output_tokens += entry["output_tokens"] or 0
        calls.clear()


def process_queued_run(database, run_id: str, ingestor, indexer, calls: list[dict] | None = None) -> str:
    with database.session() as session:
        with session.begin():
            run = session.get(PipelineRun, run_id, with_for_update=True)
            if run is None:
                raise KeyError("RUN_NOT_FOUND")
            if run.status in {"SUCCEEDED", "FAILED", "PARTIAL_FAILURE", "NEEDS_REVIEW"}:
                return run.status
            if run.status != "QUEUED":
                raise ValueError("RUN_ALREADY_RUNNING")
            run.status = "RUNNING"
            paths = list(run.config_snapshot.get("paths", []))
            finished_paths = set(run.config_snapshot.get("finished_paths", []))
            mode = run.config_snapshot.get("mode", "changed")
    stopped = False
    index_failed = False
    synced_during_loop = False
    def sync_index():
        nonlocal index_failed, synced_during_loop
        try:
            indexer()
            index_failed = False
            synced_during_loop = True
            with database.session() as session:
                with session.begin():
                    run = session.get(PipelineRun, run_id)
                    run.config_snapshot = {key: value for key, value in run.config_snapshot.items()
                                           if key != "index_error"}
        except Exception as error:
            index_failed = True
            with database.session() as session:
                with session.begin():
                    run = session.get(PipelineRun, run_id)
                    run.config_snapshot = {**run.config_snapshot, "index_error": type(error).__name__}
    for path_index, path in enumerate([] if mode == "reindex" else paths):
        if path in finished_paths:
            continue
        try:
            outcome = ingestor.ingest_file(path)
            with database.session() as session:
                with session.begin():
                    run = session.get(PipelineRun, run_id)
                    if outcome.status == "SKIPPED":
                        run.skipped_documents += 1
                    elif outcome.status == "NEEDS_REVIEW":
                        run.needs_review_documents += 1
                    else:
                        run.processed_documents += 1
                        run.processed_questions += outcome.question_count
                        if getattr(outcome, "excluded", False):
                            run.excluded_documents += 1
                    run.config_snapshot = {**run.config_snapshot,
                                           "finished_paths": [*run.config_snapshot.get("finished_paths", []), path]}
                    finished_paths.add(path)
            if outcome.status == "SUCCEEDED":
                sync_index()
        except Exception as error:
            status_code = (error.status_code if isinstance(error, APIStatusError)
                           else error.response.status_code if isinstance(error, httpx.HTTPStatusError)
                           else None)
            stopped = (status_code in {400, 401, 403, 404, 429}
                       or "BUDGET_EXCEEDED" in str(error))
            with database.session() as session:
                with session.begin():
                    run = session.get(PipelineRun, run_id)
                    run.failed_documents += 1
                    failed = [item for item in paths[path_index:] if item not in finished_paths] if stopped else [path]
                    run.config_snapshot = {**run.config_snapshot,
                                           "finished_paths": [*run.config_snapshot.get("finished_paths", []), path],
                                           "failed_paths": list(dict.fromkeys([*run.config_snapshot.get("failed_paths", []), *failed])),
                                           "stopped_on_model_error": stopped,
                                           "last_error": type(error).__name__}
                    finished_paths.add(path)
            if stopped:
                break
        finally:
            if calls is not None:
                _persist_model_calls(database, run_id, calls)
    if stopped:
        index_failed = True
        with database.session() as session:
            with session.begin():
                run = session.get(PipelineRun, run_id)
                run.config_snapshot = {**run.config_snapshot, "index_error": "MODEL_CALLS_STOPPED"}
    elif not synced_during_loop or index_failed:
        sync_index()
    if calls is not None:
        _persist_model_calls(database, run_id, calls)
    with database.session() as session:
        with session.begin():
            run = session.get(PipelineRun, run_id)
            run.end_time = now_utc()
            run.status = ("PARTIAL_FAILURE" if run.failed_documents and run.processed_documents
                          else "FAILED" if run.failed_documents or index_failed
                          else "NEEDS_REVIEW" if run.needs_review_documents
                          else "SUCCEEDED")
            return run.status


def recover_orphaned_runs(database) -> int:
    """Called only after the process holds the single-worker advisory lock."""
    with database.session() as session:
        with session.begin():
            runs = list(session.scalars(select(PipelineRun).where(
                PipelineRun.status.in_(["RUNNING", "QUEUED"])).with_for_update()))
            recovered = 0
            for run in runs:
                if "paths" not in run.config_snapshot:
                    from interview_intelligence.domain.models import PipelineTask
                    run.status = "FAILED"
                    run.end_time = now_utc()
                    for task in session.scalars(select(PipelineTask).where(
                        PipelineTask.run_id == run.id, PipelineTask.state.in_(["PENDING", "RUNNING"]))):
                        task.state = "FAILED"
                        task.error_code = "WORKER_RESTART"
                    recovered += 1
                    continue
                if run.status != "RUNNING":
                    continue
                run.status = "QUEUED"
                run.config_snapshot = {
                    **run.config_snapshot,
                    "recovery_count": run.config_snapshot.get("recovery_count", 0) + 1,
                }
                recovered += 1
            return recovered


def _work_loop(database, settings):
    while True:
        with database.session() as session:
            run_id = session.scalar(select(PipelineRun.id).where(
                PipelineRun.status == "QUEUED").order_by(PipelineRun.start_time).limit(1))
        if run_id:
            try:
                ingestor, indexer, calls = build_worker_services(database, settings, run_id=run_id)
                process_queued_run(database, run_id, ingestor, indexer, calls)
            except Exception:
                with database.session() as session:
                    with session.begin():
                        run = session.get(PipelineRun, run_id)
                        run.status = "FAILED"
                        run.end_time = now_utc()
        else:
            time.sleep(3)


def main():
    settings = load_settings()
    database = create_database(settings.database_url)
    while True:
        with database.engine.connect() as lease:
            if database.engine.dialect.name == "postgresql" and not lease.scalar(
                text("SELECT pg_try_advisory_lock(7461182371904)")):
                lease.commit()
                time.sleep(3)
                continue
            lease.commit()
            recover_orphaned_runs(database)
            _work_loop(database, settings)
        if database.engine.dialect.name != "postgresql":
            time.sleep(3)


if __name__ == "__main__":
    main()
