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
from interview_intelligence.analytics.counts import count_questions
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


SEARCH_FILTER_POLICY_VERSION = "search_question_type_origin_v1"
SEARCH_DIMENSION_POLICY_VERSION = "search_semantic_dimensions_origin_v1"
SEARCH_SEMANTIC_FIELDS = ("topic_l1", "topic_l2", "coding_focus")


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
        filters = request.filters.model_dump(mode="json", exclude_unset=True,
                                             exclude_none=request.requery_of_run_id is None)
        # Clearing the legacy type is meaningful when a previous explicit scope
        # exists. Other ordinary-request null semantics remain unchanged.
        for key in ("question_type", *SEARCH_SEMANTIC_FIELDS):
            if key in request.filters.model_fields_set:
                filters[key] = request.filters.model_dump(mode="json")[key]
        return filters

    @staticmethod
    def _search_filter_scope(state):
        saved = state.get("search_filter_scope") or {}
        scopes = {key: value for key, value in saved.get("fields", {}).items()
                  if saved.get("version") == SEARCH_DIMENSION_POLICY_VERSION
                  and key in SEARCH_SEMANTIC_FIELDS and isinstance(value, dict)
                  and value.get("source") in {"explicit_ui", "sql_list"} and value.get("value") is not None}
        for key in SEARCH_SEMANTIC_FIELDS:
            value = (state.get("list_request") or {}).get(key)
            if key not in scopes and value is not None:
                scopes[key] = {"value": value, "source": "sql_list"}
        return scopes

    def _search_semantic_dimensions(self, run, plan, explicit, automatic_annotation):
        scopes = self._search_filter_scope(run.state)
        changes, fields = {}, {}
        for key in SEARCH_SEMANTIC_FIELDS:
            requested = getattr(plan.filters, key)
            scope = scopes.get(key, {})
            if key in explicit:
                source, decision, applied = "explicit_ui", "explicit_clear" if explicit[key] is None else "explicit", requested
            elif requested is not None and requested == scope.get("value"):
                source, decision, applied = "inherited_" + scope["source"], "trusted_inherited", requested
            elif key == "coding_focus" and requested is not None and plan.filters.response_form in {"CODE", "SQL"}:
                source, decision, applied = "programming_response_form", "programming_task_preserved", requested
            else:
                source = "model_intent" if requested is not None else "unspecified"
                decision, applied = ("model_inferred_softened" if requested is not None else "unspecified"), None
            changes[key] = applied
            fields[key] = {"planned": requested, "applied": applied, "source": source, "decision": decision}
        cleared_annotation = bool(automatic_annotation and not plan.filters.response_form and not changes["coding_focus"])
        if cleared_annotation:
            changes["annotation_status"] = None
        return plan.model_copy(update={"filters": plan.filters.model_copy(update=changes)}), {
            "version": SEARCH_DIMENSION_POLICY_VERSION, "fields": fields,
            "automatic_annotation_cleared": cleared_annotation}

    @staticmethod
    def _question_type_scope(state):
        scope = state.get("question_type_scope") or {}
        if scope.get("version") == SEARCH_FILTER_POLICY_VERSION and scope.get("source") in {"explicit_ui", "sql_list"}:
            return scope
        # A saved SQL list is an exact category scope, including old receipts.
        # A bare last_plan/filter from semantic search has no such provenance.
        value = (state.get("list_request") or {}).get("question_type")
        if value is not None:
            return {"version": SEARCH_FILTER_POLICY_VERSION, "value": value, "source": "sql_list"}
        return {}

    def _search_question_type(self, run, plan, explicit):
        """Legacy extraction labels are soft intent unless a host scope binds them."""
        requested = plan.filters.model_dump(mode="json")["question_type"]
        scope = self._question_type_scope(run.state)
        if "question_type" in explicit:
            source, hard_scope = "explicit_ui", explicit["question_type"] is not None
            decision = "explicit" if hard_scope else "explicit_clear"
        elif requested is not None and requested == scope.get("value"):
            source, hard_scope = "inherited_" + scope["source"], True
            decision = "trusted_inherited"
        else:
            source, hard_scope = "model_intent" if requested is not None else "unspecified", False
            decision = "model_inferred_softened" if requested is not None else "unspecified"
        if requested is not None and not hard_scope:
            plan = plan.model_copy(update={"filters": plan.filters.model_copy(update={"question_type": None})})
        return plan, {"version": SEARCH_FILTER_POLICY_VERSION, "planned_question_type": requested,
                      "applied_question_type": plan.filters.model_dump(mode="json")["question_type"],
                      "source": source, "decision": decision,
                      "mode": "hard_filter" if hard_scope else "semantic_intent"}

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
        allowed = {"LIST", "STATS", "SEARCH", "NEXT", "CLARIFY", "COUNT"}
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
        source_type_scope = self._question_type_scope(after)
        if plan.action == "SEARCH" and source_type_scope:
            original_filters.setdefault("question_type", source_type_scope["value"])
        source_search_scope = self._search_filter_scope(after)
        if plan.action == "SEARCH":
            for key, scope in source_search_scope.items():
                original_filters.setdefault(key, scope["value"])
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
        if source_type_scope:
            state["question_type_scope"] = deepcopy(source_type_scope)
        if source_search_scope:
            state["search_filter_scope"] = {"version": SEARCH_DIMENSION_POLICY_VERSION,
                                            "fields": deepcopy(source_search_scope)}
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
            legacy_digests = set(compatible_digests)
            explicit_type_clear = "question_type" in request.filters.model_fields_set and request.filters.question_type is None
            if explicit_type_clear:
                digest = hashlib.sha256((digest + ":explicit_question_type_clear").encode()).hexdigest()
                compatible_digests = {digest}
            legacy_dimension_digests = set(compatible_digests)
            dimension_clears = [key for key in SEARCH_SEMANTIC_FIELDS
                                if key in request.filters.model_fields_set and getattr(request.filters, key) is None]
            if dimension_clears:
                digest = hashlib.sha256((digest + ":explicit_search_dimension_clears:" + ",".join(dimension_clears)).encode()).hexdigest()
                compatible_digests = {digest}
        with self.database.session() as session, session.begin():
            receipt = session.scalar(select(AgentTurn).where(
                AgentTurn.user_id == self.user_id, AgentTurn.request_id == request.request_id).with_for_update())
            if receipt:
                legacy_clear = (not request.requery_of_run_id and explicit_type_clear and
                    (receipt.request_payload or {}).get("query_filter_policy_version") != SEARCH_FILTER_POLICY_VERSION and
                    receipt.payload_hash in legacy_digests)
                legacy_dimensions = (not request.requery_of_run_id and bool(dimension_clears) and
                    (receipt.request_payload or {}).get("search_dimension_policy_version") != SEARCH_DIMENSION_POLICY_VERSION
                    and receipt.payload_hash in legacy_dimension_digests)
                if receipt.payload_hash not in compatible_digests and not legacy_clear and not legacy_dimensions:
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
            receipt.request_payload = {**receipt.request_payload,
                "query_filter_policy_version": SEARCH_FILTER_POLICY_VERSION,
                "search_dimension_policy_version": SEARCH_DIMENSION_POLICY_VERSION,
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
        session_state = {**run.state}
        focus = self.context_compiler.conversation_focus(run.state)
        if focus:
            session_state["conversation_focus"] = focus
        context = {"message": run.request.message, "today": self.today().isoformat(),
                "explicit_filters": self._explicit_filters(run.request),
                "default_page_size":run.request.page_size if "page_size" in run.request.model_fields_set else self.preferences.defaults().get("page_size",run.request.page_size),
                "requested_pipeline": run.request.pipeline if "pipeline" in run.request.model_fields_set else self.preferences.defaults().get("pipeline",run.request.pipeline),
                "preferences":self.preferences.defaults(),"session": session_state,
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
        metadata["evidence"] = [self.context_compiler.evidence_row(row) for row in run.result["facts"].get("data", [])[:5]]
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
        if plan.action == "ANSWER":
            if any(identity not in run.state.get("current_page_ids", []) for identity in plan.question_ids):
                raise ValueError("QUESTION_NOT_IN_SESSION")
            has_aggregate_evidence = any(part["intent"] in {"COUNT", "STATS"} or
                part["intent"] in {"LIST", "NEXT"} and
                type(part["facts"].get("meta", {}).get("pagination", {}).get("result_total")) is int
                for part in run.parts)
            if plan.answer_basis in {"CORPUS", "MIXED"} and not plan.question_ids and not has_aggregate_evidence:
                raise ValueError("QUERY_ANSWER_EVIDENCE_REQUIRED")
            if plan.answer_basis == "GENERAL_KNOWLEDGE" and plan.question_ids:
                raise ValueError("QUERY_ANSWER_BASIS_CONFLICT")
        if plan.action == "RECORD_REVIEW" and any(
            item.status not in authorized_statuses(run.request.message) for item in plan.review_items
        ):
            raise ValueError("QUERY_WRITE_STATUS_CONFLICT")
        run.tool_calls += 1
        automatic_annotation = bool((plan.filters.response_form or plan.filters.coding_focus) and not plan.filters.annotation_status)
        if automatic_annotation:
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
        question_type_policy = None
        search_dimension_policy = None
        if plan.action == "SEARCH":
            plan, question_type_policy = self._search_question_type(run, plan, explicit)
            plan, search_dimension_policy = self._search_semantic_dimensions(run, plan, explicit, automatic_annotation)
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
                           StatsListRequest(**plan.filters.model_dump(),group_by=plan.group_by,topic_level=plan.topic_level,sort=plan.sort,
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
                                                  "group_by":getattr(req,"group_by","question"),
                                                  "topic_level":getattr(req,"topic_level","L1")})
                elif plan.action == "COUNT":
                    counts = count_questions(session, plan.filters, user_id=self.user_id, review_statuses=plan.review_statuses)
                    scoped = any(value is not None for key, value in plan.filters.model_dump(mode="json").items()
                                 if key != "date_basis") or bool(plan.review_statuses)
                    result = {"data": [], "meta": {"route": "SQL", "counts": counts,
                        "count_scope": "filtered_corpus" if scoped else "full_corpus"}}
                elif plan.action == "ANSWER":
                    rows = [get_question_detail(session, identity, plan.filters) for identity in plan.question_ids]
                    if any(not row.get("occurrence_count") for row in rows):
                        raise ValueError("QUERY_ANSWER_EVIDENCE_OUT_OF_SCOPE")
                    result = {"data": rows, "meta": {"route": "ANSWER", "answer_kind": plan.answer_kind,
                        "answer_basis": plan.answer_basis, "evidence_question_ids": list(plan.question_ids),
                        "evidence_notice": ("根据通用知识生成的参考回答。" if plan.answer_basis == "GENERAL_KNOWLEDGE" else
                            "题库来源仅证明题目与提问记录；解答为模型生成的参考内容。")}}
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
                                preferred_question_type=(plan.preferred_question_type or (question_type_policy["planned_question_type"]
                                    if question_type_policy and question_type_policy["decision"] == "model_inferred_softened" else None)),
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
                    partial = (result["meta"].get("rerank_status") == "COMPLETED_PARTIAL"
                               and result["meta"].get("candidate_verification_status") == "PARTIAL"
                               and type(result["meta"].get("invalid_candidate_count")) is int
                               and result["meta"]["invalid_candidate_count"] > 0)
                    verified = executed == "HYBRID_RERANK" and (
                        result["meta"].get("rerank_status") == "COMPLETED"
                        and result["meta"].get("candidate_verification_status") in {None, "COMPLETE"}
                        and result["meta"].get("invalid_candidate_count", 0) == 0
                        or partial and bool(result["data"]))
                    if plan.pipeline == "HYBRID_RERANK" and not verified and result["meta"].get("eligible_count", 0):
                        result["data"] = []
                        warnings = ["相关性核验未完成，请重新检索，或明确选择原始混合检索查看候选。"]
                    if partial:
                        warnings.append(f"已排除 {result['meta']['invalid_candidate_count']} 个结构异常候选，结果可能不完整。")
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
        if question_type_policy:
            result["meta"]["question_type_filter_policy"] = question_type_policy
        if search_dimension_policy:
            result["meta"]["search_filter_policy"] = search_dimension_policy
        if run.request.requery_of_run_id:
            result["meta"]["requery_of_run_id"] = run.request.requery_of_run_id
        result["meta"].setdefault("timings",{}).update({"tool_ms":int((time.perf_counter()-started)*1000)})
        run.plan = plan
        rows = result.get("data", [])
        count_labels = [str(value) for value in (plan.filters.company, plan.filters.topic_l1,
                                                 plan.filters.topic_l2) if value]
        count_scope = (f"当前筛选范围（{' / '.join(count_labels)}）" if count_labels else "当前筛选范围")
        answer = (plan.answer_text if plan.action == "ANSWER" else
                  (f"{count_scope if result['meta']['count_scope'] == 'filtered_corpus' else '当前全库'}共有 "
                   f"{result['meta']['counts']['canonical_questions']:,} 道去重题目，"
                   f"{result['meta']['counts']['occurrences']:,} 条提问记录，来自 "
                   f"{result['meta']['counts']['interviews']:,} 场面试、"
                   f"{result['meta']['counts']['source_documents']:,} 篇面经。") if plan.action == "COUNT" else
                  plan.clarification if plan.action == "CLARIFY" else
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
        if plan.action == "COUNT":
            run.state["last_count"] = {"filters": plan.filters.model_dump(mode="json"),
                "review_statuses": list(plan.review_statuses), "counts": result["meta"]["counts"],
                "count_scope": result["meta"]["count_scope"], "message": run.request.message}
        # Reading details or review state must retain the page and scope for later
        # references such as "第3题" and "下一页".
        if plan.action in {"DETAILS", "REVIEW_STATE", "RECORD_REVIEW", "ANSWER", "COUNT"}:
            self._remember(run)
            self._attach_harness(run)
            self.journal.complete(run,invocation_id)
            return run.result
        inherited_search_scope = self._search_filter_scope(run.state)
        run.state = {**run.state, "filters": result["meta"]["applied_filters"], "sort": plan.sort,
                     "last_plan": plan.model_dump(mode="json"), "corpus_revision": run.revisions[0],
                     "task_annotation_revision": run.revisions[1],
                     "current_page_ids": [r["canonical_question_id"] for r in rows if r.get("canonical_question_id")],
                     "recent_messages": run.state.get("recent_messages", [])}
        run.state["current_page_summary"] = [self.context_compiler.evidence_row(row) for row in rows[:10]]
        if plan.filters.question_type is None:
            run.state.pop("question_type_scope", None)
        elif plan.action in {"LIST", "STATS", "NEXT", "SEARCH"}:
            inherited = self._question_type_scope(run.state)
            source = ("explicit_ui" if "question_type" in explicit else
                      inherited.get("source", "sql_list") if plan.action == "SEARCH" else "sql_list")
            run.state["question_type_scope"] = {"version": SEARCH_FILTER_POLICY_VERSION,
                "value": plan.filters.model_dump(mode="json")["question_type"], "source": source}
        search_scope = {}
        for key in SEARCH_SEMANTIC_FIELDS:
            value = getattr(plan.filters, key)
            inherited = inherited_search_scope.get(key, {})
            if value is None:
                continue
            if key in explicit or plan.action in {"LIST", "STATS", "NEXT"}:
                search_scope[key] = {"value": value, "source": "explicit_ui" if key in explicit else "sql_list"}
            elif value == inherited.get("value"):
                search_scope[key] = inherited
        if search_scope:
            run.state["search_filter_scope"] = {"version": SEARCH_DIMENSION_POLICY_VERSION, "fields": search_scope}
        else:
            run.state.pop("search_filter_scope", None)
        self._remember(run)
        if plan.action in {"LIST", "NEXT","STATS"}:
            run.state["list_request"] = list_state
            run.state["result_set_id"]=result["meta"].get("result_set_id")
            if plan.action in {"LIST", "STATS"}:
                run.state["list_goal"] = {"message": run.request.message, "run_id": run.id,
                                          "intent": plan.action}
                run.state["list_explicit_filters"] = self._explicit_filters(run.request)
            elif "list_explicit_filters" in run.state or self._explicit_filters(run.request):
                run.state["list_explicit_filters"] = {**run.state.get("list_explicit_filters", {}),
                                                       **self._explicit_filters(run.request)}
        elif plan.action != "CLARIFY":
            run.state.pop("list_request", None)
            run.state.pop("list_goal", None)
            run.state.pop("list_explicit_filters", None)
        self._attach_harness(run)
        self.journal.complete(run,invocation_id)
        return run.result

    @staticmethod
    def _remember(run):
        messages=run.state.get("recent_messages", [])
        if not messages or messages[-1]!=run.request.message:
            run.state["recent_messages"]=[*messages[-7:],run.request.message]
        plan = run.plan
        if not run.terminal or not plan or plan.action not in {"LIST", "NEXT", "STATS", "SEARCH", "COUNT", "ANSWER"}:
            return
        previous = ContextCompiler.conversation_focus(run.state)
        intent = plan.action
        message = run.request.message
        if intent == "NEXT":
            intent = "STATS" if (run.state.get("list_request") or {}).get("group_by") else "LIST"
            message = ((run.state.get("list_goal") or {}).get("message") or
                       (previous.get("message") if previous.get("intent") in {"LIST", "STATS"} else None) or message)
        run.state["conversation_focus"] = {
            "intent": intent, "source_action": plan.action, "message": message,
            "filters": plan.filters.model_dump(mode="json"), "sort": plan.sort, "top_n": plan.top_n,
            "review_statuses": list(plan.review_statuses), "review_order": plan.review_order,
            "group_by": plan.group_by, "topic_level": plan.topic_level, "run_id": run.id,
            **({"answer_kind": plan.answer_kind, "answer_basis": plan.answer_basis,
                "question_ids": list(plan.question_ids)} if plan.action == "ANSWER" else {})}

    def finish(self, run: QueryRun, *, provider: str, timings: dict | None = None):
        if not run.result:
            raise ValueError("QUERY_PLAN_MISSING")
        response = run.result
        if len(run.parts)>1:
            response["facts"]["components"]=[{"intent":p["intent"],"facts":p["facts"],"answer":p["answer"]}
                for p in run.parts[:-1]]
            response["tool_trace"]=[i for p in run.parts for i in p["tool_trace"]]
        response["planning"]["provider"] = provider
        run.state["last_response"] = {"message": run.request.message, "intent": response["intent"],
                                      "answer": response["answer"]}
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
