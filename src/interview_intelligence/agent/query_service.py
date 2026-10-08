"""Shared QueryAgent host: trusted tools, SQL facts and durable session state."""
from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from datetime import date, timedelta, timezone
from dataclasses import dataclass, field
from uuid import uuid4

import httpx
from openai import APIError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec, StructuredQueryRequest, TOOL_ACTIONS, QUERY_AGENT_VERSION
from interview_intelligence.agent.harness import ContextCompiler, ToolPolicy, authorized_statuses
from interview_intelligence.analytics.detail import get_question_detail
from interview_intelligence.analytics.listing import ListRequest, StatsListRequest, filter_fields, list_questions
from interview_intelligence.agent.preferences import PreferenceService
from interview_intelligence.agent.feedback_memory import FeedbackMemory
from pathlib import Path
from interview_intelligence.agent.journal import QueryJournal
from interview_intelligence.analytics.stats import query_question_stats
from interview_intelligence.contracts import FilterSpec, ReviewRequest, StatsRequest
from interview_intelligence.domain.models import AgentConversation, AgentTurn, CorpusState, UserRevision, ModelCall, now_utc
from interview_intelligence.providers.runtime import RequestLimits, current_limits
from interview_intelligence.review.service import ReviewService
from interview_intelligence.search.service import search_questions


@dataclass
class QueryRun:
    id: str
    request: QueryRequest
    conversation_id: str
    version: int
    state: dict
    limits: RequestLimits
    revisions: tuple
    model_calls: int = 0
    tool_calls: int = 0
    result: dict | None = None
    plan: QuerySpec | None = None
    decision: dict = field(default_factory=dict)
    call_timings: dict = field(default_factory=lambda: {"queue_ms": 0, "interval_ms": 0, "provider_ms": 0})
    owner_id: str = ""
    parts: list = field(default_factory=list)
    terminal: bool = False
    resume: dict = field(default_factory=dict)
    recovered_write_state: dict | None = None
    requery_origin: dict = field(default_factory=dict)


