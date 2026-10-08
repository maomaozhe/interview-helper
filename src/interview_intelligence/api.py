"""Local FastAPI interface over the shared domain services."""

from __future__ import annotations

import hashlib
import json
import secrets
import asyncio
import time
from contextvars import ContextVar
from datetime import date
from typing import Annotated, Literal
from uuid import uuid4
from urllib.parse import quote
import httpx
from openai import APIError, APITimeoutError

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field, ValidationError
from sqlalchemy import select

from interview_intelligence.analytics.detail import get_question_detail, list_occurrences
from interview_intelligence.analytics.stats import query_question_stats
from interview_intelligence.analytics.scope import sign_scope, verify_scope
from interview_intelligence.agent.service import AgentService
from interview_intelligence.agent.planner import SearchPlanner
from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec, ListQueryRequest, StructuredQueryRequest, TOOL_ACTIONS, QUERY_AGENT_VERSION, query_model_schema, validate_model_plan
from interview_intelligence.agent.query_service import QueryService, QueryRun
from interview_intelligence.agent.model_gateway import ModelGateway, completion_sse
from interview_intelligence.agent.jev import JevPlanner
from interview_intelligence.agent.preferences import PreferenceUpdate
from interview_intelligence.agent.feedback_memory import MemoryUpdate
from interview_intelligence.agent.history import ConversationHistory
from interview_intelligence.agent.annotation_review import AnnotationReviewer,AnnotationReview,AnnotationPublish
from interview_intelligence.analytics.listing import ListRequest, list_questions
from interview_intelligence.providers.runtime import current_limits
from interview_intelligence.config import Settings, load_settings, resolve_corpus_document, discover_corpus_documents, validate_backend_model_endpoint, validate_model_preflight
from interview_intelligence.contracts import FilterSpec, ReviewRequest, StatsRequest, StrictModel
from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusState, IdempotencyReceipt, PipelineRun, ModelCall,
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
from interview_intelligence.web import register_web
from interview_intelligence.resources import resource_path


model_request_id = ContextVar("model_request_id", default="local-search")
query_run_context = ContextVar("query_run_context", default=None)


class IngestRequest(StrictModel):
    paths: list[str] | None = None
    mode: Literal["changed", "reprocess", "reindex"] = "changed"
    idempotency_key: str = Field(min_length=1)


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    context: list[dict] = Field(default_factory=list, max_length=10)
    request_id: str | None = None
    conversation_id: str | None = Field(default=None, max_length=36)
    expected_version: int | None = Field(default=None, ge=0)


class RetryRequest(StrictModel):
    idempotency_key: str = Field(min_length=1)


class SearchRequest(FilterSpec):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=10, ge=1, le=50)
    pipeline: Literal["BM25", "DENSE", "HYBRID", "HYBRID_RERANK"] = "HYBRID_RERANK"


def _request_id(request: Request) -> str:
    if not getattr(request.state, "request_id", None):
        request.state.request_id = (request.headers.get("x-request-id") or str(uuid4()))[:128]
    return request.state.request_id


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


