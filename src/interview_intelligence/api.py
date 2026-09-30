"""Local FastAPI interface over the shared domain services."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date
from typing import Annotated, Literal
from uuid import uuid4
from urllib.parse import quote
import httpx
from openai import APIError

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError
from sqlalchemy import select

from interview_intelligence.analytics.detail import get_question_detail, list_occurrences
from interview_intelligence.analytics.stats import query_question_stats
from interview_intelligence.analytics.scope import sign_scope, verify_scope
from interview_intelligence.agent.service import AgentService
from interview_intelligence.config import Settings, load_settings, resolve_corpus_path, validate_backend_model_endpoint, validate_model_preflight
from interview_intelligence.contracts import FilterSpec, ReviewRequest, StatsRequest, StrictModel
from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusState, IdempotencyReceipt, PipelineRun,
    SourceRevision, create_database,
)
from interview_intelligence.review.service import ReviewService
from interview_intelligence.dedup.provider import ArkMultimodalEncoder, OpenAICompatibleEncoder
from interview_intelligence.search.elasticsearch import ElasticsearchRetriever
from interview_intelligence.search.reranker import LLMReranker
from interview_intelligence.providers.budget import CallBudget
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.ingestion.snapshot import decode_source
from interview_intelligence.search.service import search_questions
from interview_intelligence.taxonomy import load_taxonomy


class IngestRequest(StrictModel):
    paths: list[str] | None = None
    mode: Literal["changed", "reprocess", "reindex"] = "changed"
    idempotency_key: str = Field(min_length=1)


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    context: list[dict] = Field(default_factory=list, max_length=10)
    request_id: str | None = None


class RetryRequest(StrictModel):
    idempotency_key: str = Field(min_length=1)


def _request_id(request: Request) -> str:
    return request.headers.get("x-request-id") or str(uuid4())


def _prefix_source_urls(value, prefix: str):
    if isinstance(value, list):
        return [_prefix_source_urls(item, prefix) for item in value]
    if isinstance(value, dict):
        return {key: prefix + item if key in {"sources_url", "source_api_url"}
                and isinstance(item, str) and item.startswith("/api/")
                else _prefix_source_urls(item, prefix) for key, item in value.items()}
    return value


def _response(request: Request, database, data, *, filters=None, status_code=200, extra=None, warnings=None):
    with database.session() as session:
        state = session.get(CorpusState, 1)
        revision = state.current_revision
    prefix = request.scope.get("root_path", "").rstrip("/")
    if prefix:
        data = _prefix_source_urls(data, prefix)
    return JSONResponse(status_code=status_code, content={
        "data": data,
        "meta": {"request_id": _request_id(request), "corpus_revision": revision,
                 "applied_filters": filters.model_dump(mode="json") if filters else None,
                 "as_of": date.today().isoformat(), **(extra or {})},
        "warnings": warnings or [],
    })


def create_app(database=None, settings: Settings | None = None, retriever=None) -> FastAPI:
    settings = settings or load_settings()
    database = database or create_database(settings.database_url)
    if retriever is None and settings.elasticsearch_url:
        encoder = None
        reranker = None
        if settings.model_api_key and settings.model_base_url and settings.embedding_model:
            validate_backend_model_endpoint(settings.model_base_url)
            validate_model_preflight(api_key=settings.model_api_key,
                                     embedding_dimension=settings.embedding_dimension,
                                     max_calls=settings.max_model_calls,
                                     max_tokens=settings.max_model_tokens)
            budget = CallBudget(max_calls=settings.max_model_calls, max_tokens=settings.max_model_tokens)
            gate = ModelCallGate(settings.model_lock_path,
                                 minimum_interval_seconds=settings.model_min_interval_seconds)
            encoder_class = (ArkMultimodalEncoder if settings.embedding_model == "doubao-embedding-vision"
                             else OpenAICompatibleEncoder)
            encoder = encoder_class(model=settings.embedding_model, dimension=settings.embedding_dimension,
                                    api_key=settings.model_api_key, base_url=settings.model_base_url,
                                    budget=budget, call_gate=gate,
                                    timeout_seconds=settings.model_request_timeout_seconds)
            if settings.reranker_model:
                reranker = LLMReranker(model=settings.reranker_model,
                                       api_key=settings.model_api_key, base_url=settings.model_base_url,
                                       budget=budget, call_gate=gate,
                                       timeout_seconds=settings.model_request_timeout_seconds)
        retriever = ElasticsearchRetriever(settings.elasticsearch_url, encoder, reranker=reranker)
    review_service = ReviewService(database)
    agent_service = AgentService(database, retriever, user_id=settings.local_user_id)
    signing_key = settings.app_signing_key or secrets.token_hex(32)
    app = FastAPI(title="Interview Intelligence", version="1.0.0", root_path=settings.api_root_path)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {
            "code": "VALIDATION_ERROR", "message": "invalid request", "details": {"errors": error.errors()},
            "retryable": False}, "request_id": _request_id(request)})

    @app.exception_handler(KeyError)
    async def missing_error(request: Request, error: KeyError):
        return JSONResponse(status_code=404, content={"error": {
            "code": "NOT_FOUND", "message": str(error), "retryable": False},
            "request_id": _request_id(request)})

    @app.exception_handler(ValueError)
    async def semantic_error(request: Request, error: ValueError):
        message = str(error)
        code = message.split(":", 1)[0] if message.isupper() or ":" in message else "INVALID_OPERATION"
        status = 409 if code in {"IDEMPOTENCY_CONFLICT", "USER_STATE_VERSION_CONFLICT", "SNAPSHOT_CHANGED"} else 400
        if code in {"INDEX_NOT_READY", "EMBEDDING_NOT_READY", "RERANKER_NOT_READY",
                    "MODEL_CONFIGURATION_INCOMPLETE", "MODEL_NOT_READY", "SOURCE_SNAPSHOT_MISSING"}:
            status = 503
        return JSONResponse(status_code=status, content={"error": {
            "code": code, "message": message, "retryable": status == 503},
            "request_id": _request_id(request)})

    @app.get("/api/health")
    def health(request: Request):
        with database.session() as session:
            state = session.get(CorpusState, 1)
            data = {"database": "ready", "current_revision": state.current_revision,
                    "indexed_revision": state.indexed_revision,
                    "index": "ready" if retriever and state.current_revision > 0 and state.indexed_revision == state.current_revision else "not_ready",
                    "model_max_in_flight": 1,
                    "model_min_interval_seconds": settings.model_min_interval_seconds,
                    "model": "configured" if settings.model_api_key and settings.model_base_url else "not_configured"}
        return _response(request, database, data)

    @app.get("/api/topics")
    def topics(request: Request):
        taxonomy = load_taxonomy()
        return _response(request, database, {"taxonomy_version": "v1", "topics": taxonomy.topics})

    @app.get("/api/topics/{topic_id}/overview")
    def topic_overview(request: Request, topic_id: str, company: str | None = None,
                       position: str | None = None, limit: int = Query(20, ge=1, le=100)):
        taxonomy = load_taxonomy()
        if topic_id in taxonomy.topics:
            filters = StatsRequest(topic_l1=topic_id, company=company, position=position, limit=limit)
        else:
            l1 = next((name for name, children in taxonomy.topics.items()
                       if topic_id in children.values()), None)
            if l1 is None:
                raise KeyError("unknown topic")
            label = next(label for label, child_id in taxonomy.topics[l1].items() if child_id == topic_id)
            filters = StatsRequest(topic_l1=l1, topic_l2=label, company=company,
                                   position=position, limit=limit)
        with database.session() as session:
            result = query_question_stats(session, filters)
        return _response(request, database, result["data"], filters=filters, extra=result["meta"])

    @app.get("/api/questions/stats")
    def question_stats(request: Request, params: Annotated[StatsRequest, Query()]):
        with database.session() as session:
            result = query_question_stats(session, params, user_id=settings.local_user_id)
        for item in result["data"]:
            scope = sign_scope({"group_by": params.group_by, "key": item["key"],
                                "topic_level": params.topic_level,
                                "filters": params.model_dump(mode="json", exclude={"group_by", "sort", "limit", "cursor", "topic_level", "time_bucket"})},
                               key=signing_key, corpus_revision=result["meta"]["corpus_revision"])
            item["sources_url"] = f"/api/occurrences?scope={quote(scope)}"
        return _response(request, database, result["data"], filters=params, extra=result["meta"])

    @app.get("/api/occurrences")
    def scoped_occurrences(request: Request, scope: str, offset: int = Query(0, ge=0),
                           limit: int = Query(20, ge=1, le=100)):
        with database.session() as session:
            revision = session.get(CorpusState, 1).current_revision
            payload = verify_scope(scope, key=signing_key, corpus_revision=revision)
            filters = FilterSpec.model_validate(payload["filters"])
            result = list_occurrences(session, filters, group_by=payload["group_by"],
                                      group_key=payload["key"], topic_level=payload.get("topic_level", "L2"),
                                      offset=offset, limit=limit)
        return _response(request, database, result["data"], filters=filters,
                         extra={"pagination": {"total": result["total"],
                                               "next_offset": result["next_offset"]}})

    @app.get("/api/questions/search")
    def question_search(request: Request, query: str = Query(min_length=1, max_length=500),
                        filters: Annotated[FilterSpec, Query()] = None,
                        top_k: int = Query(10, ge=1, le=50),
                        pipeline: Literal["BM25", "DENSE", "HYBRID", "HYBRID_RERANK"] = "HYBRID"):
        filters = filters or FilterSpec()
        for attempt in range(2):
            with database.session() as session:
                if session.bind.dialect.name == "postgresql":
                    session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                state = session.get(CorpusState, 1)
                if not retriever or state.current_revision == 0 or state.current_revision != state.indexed_revision:
                    raise ValueError("INDEX_NOT_READY")
                revision = (state.current_revision, state.indexed_revision)
                stages = (["HYBRID_RERANK", "HYBRID", "BM25"] if pipeline == "HYBRID_RERANK"
                          else ["HYBRID", "BM25"] if pipeline == "HYBRID"
                          else ["DENSE", "BM25"] if pipeline == "DENSE" else ["BM25"])
                warnings = []
                for position, executed in enumerate(stages):
                    try:
                        result = search_questions(session, retriever, query, filters,
                                                  pipeline=executed, top_k=top_k)
                        break
                    except Exception as error:
                        if not (isinstance(error, (httpx.HTTPError, APIError, ValidationError)) or
                                str(error) in {"EMBEDDING_NOT_READY", "RERANKER_NOT_READY"}):
                            raise
                        if position + 1 == len(stages):
                            raise
                        warnings.append(f"{executed} failed: {type(error).__name__}; trying {stages[position + 1]}")
            with database.session() as latest:
                current = latest.get(CorpusState, 1)
                if (current.current_revision, current.indexed_revision) == revision:
                    return _response(request, database, result["data"], filters=filters,
                                     extra={**result["meta"], "requested_pipeline": pipeline,
                                            "executed_pipeline": executed,
                                            "degraded": executed != pipeline}, warnings=warnings)
        raise ValueError("SNAPSHOT_CHANGED")

    @app.get("/api/questions/{canonical_id}/occurrences")
    def question_occurrences(request: Request, canonical_id: str,
                             filters: Annotated[FilterSpec, Query()],
                             offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        with database.session() as session:
            if session.get(CanonicalQuestion, canonical_id) is None:
                raise KeyError("QUESTION_NOT_FOUND")
            result = list_occurrences(session, filters, canonical_id=canonical_id, offset=offset, limit=limit)
        return _response(request, database, result["data"], filters=filters,
                         extra={"pagination": {"total": result["total"], "next_offset": result["next_offset"]}})

    @app.get("/api/questions/{canonical_id}")
    def question_detail(request: Request, canonical_id: str, filters: Annotated[FilterSpec, Query()]):
        with database.session() as session:
            detail = get_question_detail(session, canonical_id, filters)
        return _response(request, database, detail, filters=filters)

    @app.get("/api/sources/{revision_id}")
    def source_revision(request: Request, revision_id: str,
                        line_start: int | None = Query(None, ge=1),
                        line_end: int | None = Query(None, ge=1)):
        with database.session() as session:
            revision = session.get(SourceRevision, revision_id)
            if revision is None:
                raise KeyError("SOURCE_REVISION_NOT_FOUND")
            digest = revision.raw_file_hash
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise ValueError("SOURCE_HASH_INVALID")
            snapshot = settings.snapshot_root / f"{digest}.md"
            if not snapshot.is_file():
                raise ValueError("SOURCE_SNAPSHOT_MISSING")
            raw = snapshot.read_bytes()
            if hashlib.sha256(raw).hexdigest() != revision.raw_file_hash:
                raise ValueError("SOURCE_HASH_MISMATCH")
            text = decode_source(raw)
            lines = text.splitlines()
            if line_end is not None and line_start is not None and line_end < line_start:
                raise ValueError("invalid source line range")
            selected = "\n".join(lines[(line_start or 1) - 1:line_end])
            data = {"revision_id": revision.id, "raw_file_hash": revision.raw_file_hash,
                    "line_start": line_start or 1, "line_end": line_end or len(lines),
                    "markdown": selected}
        return _response(request, database, data)

    @app.get("/api/review/state")
    def review_state(request: Request, canonical_question_ids: str | None = None,
                     statuses: str | None = None, limit: int = Query(100, ge=1, le=200)):
        with database.session() as session:
            ids = (canonical_question_ids.split(",") if canonical_question_ids else
                   list(session.scalars(select(CanonicalQuestion.id).where(
                       CanonicalQuestion.lifecycle == "ACTIVE").limit(limit))))
        if len(ids) > 200:
            raise ValueError("too many canonical_question_ids")
        data = review_service.get_states(settings.local_user_id, ids)
        if statuses:
            allowed = set(statuses.split(","))
            data["states"] = {key: value for key, value in data["states"].items()
                              if value["status"] in allowed}
        return _response(request, database, data,
                         extra={"user_state_revision": data["user_state_revision"]})

    @app.post("/api/review", status_code=201)
    def review(request: Request, payload: ReviewRequest):
        data = review_service.record(settings.local_user_id, payload)
        return _response(request, database, data, status_code=201,
                         extra={"user_state_revision": data["user_state_revision"]})

    @app.post("/api/agent/chat")
    def agent_chat(request: Request, payload: ChatRequest):
        data = agent_service.chat(payload.message, request_id=payload.request_id or _request_id(request))
        return _response(request, database, data)

    @app.post("/api/ingest", status_code=202)
    def ingest(request: Request, payload: IngestRequest):
        if not settings.model_api_key or not settings.model_base_url:
            raise ValueError("MODEL_CONFIGURATION_INCOMPLETE")
        validate_backend_model_endpoint(settings.model_base_url)
        validate_model_preflight(api_key=settings.model_api_key,
                                 embedding_dimension=settings.embedding_dimension,
                                 max_calls=settings.max_model_calls,
                                 max_tokens=settings.max_model_tokens)
        paths = ([] if payload.mode == "reindex" else
                 payload.paths if payload.paths is not None else
                 [str(path.relative_to(settings.corpus_root))
                  for path in settings.corpus_root.rglob("*.md")])
        paths = list(dict.fromkeys(paths))
        for path in paths:
            if not resolve_corpus_path(settings.corpus_root, path).is_file():
                raise ValueError(f"unknown corpus file: {path}")
        body = payload.model_dump(mode="json")
        body["paths"] = paths
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        with database.session() as session:
            with session.begin():
                receipt = session.scalar(select(IdempotencyReceipt).where(
                    IdempotencyReceipt.namespace == "ingest", IdempotencyReceipt.actor_id == settings.local_user_id,
                    IdempotencyReceipt.idempotency_key == payload.idempotency_key,
                ))
                if receipt:
                    if receipt.payload_hash != digest:
                        raise ValueError("IDEMPOTENCY_CONFLICT")
                    data = receipt.response_json
                else:
                    run = PipelineRun(pipeline_version="v1", extractor_version="extract_question_v1",
                                      taxonomy_version="v1", embedding_version="configured",
                                      config_snapshot={"paths": paths, "mode": payload.mode},
                                      status="QUEUED")
                    session.add(run)
                    session.flush()
                    data = {"run_id": run.id, "status": run.status, "total_documents": len(paths)}
                    session.add(IdempotencyReceipt(namespace="ingest", actor_id=settings.local_user_id,
                                                   idempotency_key=payload.idempotency_key,
                                                   payload_hash=digest, response_json=data))
        return _response(request, database, data, status_code=202)

    @app.get("/api/ingest/runs/{run_id}")
    def ingest_run(request: Request, run_id: str):
        with database.session() as session:
            run = session.get(PipelineRun, run_id)
            if run is None:
                raise KeyError("RUN_NOT_FOUND")
            data = {"run_id": run.id, "status": run.status,
                    "processed_documents": run.processed_documents,
                    "processed_questions": run.processed_questions,
                    "skipped_documents": run.skipped_documents,
                    "failed_documents": run.failed_documents,
                    "excluded_documents": run.excluded_documents,
                    "needs_review_documents": run.needs_review_documents,
                    "input_tokens": run.input_tokens, "output_tokens": run.output_tokens,
                    "estimated_cost": run.estimated_cost, "config": run.config_snapshot}
        return _response(request, database, data)

    @app.post("/api/ingest/runs/{run_id}/retry-failed", status_code=202)
    def retry_failed(request: Request, run_id: str, payload: RetryRequest):
        with database.session() as session:
            previous = session.get(PipelineRun, run_id)
            if previous is None:
                raise KeyError("RUN_NOT_FOUND")
            if previous.status not in {"FAILED", "PARTIAL_FAILURE"}:
                raise ValueError("run has no failed work")
            failed_paths = previous.config_snapshot.get("failed_paths")
            if failed_paths:
                mode = previous.config_snapshot.get("mode", "changed")
                paths = failed_paths
            elif previous.config_snapshot.get("index_error"):
                mode, paths = "reindex", []
            else:
                mode = previous.config_snapshot.get("mode", "changed")
                paths = previous.config_snapshot.get("paths", [])
        return ingest(request, IngestRequest(paths=paths, mode=mode,
                                            idempotency_key=payload.idempotency_key))

    return app


app = create_app()