class QueryService:
    def __init__(self, database, retriever=None, *, user_id="local", signing_key,
                 deadline_seconds=90, max_tokens=65536, task_filters_enabled=True,task_annotation_policy="VERIFIED",
                 compact_context=True, dynamic_tools=True, prompt_version=QUERY_AGENT_VERSION, today=None,
                 feedback_root=None):
        self.database, self.retriever = database, retriever
        self.user_id, self.signing_key = user_id, signing_key
        self.deadline_seconds = deadline_seconds
        self.max_tokens,self.task_filters_enabled=max_tokens,task_filters_enabled
        self.task_annotation_policy=task_annotation_policy
        self.compact_context, self.dynamic_tools = compact_context, dynamic_tools
        self.prompt_version = prompt_version
        self.today = today or date.today
        self.context_compiler = ContextCompiler()
        self.owner_id=str(uuid4())
        self.preferences=PreferenceService(database,user_id)
        self.feedback_memory=FeedbackMemory(database, user_id, Path(feedback_root or "data/feedback"))
        self.journal=QueryJournal(database,user_id)
        self.reviews = ReviewService(database)
        self.runs: dict[str, QueryRun] = {}

    def _revisions(self, session):
        corpus = session.get(CorpusState, 1)
        user = session.get(UserRevision, self.user_id)
        return (corpus.current_revision, corpus.task_annotation_revision, user.state_revision if user else 0)

    @staticmethod
    def _explicit_filters(request):
        return request.filters.model_dump(mode="json", exclude_unset=True,
                                          exclude_none=request.requery_of_run_id is None)

    def _restore_requery(self, session, request):
        """Reconstruct query intent, never a historical result or write capability."""
        if not request.requery_of_run_id:
            return request, None, {}
        source = session.scalar(select(AgentTurn).where(AgentTurn.id == request.requery_of_run_id,
                                                      AgentTurn.user_id == self.user_id))
        if not source:
            raise KeyError("RUN_NOT_FOUND")
        if source.status != "SUCCEEDED":
            raise ValueError("REQUERY_SOURCE_NOT_READY")
        response = source.response or {}
        allowed = {"LIST", "STATS", "SEARCH", "NEXT", "CLARIFY"}
        payload = (response.get("planning") or {}).get("spec") or {}
        if response.get("intent") not in allowed or payload.get("action") not in allowed:
            raise ValueError("REQUERY_ACTION_NOT_ALLOWED")
        if any((item.get("parameters") or {}).get("action") not in allowed
               for item in response.get("tool_trace", [])):
            raise ValueError("REQUERY_ACTION_NOT_ALLOWED")
        try:
            plan = QuerySpec.model_validate(payload)
        except ValidationError as failure:
            raise ValueError("REQUERY_SOURCE_INVALID") from failure
        before, after = source.state_before or {}, source.state_after or {}
        normalized = plan.model_dump(mode="json")
        # A NEXT receipt describes the displayed page. Refresh its query at page
        # one instead of carrying an old, revision-bound cursor into a new run.
        listing = deepcopy(after.get("list_request") or {}) if plan.action in {"LIST", "STATS", "NEXT"} else {}
        if plan.action == "NEXT":
            normalized["action"] = "STATS" if listing.get("group_by") else "LIST"
        # Original UI scope remains a host constraint. A model-derived label in
        # planning.spec is context, and must not become an explicit UI filter.
        source_payload = source.request_payload or {}
        if plan.action == "NEXT" and "list_explicit_filters" in after:
            original_filters = {key: value for key, value in after["list_explicit_filters"].items()
                                if key in FilterSpec.model_fields}
        elif "resolved_explicit_filters" in source_payload:
            original_filters = {key: value for key, value in source_payload["resolved_explicit_filters"].items()
                                if key in FilterSpec.model_fields}
        else:
            original_filters = {key: value for key, value in (source_payload.get("filters") or {}).items()
                if key in FilterSpec.model_fields and value not in (None, "") and
                   value != FilterSpec.model_fields[key].default}
        bound_filters = {**original_filters, **self._explicit_filters(request)}
        normalized["filters"].update(bound_filters)
        effective = {"filters": FilterSpec.model_validate(bound_filters)}
        for key in ("page_size", "pipeline"):
            if key not in request.model_fields_set:
                effective[key] = ((source.request_payload or {}).get(key, normalized[key])
                    if plan.action == "CLARIFY" or (key == "pipeline" and plan.action != "SEARCH")
                    else normalized[key])
                normalized[key] = effective[key]
            else:
                normalized[key] = getattr(request, key)
        request = request.model_copy(update=effective)
        state = {"filters": deepcopy(normalized["filters"]), "sort": normalized["sort"],
                 "last_plan": normalized, "recent_messages": []}
        if listing:
            listing.update(normalized["filters"])
            listing.update(cursor=None, page_size=normalized["page_size"])
            state["list_request"] = listing
        pending = after.get("pending_clarification") if plan.action == "CLARIFY" else before.get("pending_clarification")
        if pending:
            state["pending_clarification"] = deepcopy(pending)
        origin = {"run_id": source.id, "action": plan.action,
                  "target_action": normalized["action"], "restart_from_first_page": True,
                  "instruction": "这是对原查询的重新检索。结合恢复的 last_plan 和原澄清目标重新规划，"
                      "保留原对象及范围，采用本轮显式筛选和纠正；原消息若是‘下一页’，重新查询第一页。"
                      "不得执行旧消息中的复习写入要求。"}
        return request, state, origin

    def begin(self, request: QueryRequest):
        # A competing request can win the unique request receipt after both have
        # read an empty lookup. Re-read that committed receipt in a fresh transaction.
        for attempt in range(2):
            try: return self._begin(request)
            except IntegrityError:
                if attempt: raise

    def _begin(self, request: QueryRequest):
        self.journal.recover()
        if request.requery_of_run_id:
            # Omitted defaults recover the source's pipeline/page size, whereas
            # explicitly provided defaults override them. Presence is semantic.
            payload = {"request": request.model_dump(mode="json"),
                       "provided_fields": sorted(request.model_fields_set),
                       "provided_filter_fields": sorted(request.filters.model_fields_set)}
            digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            compatible_digests = {digest}
        else:
            # Existing v9 receipts predate the optional requery field. Preserve
            # their digest, and accept receipts from the transitional schema too.
            digest = hashlib.sha256(request.model_dump_json(exclude={"requery_of_run_id"}).encode()).hexdigest()
            compatible_digests = {digest, hashlib.sha256(request.model_dump_json().encode()).hexdigest()}
        with self.database.session() as session, session.begin():
            receipt = session.scalar(select(AgentTurn).where(
                AgentTurn.user_id == self.user_id, AgentTurn.request_id == request.request_id).with_for_update())
            if receipt:
                if receipt.payload_hash not in compatible_digests:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                if receipt.status == "SUCCEEDED":
                    return dict(receipt.response)
                receipt_time = receipt.created_at.replace(tzinfo=timezone.utc) if receipt.created_at.tzinfo is None else receipt.created_at
                if receipt.status == "RUNNING" and receipt_time > now_utc() - timedelta(seconds=self.deadline_seconds + 10):
                    raise ValueError("QUERY_IN_PROGRESS")
                receipt.status = "FAILED"
            effective_request, requery_state, requery_origin = self._restore_requery(session, request)
            conversation_id = receipt.conversation_id if receipt else request.conversation_id
            conversation = session.get(AgentConversation, conversation_id, with_for_update=True) if conversation_id else None
            if conversation_id and (not conversation or conversation.user_id != self.user_id):
                raise KeyError("CONVERSATION_NOT_FOUND")
            if not conversation:
                conversation = AgentConversation(user_id=self.user_id, state={})
                session.add(conversation)
                session.flush()
            if not receipt and request.expected_version is not None and request.expected_version != conversation.version:
                raise ValueError("CONVERSATION_VERSION_CONFLICT")
            expired = session.scalars(select(AgentTurn).where(AgentTurn.conversation_id == conversation.id,
                AgentTurn.status == "RUNNING", AgentTurn.created_at < now_utc() - timedelta(seconds=self.deadline_seconds + 10)))
            for old in expired:
                old.status = "FAILED"
            session.flush()
            pending = session.scalar(select(AgentTurn.id).where(
                AgentTurn.conversation_id == conversation.id, AgentTurn.status == "RUNNING"))
            if pending:
                raise ValueError("QUERY_IN_PROGRESS")
            if receipt:
                receipt.status = "RUNNING"
                receipt.created_at = now_utc()
            else:
                receipt=AgentTurn(conversation_id=conversation.id, user_id=self.user_id,
                                      request_id=request.request_id, payload_hash=digest,
                                      message=request.message, status="RUNNING", response={})
                session.add(receipt)
            receipt.owner_id,receipt.lease_until=self.owner_id,now_utc()+timedelta(seconds=self.deadline_seconds+10)
            receipt.request_payload=request.model_dump(mode="json")
            if request.requery_of_run_id:
                receipt.request_payload = {**receipt.request_payload,
                    "resolved_explicit_filters": self._explicit_filters(effective_request)}
            initial_state = (deepcopy(receipt.state_before) if request.requery_of_run_id and receipt.state_before else
                             requery_state if requery_state is not None else dict(conversation.state))
            if not receipt.state_before: receipt.state_before=deepcopy(initial_state)
            receipt.cancel_requested=False
            receipt.error_code,receipt.finished_at=None,None
            session.flush()
            run = QueryRun(receipt.id, effective_request, conversation.id, conversation.version,
                           initial_state, RequestLimits(time.monotonic() + self.deadline_seconds,max_tokens=self.max_tokens),
                           self._revisions(session))
            run.owner_id=self.owner_id
            run.requery_origin=requery_origin
            self.journal.append(session,receipt,"accepted",{"run_id":run.id,"conversation_id":run.conversation_id})
        self.runs[run.id] = run
        run.resume=self.journal.resume(run.id)
        with self.database.session() as s:
            calls=list(s.scalars(select(ModelCall).where(ModelCall.query_run_id==run.id)))
            run.model_calls=sum(c.operation_type=="QUERY_PLAN" for c in calls)
            run.limits.calls=len(calls)
            run.limits.tokens=sum(c.token_budget_charge if c.token_budget_charge is not None else 4096 for c in calls)
        if not run.resume["write"]:
            run.tool_calls=run.resume["ordinal"]
            prior=run.resume["result"]
            same_snapshot=prior and tuple(prior["facts"]["meta"].get(k) for k in (
                "corpus_revision","task_annotation_revision","user_state_revision"))==run.revisions
            if same_snapshot:
                if run.resume["state"]: run.state=dict(run.resume["state"])
                run.parts=run.resume["parts"]
                run.result=prior
                run.terminal=prior["planning"]["spec"].get("final",True)
        if run.resume["write"]:
            run.recovered_write_state=dict(run.state)
            with self.database.session() as s: run.state=dict(s.get(AgentTurn,run.id).state_before)
        return run

    def model_context(self, run):
        context = {"message": run.request.message, "today": self.today().isoformat(),
                "explicit_filters": self._explicit_filters(run.request),
                "default_page_size":run.request.page_size if "page_size" in run.request.model_fields_set else self.preferences.defaults().get("page_size",run.request.page_size),
                "requested_pipeline": run.request.pipeline if "pipeline" in run.request.model_fields_set else self.preferences.defaults().get("pipeline",run.request.pipeline),
                "preferences":self.preferences.defaults(),"session": run.state,
                "query_feedback":self.feedback_memory.selected(run.request.feedback_ids),
                "query_memory":self.feedback_memory.matching(run.request.message),
                "requery_origin":run.requery_origin,
                "completed_tools":[{"intent":p["intent"],"answer":p["answer"],"meta":p["facts"]["meta"]}
                                   for p in run.parts]}
        if self.compact_context:
            context = self.context_compiler.compile(
                **{k: v for k, v in context.items() if k not in {"session", "completed_tools"}},
                state=run.state, parts=run.parts, terminal=run.terminal,
                remaining_tools=8-run.tool_calls)
        elif self.dynamic_tools:
            context["tool_policy"] = self.tool_policy(run).model_dump()
        if not self.dynamic_tools:
            context.pop("tool_policy", None)
        return context

    @staticmethod
    def tool_policy(run):
        return ToolPolicy.compile(run.request.message, run.state, terminal=run.terminal,
                                  remaining_tools=8-run.tool_calls,
                                  read_only=bool(run.request.requery_of_run_id))

    def _attach_harness(self, run):
        """Return refreshed capabilities after a tool changes the trusted state."""
        compiled = self.context_compiler.compile(message=run.request.message,
            today=self.today().isoformat(), explicit_filters={}, preferences={},
            state=run.state, default_page_size=run.request.page_size,
            requested_pipeline=run.request.pipeline, terminal=run.terminal,
            remaining_tools=8-run.tool_calls, requery_origin=run.requery_origin)
        metadata = {"context_contract": compiled["context_contract"], "session": compiled["session"]}
        if self.dynamic_tools:
            metadata["tool_policy"] = compiled["tool_policy"]
        run.result["facts"]["meta"]["harness"] = metadata

    def execute(self, run: QueryRun, name: str, plan: QuerySpec) -> dict:
        run.limits.check()
        if run.tool_calls >= 8 or run.terminal:
            raise ValueError("QUERY_TOOL_BUDGET_EXCEEDED")
        if name not in TOOL_ACTIONS or plan.action not in TOOL_ACTIONS[name]:
            raise ValueError("INVALID_TOOL_PLAN")
        if (plan.action == "NEXT" and run.tool_calls == 0 and run.requery_origin and
                run.requery_origin["target_action"] in {"LIST", "STATS"}):
            plan = QuerySpec.model_validate({**run.state["last_plan"], "final": plan.final})
            name = "get_question_stats" if plan.action == "STATS" else "list_questions"
        if plan.action == "NEXT" and not (run.state.get("list_request") or {}).get("cursor"):
            plan = QuerySpec(action="CLARIFY", clarification="当前没有下一页，请先查询一个题目列表。")
        # Exposure is an optimization; authorization always remains a host check,
        # including when the decision provider bypasses Pi's visible tool schema.
        self.tool_policy(run).require(name, plan.action)
        if plan.action == "RECORD_REVIEW" and any(
            item.status not in authorized_statuses(run.request.message) for item in plan.review_items
        ):
            raise ValueError("QUERY_WRITE_STATUS_CONFLICT")
        run.tool_calls += 1
        if (plan.filters.response_form or plan.filters.coding_focus) and not plan.filters.annotation_status:
            plan=plan.model_copy(update={"filters":plan.filters.model_copy(update={"annotation_status":self.task_annotation_policy})})
        if not self.task_filters_enabled and (plan.filters.coding_focus or plan.filters.response_form):
            raise ValueError("TASK_FILTERS_DISABLED")
        if plan.action == "NEXT":
            saved = run.state.get("list_request")
            if not saved or not saved.get("cursor"):
                plan = QuerySpec(action="CLARIFY", clarification="当前没有下一页，请先查询一个题目列表。")
            else:
                saved_request = (StatsListRequest if saved.get("group_by") else ListRequest).model_validate(saved)
                saved_filters = filter_fields(saved_request)
                plan = plan.model_copy(update={"filters": saved_filters, "sort": saved_request.sort,
                    "top_n": saved_request.top_n, "page_size": saved_request.page_size,
                    "review_statuses":saved_request.review_statuses,"review_order":saved_request.review_order})
        explicit = self._explicit_filters(run.request)
        if any(plan.filters.model_dump(mode="json").get(k) != v for k, v in explicit.items()) and plan.action != "CLARIFY":
            raise ValueError("EXPLICIT_FILTER_CONFLICT")
        invocation_id,previous=self.journal.prepare(run,run.tool_calls,name,plan)
        if previous:
            run.result=previous
            run.terminal=plan.final
            return previous
        started = time.perf_counter()
        warnings = []
        if plan.action == "SEARCH":
            plan = plan.model_copy(update={"pipeline":self.model_context(run)["requested_pipeline"]})
        token = current_limits.set(run.limits)
        try:
            with self.database.session() as session:
                if session.bind.dialect.name == "postgresql":
                    session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                if self._revisions(session) != run.revisions:
                    raise ValueError("SNAPSHOT_CHANGED")
                if plan.action in {"LIST", "NEXT","STATS"}:
                    req = (run.request.list_request if isinstance(run.request, StructuredQueryRequest) else
                           (StatsListRequest if run.state["list_request"].get("group_by") else ListRequest).model_validate(run.state["list_request"]) if plan.action == "NEXT" else
                           StatsListRequest(**plan.filters.model_dump(),group_by=plan.group_by,sort=plan.sort,
                               top_n=plan.top_n,page_size=plan.page_size,review_statuses=plan.review_statuses,review_order=plan.review_order) if plan.action=="STATS" else
                           ListRequest(**plan.filters.model_dump(), sort=plan.sort, top_n=plan.top_n, page_size=plan.page_size,
                                       review_statuses=plan.review_statuses,review_order=plan.review_order))
                    if isinstance(run.request,StructuredQueryRequest):
                        req=req.model_copy(update={"annotation_status":plan.filters.annotation_status})
                    result = list_questions(session, req, signing_key=self.signing_key, user_id=self.user_id, as_of=self.today())
                    warnings = result.get("warnings", [])
                    list_state = req.model_dump(mode="json")
                    list_state["cursor"] = result["meta"]["pagination"]["next_cursor"]
                    next_filters = filter_fields(req)
                    plan = plan.model_copy(update={"filters": next_filters, "sort": req.sort,
                                                  "top_n": req.top_n, "page_size": req.page_size,
                                                  "group_by":getattr(req,"group_by","question")})
                elif plan.action == "SEARCH":
                    corpus = session.get(CorpusState, 1)
                    if not self.retriever or not corpus.current_revision or corpus.indexed_revision != corpus.current_revision:
                        raise ValueError("INDEX_NOT_READY")
                    stages = {"HYBRID_RERANK": ["HYBRID_RERANK", "HYBRID", "BM25"],
                              "HYBRID": ["HYBRID", "BM25"], "DENSE": ["DENSE", "BM25"], "BM25": ["BM25"]}[plan.pipeline]
                    for index, executed in enumerate(stages):
                        run.limits.check()
                        try:
                            result = search_questions(session, self.retriever, plan.search_query, plan.filters,
                                top_k=plan.page_size, pipeline=executed, relevance_query=plan.relevance_query or
                                ((run.state.get("pending_clarification") or {}).get("original_message", "") + " " + run.request.message).strip(),
                                lexical_facets=plan.lexical_facets,
                                on_progress=lambda event:self.journal.event(run.id,"stage",event))
                            break
                        except (httpx.HTTPError, APIError, ValidationError, ValueError) as failure:
                            if isinstance(failure, ValueError) and str(failure) not in {"EMBEDDING_NOT_READY", "RERANKER_NOT_READY"}:
                                raise
                            if index + 1 == len(stages):
                                raise
                            warnings.append(f"{executed} 暂时不可用，已尝试 {stages[index + 1]}。")
                    executed=result["meta"].get("pipeline",executed)
                    if result["meta"].get("rerank_status") in {"SKIPPED_BUDGET","FAILED"}:
                        warnings.append("重排未完成，已保留混合召回结果。")
                    verified = executed == "HYBRID_RERANK" and result["meta"].get("rerank_status") == "COMPLETED"
                    if plan.pipeline == "HYBRID_RERANK" and not verified and result["meta"].get("eligible_count", 0):
                        result["data"] = []
                        warnings = ["相关性核验未完成，请重新检索，或明确选择原始混合检索查看候选。"]
                    result["meta"].update({"relevance_status": "VERIFIED" if verified else
                        "UNAVAILABLE" if plan.pipeline == "HYBRID_RERANK" else "UNVERIFIED",
                        "returned_count": len(result["data"])})
                    result["meta"].update({"route": "SEARCH", "requested_pipeline": plan.pipeline,
                        "executed_pipeline": executed, "degraded": executed != plan.pipeline,
                        "ranking_scope": "semantic_candidates"})
                elif plan.action in {"DETAILS", "REVIEW_STATE", "RECORD_REVIEW"}:
                    ids = plan.question_ids if plan.action != "RECORD_REVIEW" else [i.canonical_question_id for i in plan.review_items]
                    if plan.action=="REVIEW_STATE" and plan.scope=="full_scope":
                        saved=run.state.get("list_request")
                        if not saved or saved.get("group_by") not in {None,"question"}: raise ValueError("QUERY_SCOPE_MISSING")
                        bound=ListRequest.model_validate({k:v for k,v in saved.items() if k in ListRequest.model_fields}).model_copy(update={"cursor":None,"page_size":100})
                        ids=[]
                        while True:
                            page=list_questions(session,bound,signing_key=self.signing_key,user_id=self.user_id,as_of=self.today())
                            if page["meta"]["pagination"]["result_total"]>1000: raise ValueError("QUERY_SCOPE_TOO_LARGE")
                            ids.extend(x["canonical_question_id"] for x in page["data"])
                            if not page["meta"]["pagination"]["next_cursor"]: break
                            bound=bound.model_copy(update={"cursor":page["meta"]["pagination"]["next_cursor"]})
                    if plan.scope=="current_page" and any(i not in run.state.get("current_page_ids", []) for i in ids):
                        raise ValueError("QUESTION_NOT_IN_SESSION")
                    if plan.action == "DETAILS":
                        result = {"data": [get_question_detail(session, i, plan.filters) for i in ids], "meta": {"route": "SQL"}}
                    elif plan.action == "REVIEW_STATE":
                        result = {"data": [], "states": self.reviews.get_states(self.user_id, ids), "meta": {"route": "SQL"}}
                    else:
                        batches=[]
                        for offset in range(0,len(plan.review_items),20):
                            run.limits.check()
                            batches.append(self.reviews.record(self.user_id,ReviewRequest(
                                idempotency_key=f"agent:{invocation_id}:{offset//20}",items=plan.review_items[offset:offset+20])))
                        result={"data":[],"review":{"items":[i for b in batches for i in b["items"]],
                            "user_state_revision":batches[-1]["user_state_revision"],"batches":batches},
                            "meta":{"route":"SQL","write_scope":"current_page","action_id":invocation_id}}
                else:
                    result = {"data": [], "meta": {"route": "CLARIFY", "clarification": {
                        "question": plan.clarification, "options": plan.clarification_options}}}
            with self.database.session() as session:
                latest = self._revisions(session)
                if latest[:2] != run.revisions[:2] or (plan.action != "RECORD_REVIEW" and latest[2] != run.revisions[2]):
                    raise ValueError("SNAPSHOT_CHANGED")
            run.limits.check()
        finally:
            current_limits.reset(token)
        result.setdefault("meta", {}).update({"corpus_revision": run.revisions[0],
            "request_id": run.request.request_id,
            "task_annotation_revision": run.revisions[1], "user_state_revision": latest[2],
            "applied_filters": plan.filters.model_dump(mode="json")})
        if run.request.requery_of_run_id:
            result["meta"]["requery_of_run_id"] = run.request.requery_of_run_id
        result["meta"].setdefault("timings",{}).update({"tool_ms":int((time.perf_counter()-started)*1000)})
        run.plan = plan
        rows = result.get("data", [])
        answer = (plan.clarification if plan.action == "CLARIFY" else
                  f"已保存 {len(plan.review_items)} 道题的复习记录。" if plan.action == "RECORD_REVIEW" else
                  "已读取当前题目的复习状态。" if plan.action == "REVIEW_STATE" else
                  f"返回 {len(rows)} 道题，按真实提问频次排序。" if plan.action in {"LIST", "NEXT"} and plan.sort == "frequency" else
                  ("相关性核验未完成，请重试。" if result["meta"].get("relevance_status") == "UNAVAILABLE" else
                   f"找到 {len(rows)} 道经语义筛选的题目，按相关度排列。" if plan.action == "SEARCH" and result["meta"].get("relevance_status") == "VERIFIED" else
                   f"返回 {len(rows)} 条结果。"))
        run.result = {"intent": plan.action, "answer": answer, "facts": result,
                      "planning": {"spec": plan.model_dump(mode="json"), "version": self.prompt_version},
                      "tool_trace": [{"name": name, "parameters": plan.model_dump(mode="json"),
                                      "elapsed_ms": result["meta"]["timings"]["tool_ms"]}],
                      "warnings": warnings}
        run.parts.append(run.result)
        run.terminal=plan.final
        if plan.action == "CLARIFY":
            pending = run.state.get("pending_clarification") or {}
            same_choices = bool(plan.clarification_options) and plan.clarification_options == pending.get("options")
            run.state["pending_clarification"] = {"original_message":
                pending.get("original_message", run.request.message) if same_choices else run.request.message,
                "question": plan.clarification, "options": plan.clarification_options}
            self._remember(run)
            self._attach_harness(run)
            self.journal.complete(run,invocation_id)
            return run.result
        run.state.pop("pending_clarification", None)
        # Reading details or review state must retain the page and scope for later
        # references such as "第3题" and "下一页".
        if plan.action in {"DETAILS", "REVIEW_STATE", "RECORD_REVIEW"}:
            self._remember(run)
            self._attach_harness(run)
            self.journal.complete(run,invocation_id)
            return run.result
        run.state = {**run.state, "filters": result["meta"]["applied_filters"], "sort": plan.sort,
                     "last_plan": plan.model_dump(mode="json"), "corpus_revision": run.revisions[0],
                     "task_annotation_revision": run.revisions[1],
                     "current_page_ids": [r["canonical_question_id"] for r in rows if r.get("canonical_question_id")],
                     "recent_messages": run.state.get("recent_messages", [])}
        self._remember(run)
        if plan.action in {"LIST", "NEXT","STATS"}:
            run.state["list_request"] = list_state
            run.state["result_set_id"]=result["meta"].get("result_set_id")
            if plan.action in {"LIST", "STATS"}:
                run.state["list_explicit_filters"] = self._explicit_filters(run.request)
            elif "list_explicit_filters" in run.state or self._explicit_filters(run.request):
                run.state["list_explicit_filters"] = {**run.state.get("list_explicit_filters", {}),
                                                       **self._explicit_filters(run.request)}
        elif plan.action != "CLARIFY":
            run.state.pop("list_request", None)
            run.state.pop("list_explicit_filters", None)
        self._attach_harness(run)
        self.journal.complete(run,invocation_id)
        return run.result

    @staticmethod
    def _remember(run):
        messages=run.state.get("recent_messages", [])
        if not messages or messages[-1]!=run.request.message:
            run.state["recent_messages"]=[*messages[-7:],run.request.message]

    def finish(self, run: QueryRun, *, provider: str, timings: dict | None = None):
        if not run.result:
            raise ValueError("QUERY_PLAN_MISSING")
        response = run.result
        if len(run.parts)>1:
            response["facts"]["components"]=[{"intent":p["intent"],"facts":p["facts"],"answer":p["answer"]}
                for p in run.parts[:-1]]
            response["tool_trace"]=[i for p in run.parts for i in p["tool_trace"]]
        response["planning"]["provider"] = provider
        meta = response["facts"]["meta"]
        meta["timings"].update(timings or {})
        with self.database.session() as session, session.begin():
            conversation = session.get(AgentConversation, run.conversation_id, with_for_update=True)
            if conversation.version != run.version:
                raise ValueError("CONVERSATION_VERSION_CONFLICT")
            conversation.version += 1
            if run.recovered_write_state is not None:
                run.state=run.recovered_write_state
                self._remember(run)
            conversation.state = run.state
            response["conversation_id"] = run.conversation_id
            response["run_id"] = run.id
            response["conversation_version"] = conversation.version
            turn = session.scalar(select(AgentTurn).where(AgentTurn.user_id == self.user_id,
                                                           AgentTurn.request_id == run.request.request_id))
            turn.status, turn.response = "SUCCEEDED", response
            turn.state_after,turn.finished_at=run.state,now_utc()
            self.journal.append(session,turn,"completed",{"result":response})
        self.runs.pop(run.id, None)
        return response

    def fail(self, run, error=None):
        run.limits.cancelled.set()
        with self.database.session() as session, session.begin():
            turn = session.scalar(select(AgentTurn).where(AgentTurn.user_id == self.user_id,
                                                           AgentTurn.request_id == run.request.request_id))
            if turn and turn.status == "RUNNING":
                interrupted=type(error).__name__=="CancelledError" and not turn.cancel_requested
                turn.status = "INTERRUPTED" if interrupted else "CANCELLED" if str(error)=="QUERY_CANCELLED" else "FAILED"
                turn.error_code="QUERY_INTERRUPTED" if interrupted else str(error or "QUERY_FAILED")[:128]
                turn.finished_at=now_utc()
                self.journal.append(session,turn,"interrupted" if interrupted else "failed",{"code":turn.error_code,"partial":bool(turn.response)})
        self.runs.pop(run.id, None)