def create_app(database=None, settings: Settings | None = None, retriever=None, *, query_planner=None) -> FastAPI:
    settings = settings or load_settings()
    database = database or create_database(settings.database_url)
    planner = None
    def record_api_call(entry):
        run = query_run_context.get()
        if run and not entry.get("request_id"):
            for key in ("queue_ms", "interval_ms", "provider_ms"):
                run.call_timings[key] += entry.get(key) or 0
            if entry.get("_token_charge") is not None and type(entry.get("input_tokens")) is int:
                output=entry.get("output_tokens")
                if type(output) is int or entry["operation_type"]=="EMBEDDING":
                    run.limits.reconcile_tokens(entry["_token_charge"],entry["input_tokens"]+(output or 0))
        with database.session() as session, session.begin():
            session.add(ModelCall(request_id=entry.get("request_id") or model_request_id.get(), operation_type=entry["operation_type"],
                query_run_id=entry.get("query_run_id") or (run.id if run else None),
                token_budget_charge=entry.get("token_budget_charge") if "token_budget_charge" in entry else
                    (entry["input_tokens"]+(entry.get("output_tokens") or 0) if type(entry.get("input_tokens")) is int else entry.get("_token_charge")),
                model=entry["model"], model_revision=entry.get("model_revision"),
                prompt_version=entry["prompt_version"], input_tokens=entry.get("input_tokens"),
                output_tokens=entry.get("output_tokens"), latency_ms=entry["latency_ms"],
                queue_ms=entry.get("queue_ms"), interval_ms=entry.get("interval_ms"), provider_ms=entry.get("provider_ms"),
                ttft_ms=entry.get("ttft_ms"),
                status=entry["status"], retry_count=entry["retry_count"], error_code=entry.get("error_code"),
                usage_source="PROVIDER" if entry.get("input_tokens") is not None else "UNAVAILABLE"))
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
                                    budget=budget, call_gate=gate, on_call=record_api_call,
                                    timeout_seconds=settings.model_request_timeout_seconds)
            if settings.reranker_model:
                reranker = LLMReranker(model=settings.reranker_model,
                                       api_key=settings.model_api_key, base_url=settings.model_base_url,
                                       budget=budget, call_gate=gate, on_call=record_api_call,
                                       timeout_seconds=settings.model_request_timeout_seconds, stream=True)
                planner = SearchPlanner(client=reranker.client, model=settings.judge_model or settings.reranker_model,
                                        budget=budget, call_gate=gate, on_call=record_api_call)
        retriever = ElasticsearchRetriever(settings.elasticsearch_url, encoder, reranker=reranker)
    review_service = ReviewService(database)
    agent_service = AgentService(database, retriever, user_id=settings.local_user_id, planner=planner)
    signing_key = settings.app_signing_key or secrets.token_hex(32)
    query_service = QueryService(database, retriever, user_id=settings.local_user_id,
                                 signing_key=signing_key, deadline_seconds=settings.query_deadline_seconds,
                                 max_tokens=settings.query_max_tokens,task_filters_enabled=settings.task_filters_enabled,
                                 task_annotation_policy=settings.task_annotation_policy,
                                 compact_context=settings.query_compact_context,
                                 dynamic_tools=settings.query_dynamic_tools,
                                 prompt_version=settings.query_prompt_version,
                                 feedback_root=settings.snapshot_root.parent / "feedback")
    history = ConversationHistory(database, settings.local_user_id, signing_key)
    query_gate = ModelCallGate(settings.model_lock_path, minimum_interval_seconds=settings.model_min_interval_seconds)
    gateway = ModelGateway(settings, query_gate, record_api_call, query_service.journal.event)
    jev = JevPlanner(settings, database, query_gate, record_api_call) if settings.jev_decision_enabled and settings.jev_api_key else None
    app = FastAPI(title="Interview Intelligence", version="1.0.0", root_path=settings.api_root_path)
    app.state.query_service = query_service
    annotation_reviewer=AnnotationReviewer(database,settings.local_user_id,signing_key)

    @app.get("/api/task-annotations")
    def task_annotations(request:Request,cursor:str|None=None,limit:int=Query(default=20,ge=1,le=50)):
        return _response(request,database,annotation_reviewer.queue(cursor,limit))
    @app.patch("/api/task-annotations/{occurrence_id}")
    def stage_annotation(request:Request,occurrence_id:str,payload:AnnotationReview):
        return _response(request,database,annotation_reviewer.stage(occurrence_id,payload))
    @app.post("/api/task-annotations/publish")
    def publish_annotations(request:Request,payload:AnnotationPublish):
        return _response(request,database,annotation_reviewer.publish(payload))

    def internal_run(request, run_id):
        expected = settings.internal_agent_token
        provided = request.headers.get("authorization", "")
        if not expected or not secrets.compare_digest(provided, f"Bearer {expected}"):
            raise HTTPException(status_code=401, detail="internal agent authentication required")
        run = query_service.runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run expired")
        run.limits.check()
        return run

    async def execute_query(payload: QueryRequest, request: Request | None, prepared_run=None):
        started = time.perf_counter()
        run = prepared_run or await asyncio.to_thread(query_service.begin, payload)
        if not isinstance(run, QueryRun):
            return run
        limits_token = current_limits.set(run.limits)
        trace_token = model_request_id.set(payload.request_id)
        run_token = query_run_context.set(run)
        async def work():
            if run.resume.get("write_done"):
                run.result,run.state,run.terminal=run.resume["result"],run.resume["state"],True
                return await asyncio.to_thread(query_service.finish,run,provider="recovered_receipt")
            if run.resume.get("write"):
                run.tool_calls=run.resume["write_ordinal"]-1
                await asyncio.to_thread(query_service.execute,run,"record_review",QuerySpec.model_validate(run.resume["write"]))
                return await asyncio.to_thread(query_service.finish,run,provider="recovered_write")
            if run.terminal and run.result:
                return await asyncio.to_thread(query_service.finish,run,provider="recovered_read")
            if isinstance(payload, StructuredQueryRequest):
                params = payload.list_request
                plan = QuerySpec(action="LIST", filters=payload.filters, sort=params.sort,
                    top_n=params.top_n, page_size=params.page_size,review_statuses=params.review_statuses,
                    review_order=params.review_order)
                await asyncio.to_thread(query_service.execute, run, "list_questions", plan)
                return await asyncio.to_thread(query_service.finish, run, provider="structured_sql",
                    timings={"decision_ms": 0, "total_ms": int((time.perf_counter() - started) * 1000),
                             "model_attempts": 0, **run.call_timings})
            context = await asyncio.to_thread(query_service.model_context, run)
            plan_started = time.perf_counter()
            plan = await asyncio.to_thread(jev.plan, run, context) if jev else None
            if jev and settings.jev_decision_mode == "shadow":
                run.decision["candidate_spec"] = plan.model_dump(mode="json") if plan else None
                plan = None
            provider = settings.jev_provider if plan else ""
            if plan is None and query_planner:
                plan = query_planner.plan(run, context)
                if hasattr(plan, "__await__"):
                    plan = await plan
                plan = QuerySpec.model_validate(plan)
                provider = "injected"
            elif plan is None and settings.pi_agent_enabled:
                if not settings.internal_agent_token:
                    raise ValueError("MODEL_CONFIGURATION_INCOMPLETE")
                try:
                    async with httpx.AsyncClient(timeout=settings.query_deadline_seconds, trust_env=False) as client:
                        response = await client.post(settings.pi_agent_url.rstrip("/") + "/run",
                        headers={"Authorization": f"Bearer {settings.internal_agent_token}"},
                        json={"run_id": run.id, "conversation_id": run.conversation_id, "context": context,
                              "schema": query_model_schema(),
                              "prompt": resource_path(f"prompts/{settings.query_prompt_version}.md").read_text(encoding="utf-8"),
                              "timeout_ms": int(max(0.01, run.limits.deadline - time.monotonic()) * 1000)})
                        response.raise_for_status()
                        if not run.result or not run.terminal:
                            raise ValueError("QUERY_PLAN_MISSING")
                    provider = "pi"
                except httpx.HTTPError:
                    durable=await asyncio.to_thread(query_service.journal.resume,run.id)
                    if durable["write"] and not run.terminal:
                        raise ValueError("QUERY_WRITE_INTERRUPTED")
                    if run.terminal:
                        provider="pi_recovered_response"
                    else:
                        plan=await gateway.plan(run,await asyncio.to_thread(query_service.model_context,run))
                        provider="model_json_fallback"
                    if run.result: run.result.setdefault("warnings",[]).append("Pi 服务未完成响应，已使用受限回退或已提交工具回执。")
            elif plan is None:
                plan = await gateway.plan(run, context)
                provider = "model_json"
            plan_ms = int((time.perf_counter() - plan_started) * 1000)
            while plan:
                name = next(name for name, actions in TOOL_ACTIONS.items() if plan.action in actions)
                await asyncio.to_thread(query_service.execute, run, name, plan)
                if plan.final: break
                plan=await gateway.plan(run,await asyncio.to_thread(query_service.model_context,run))
            if run.decision:
                run.result["planning"]["decision"] = run.decision
            if provider == "pi":
                plan_ms = max(0, plan_ms - run.result["facts"]["meta"]["timings"]["tool_ms"])
            return await asyncio.to_thread(query_service.finish, run, provider=provider,
                timings={"decision_ms": plan_ms, "total_ms": int((time.perf_counter() - started) * 1000),
                         "model_attempts": run.limits.calls, **run.call_timings})
        task = asyncio.create_task(work())
        try:
            while not task.done():
                if request is not None and await request.is_disconnected():
                    raise ValueError("QUERY_CANCELLED")
                if await asyncio.to_thread(query_service.journal.is_cancelled,run.id):
                    raise ValueError("QUERY_CANCELLED")
                run.limits.check()
                await asyncio.sleep(0.05)
            return await task
        except BaseException as failure:
            run.limits.cancelled.set()
            task.cancel()
            try:
                await task
            except BaseException:
                pass
            await asyncio.to_thread(query_service.fail, run, failure)
            raise
        finally:
            current_limits.reset(limits_token)
            model_request_id.reset(trace_token)
            query_run_context.reset(run_token)

    @app.post("/internal/agent/runs/{run_id}/v1/chat/completions")
    async def pi_model(request: Request, run_id: str):
        run = internal_run(request, run_id)
        payload=await request.json()
        async def streaming():
            from interview_intelligence.agent.presentation import ClarificationStream
            presentation = ClarificationStream()
            queue=asyncio.Queue(maxsize=32)
            async def emit(chunk):
                question = presentation.update(chunk)
                if question:
                    await asyncio.to_thread(query_service.journal.event,run.id,"clarification_delta",question)
                text_delta="".join(c.get("delta",{}).get("content") or "" for c in chunk.get("choices",[]))
                if text_delta:
                    await asyncio.to_thread(query_service.journal.event,run.id,"text_delta",{"text":text_delta,"temporary":True})
                await queue.put("data: "+json.dumps(chunk)+"\n\n")
            async def produce():
                try:
                    response=await gateway.complete(run,payload,emit=emit)
                    await asyncio.to_thread(query_service.journal.event,run.id,"model_result",{
                        "message":response["choices"][0]["message"],"usage":response.get("usage")})
                    for chunk in list(completion_sse(response))[1:]: await queue.put(chunk)
                except Exception as e:
                    await queue.put("data: "+json.dumps({"error":{"code":str(e).split(":")[0]}})+"\n\n")
                await queue.put(None)
            task=asyncio.create_task(produce())
            try:
                while True:
                    chunk=await queue.get()
                    if chunk is None: break
                    yield chunk
            finally:
                if not task.done(): task.cancel()
                try: await task
                except asyncio.CancelledError: pass
        return StreamingResponse(streaming(),media_type="text/event-stream")

    @app.post("/internal/agent/runs/{run_id}/events")
    async def pi_event(request:Request,run_id:str):
        internal_run(request,run_id)
        event=await request.json()
        if event.get("type") not in {"turn_start","message_end","tool_execution_start","tool_execution_end","turn_end","agent_end"}:
            raise ValueError("INVALID_AGENT_EVENT")
        if len(json.dumps(event))>512000: raise ValueError("QUERY_EVENT_TOO_LARGE")
        await asyncio.to_thread(query_service.journal.event,run_id,"pi_event",event)
        return {"saved":True}

    @app.post("/internal/agent/runs/{run_id}/tools/{tool_name}")
    async def pi_tool(request: Request, run_id: str, tool_name: str):
        run = internal_run(request, run_id)
        plan = validate_model_plan(await request.json())
        trace_token = model_request_id.set(run.request.request_id)
        run_token = query_run_context.set(run)
        try:
            return await asyncio.to_thread(query_service.execute, run, tool_name, plan)
        finally:
            model_request_id.reset(trace_token)
            query_run_context.reset(run_token)

    def library_response(request, result):
        facts = result["facts"]
        return _response(request, database, facts.get("data", []),
                         extra={**facts["meta"], "conversation_id": result["conversation_id"],
                                "run_id": result["run_id"],
                                "conversation_version": result["conversation_version"],
                                "answer": result["answer"], "planning": result["planning"], "tool_trace": result["tool_trace"]},
                         warnings=result.get("warnings"))

    @app.post("/api/query")
    @app.post("/api/questions/query")
    async def question_query(request: Request, payload: QueryRequest):
        return library_response(request, await execute_query(payload, request))

    @app.post("/api/questions/list")
    async def question_list_with_context(request: Request, payload: ListQueryRequest):
        return library_response(request, await execute_query(payload.as_query(), request))

    @app.get("/api/preferences")
    def preferences(request:Request):
        return _response(request,database,query_service.preferences.list())

    @app.get("/api/query-memory")
    def query_memories(request: Request):
        return _response(request, database, query_service.feedback_memory.list())

    @app.patch("/api/query-memory/{feedback_id}")
    def update_query_memory(request: Request, feedback_id: str, payload: MemoryUpdate):
        return _response(request, database, query_service.feedback_memory.update(feedback_id, payload))

    @app.get("/api/query-memory/export")
    def export_query_memories(request: Request):
        return _response(request, database, query_service.feedback_memory.export())

    @app.get("/api/preferences/{key}")
    def preference(request:Request,key:str):
        return _response(request,database,query_service.preferences.get(key))

    @app.patch("/api/preferences/{key}")
    def update_preference(request:Request,key:str,payload:PreferenceUpdate):
        return _response(request,database,query_service.preferences.update(key,payload))

    @app.delete("/api/preferences/{key}")
    def delete_preference(request:Request,key:str,expected_version:int=Query(ge=0)):
        return _response(request,database,query_service.preferences.delete(key,expected_version))

    @app.post("/api/conversations")
    def create_conversation(request:Request):
        from interview_intelligence.domain.models import AgentConversation
        with database.session() as s,s.begin():
            c=AgentConversation(user_id=settings.local_user_id,state={})
            s.add(c);s.flush()
            result={"conversation_id":c.id,"version":c.version,"state":c.state}
        return _response(request,database,result,status_code=201)

    @app.get("/api/conversations")
    def conversations(request: Request, q: str = Query(default="", max_length=200),
                      cursor: str | None = Query(default=None, max_length=4096),
                      limit: int = Query(default=30, ge=1, le=50)):
        return _response(request, database, history.conversations(q, cursor, limit))

    @app.get("/api/conversations/{conversation_id}")
    def conversation(request:Request,conversation_id:str,
                     cursor: str | None = Query(default=None, max_length=4096),
                     limit: int = Query(default=20, ge=1, le=50)):
        return _response(request,database,history.turns(conversation_id, cursor, limit))

    live_tasks={}
    @app.post("/api/conversations/{conversation_id}/messages",status_code=202)
    async def conversation_message(request:Request,conversation_id:str,payload:QueryRequest):
        if payload.conversation_id and payload.conversation_id!=conversation_id:
            raise ValueError("CONVERSATION_VERSION_CONFLICT")
        payload=payload.model_copy(update={"conversation_id":conversation_id})
        try: run=await asyncio.to_thread(query_service.begin,payload)
        except ValueError as error:
            if str(error)!="QUERY_IN_PROGRESS": raise
            try: existing=await asyncio.to_thread(query_service.journal.request_view,payload.request_id)
            except KeyError: raise error
            return _response(request,database,existing,status_code=202)
        if not isinstance(run,QueryRun):
            from interview_intelligence.domain.models import AgentTurn
            with database.session() as s:
                t=s.scalar(select(AgentTurn).where(AgentTurn.user_id==settings.local_user_id,
                    AgentTurn.request_id==payload.request_id))
                run_id=t.id
            return _response(request,database,{"run_id":run_id,"status":"SUCCEEDED"},status_code=200)
        async def background():
            try: await execute_query(payload,None,prepared_run=run)
            except (Exception,asyncio.CancelledError): pass  # execute_query persists terminal failure first.
        task=asyncio.create_task(background())
        live_tasks[run.id]=task
        task.add_done_callback(lambda _:live_tasks.pop(run.id,None))
        return _response(request,database,{"run_id":run.id,"conversation_id":run.conversation_id,
                                          "status":"RUNNING"},status_code=202)

    @app.get("/api/runs/{run_id}")
    def query_run_status(request:Request,run_id:str,after:int=Query(default=0,ge=0)):
        return _response(request,database,query_service.journal.view(run_id,after))

    @app.get("/api/query/receipts/{request_id}")
    def query_receipt(request:Request,request_id:str):
        return _response(request,database,query_service.journal.request_view(request_id))

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_query(request:Request,run_id:str):
        run=query_service.runs.get(run_id)
        if run: run.limits.cancelled.set()
        view=await asyncio.to_thread(query_service.journal.cancel,run_id)
        return _response(request,database,view)

    @app.get("/api/runs/{run_id}/events")
    async def query_events(request:Request,run_id:str,after:int=Query(default=0,ge=0)):
        supplied=request.headers.get("last-event-id")
        if supplied:
            try: after=max(after,int(supplied))
            except ValueError: raise ValueError("INVALID_EVENT_CURSOR")
        await asyncio.to_thread(query_service.journal.view,run_id,after)  # Ownership check before headers.
        async def events():
            cursor=after
            while True:
                view=await asyncio.to_thread(query_service.journal.view,run_id,cursor)
                for e in view["events"]:
                    cursor=e["sequence"]
                    yield f"id: {cursor}\nevent: {e['type']}\ndata: "+json.dumps(e["data"],ensure_ascii=False)+"\n\n"
                if view["status"]!="RUNNING" and cursor>=view["last_sequence"]: break
                if await request.is_disconnected(): break
                if not view["events"]: yield ": heartbeat\n\n"
                await asyncio.sleep(.25)
        return StreamingResponse(events(),media_type="text/event-stream",headers={
            "Cache-Control":"no-cache, no-transform", "X-Accel-Buffering":"no"})

    @app.get("/api/questions/list")
    def question_list(request: Request, params: Annotated[ListRequest, Query()]):
        if not settings.task_filters_enabled and (params.coding_focus or params.response_form):
            raise ValueError("TASK_FILTERS_DISABLED")
        if (params.coding_focus or params.response_form) and not params.annotation_status:
            params=params.model_copy(update={"annotation_status":settings.task_annotation_policy})
        with database.session() as session:
            if session.bind.dialect.name == "postgresql":
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            result = list_questions(session, params, signing_key=signing_key, user_id=settings.local_user_id)
        return _response(request, database, result["data"], extra=result["meta"], warnings=result["warnings"])

    @app.middleware("http")
    async def model_trace(request: Request, call_next):
        token = model_request_id.set(_request_id(request))
        try:
            return await call_next(request)
        finally:
            model_request_id.reset(token)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {
            "code": "VALIDATION_ERROR", "message": "invalid request", "details": {"errors": jsonable_encoder(error.errors(), custom_encoder={ValueError: str})},
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
        status = 409 if code in {"IDEMPOTENCY_CONFLICT", "USER_STATE_VERSION_CONFLICT", "SNAPSHOT_CHANGED",
                               "CONVERSATION_VERSION_CONFLICT", "QUERY_IN_PROGRESS","PREFERENCE_VERSION_CONFLICT",
                               "REQUERY_SOURCE_NOT_READY"} else 400
        if code in {"INDEX_NOT_READY", "EMBEDDING_NOT_READY", "RERANKER_NOT_READY",
                    "MODEL_CONFIGURATION_INCOMPLETE", "MODEL_NOT_READY", "SOURCE_SNAPSHOT_MISSING",
                    "STREAM_INCOMPLETE", "MODEL_OUTPUT_TRUNCATED", "QUERY_DEADLINE_EXCEEDED", "LEXICAL_FACET_RETRIEVAL_FAILED",
                    "QUERY_PLAN_MISSING", "QUERY_MODEL_BUDGET_EXCEEDED"}:
            status = 503
        return JSONResponse(status_code=status, content={"error": {
            "code": code, "message": message, "retryable": status == 503},
            "request_id": _request_id(request)})

    @app.exception_handler(APIError)
    async def provider_error(request: Request, error: APIError):
        code = "MODEL_PROVIDER_TIMEOUT" if isinstance(error, APITimeoutError) else "MODEL_PROVIDER_UNAVAILABLE"
        return JSONResponse(status_code=503, content={"error": {
            "code": code, "message": "模型请求未完成，请稍后重试。",
            "retryable": getattr(error, "status_code", None) not in {400, 401, 403, 404, 422}},
            "request_id": _request_id(request)})

    @app.exception_handler(httpx.HTTPError)
    async def http_provider_error(request: Request, error: httpx.HTTPError):
        return JSONResponse(status_code=503, content={"error": {
            "code": "MODEL_PROVIDER_TIMEOUT" if isinstance(error, httpx.TimeoutException) else "MODEL_PROVIDER_UNAVAILABLE",
            "message": "模型或 Agent 服务暂时不可用。", "retryable": True}, "request_id": _request_id(request)})

    @app.get("/api/health")
    def health(request: Request):
        with database.session() as session:
            state = session.get(CorpusState, 1)
            data = {"database": "ready", "current_revision": state.current_revision,
                    "indexed_revision": state.indexed_revision,
                    "index": "ready" if retriever and state.current_revision > 0 and state.indexed_revision == state.current_revision else "not_ready",
                    "model_max_in_flight": 1,
                    "model_min_interval_seconds": settings.model_min_interval_seconds,
                    "query_router_enabled": settings.query_router_enabled or query_planner is not None,
                    "task_filters_enabled":settings.task_filters_enabled,
                    "task_annotation_policy":settings.task_annotation_policy,
                    "pi_agent_enabled": settings.pi_agent_enabled,
                    "query_prompt_version": settings.query_prompt_version,
                    "query_prompt_sha256": hashlib.sha256(resource_path(
                        f"prompts/{settings.query_prompt_version}.md").read_bytes()).hexdigest(),
                    "reranker_version": getattr(getattr(retriever, "reranker", None), "version", None),
                    "jev_decision_enabled": jev is not None,
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
    def question_search(request: Request, params: Annotated[SearchRequest, Query()]):
        query, pipeline, top_k = params.query, params.pipeline, params.top_k
        filters = FilterSpec.model_validate(params.model_dump(exclude={"query", "pipeline", "top_k"}))
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
    async def agent_chat(request: Request, payload: ChatRequest):
        if settings.query_router_enabled or query_planner is not None:
            result = await execute_query(QueryRequest(message=payload.message,
                conversation_id=payload.conversation_id, expected_version=payload.expected_version,
                request_id=payload.request_id or _request_id(request)), request)
            return _response(request, database, result, extra=result["facts"]["meta"], warnings=result.get("warnings"))
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
                 [path.relative_to(settings.corpus_root.resolve()).as_posix()
                  for path in discover_corpus_documents(settings.corpus_root)])
        paths = list(dict.fromkeys(paths))
        for path in paths:
            if not resolve_corpus_document(settings.corpus_root, path).is_file():
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
                    run = PipelineRun(pipeline_version="v1", extractor_version="extract_question_v2",
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

    register_web(app, database, settings, _response)
    return app


app = create_app()
