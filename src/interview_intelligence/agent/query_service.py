"""Shared QueryAgent host: trusted tools, SQL facts and durable session state."""
from __future__ import annotations

import hashlib
import json
import re
import time
from copy import deepcopy
from datetime import date, timedelta, timezone
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import uuid4

import httpx
from openai import APIError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec, StructuredQueryRequest, TOOL_ACTIONS, QUERY_AGENT_VERSION
from interview_intelligence.agent.harness import ContextCompiler, ToolPolicy, authorized_statuses, authorizes_result_page_write
from interview_intelligence.agent.evidence_projection import EVIDENCE_RESULT_ROWS
from interview_intelligence.agent.goal_contract import (
    bind_goal, completion_for, restore_completion, tool_evidence, referenceable_evidence, evidence_by_ordinal,
)
from interview_intelligence.analytics.detail import get_question_detail
from interview_intelligence.analytics.counts import count_questions
from interview_intelligence.analytics.semantic_scope import load_semantic_scope, SemanticScopeIncomplete
from interview_intelligence.analytics.listing import ListRequest, StatsListRequest, filter_fields, list_questions
from interview_intelligence.agent.preferences import PreferenceService
from interview_intelligence.agent.feedback_memory import FeedbackMemory
from pathlib import Path
from interview_intelligence.agent.journal import QueryJournal
from interview_intelligence.agent.query_response import build_query_response
from interview_intelligence.agent.collection_selection import CollectionSelection
from interview_intelligence.agent.search_pagination import (
    candidate_page, is_candidate_listing, store_candidates,
)
from interview_intelligence.agent import complete_workset as workset_pages
from interview_intelligence.relevance_scope import (
    CONTROL_MESSAGES, compile_relevance_scope, semantic_messages, validate_relevance_scope,
)
from interview_intelligence.analytics.stats import _active_from, _conditions, query_question_stats
from interview_intelligence.analytics.usage import record_usage
from interview_intelligence.company import COMPANY_ALIASES
from interview_intelligence.contracts import FilterSpec, ReviewRequest, StatsRequest
from interview_intelligence.domain.models import AgentConversation, AgentTurn, CorpusState, UserRevision, ModelCall, Interview, now_utc
from interview_intelligence.providers.runtime import (
    RequestLimits, current_limits, current_call_reserve,
    DEFAULT_QUERY_DEADLINE_SECONDS, DEFAULT_QUERY_MAX_MODEL_CALLS,
    DEFAULT_QUERY_MAX_TOTAL_MODEL_CALLS, DEFAULT_QUERY_MAX_TOKENS,
)
from interview_intelligence.review.service import ReviewService
from interview_intelligence.search.service import search_questions
from interview_intelligence.search.completeness import search_completeness, partial_verification_warning
from interview_intelligence.agent.query_scope import (
    VERSION as USER_SCOPE_POLICY_VERSION, RECALL_VERSION, collection_page_size, collection_sort, collection_top_n, continues_scope, engineering_collection_request,
    explicit_taxonomy_value, guard_plan, inherited_ui_filters, user_messages, relevance_intent, recall_intent,
    engineering_all_source_messages,
)


SEARCH_FILTER_POLICY_VERSION = "search_question_type_origin_v1"
SEARCH_DIMENSION_POLICY_VERSION = "search_semantic_dimensions_origin_v1"
SEARCH_SEMANTIC_FIELDS = ("topic_l1", "topic_l2", "coding_focus")
SEARCH_FREQUENCY_CUES = re.compile(r"高频|频率|频次|出现次数|提问次数|考察次数|被问次数|出现最多|最常考", re.I)
SEARCH_RELEVANCE_CUES = re.compile(r"(?:按|依|根据|按照).{0,5}(?:相关度|相关性|匹配度).{0,5}(?:排序|排名|排列)|(?:相关度|相关性|匹配度).{0,4}(?:排序|排名|排列)", re.I)
SEARCH_CONTINUATION_CUES = re.compile(r"^(再|继续|更多|还有|下一批|同上|这些|这类|上面|刚才|前面|重查|重新|那)")
SEARCH_RANKING_NEGATION = re.compile(r"(?:不要|不按|不是|不再|不用|无需|不必|别|避免|禁止).{0,10}$")


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
    collection_event: dict = field(default_factory=dict)
    user_goal: dict = field(default_factory=dict)
    completion: dict = field(default_factory=dict)
    write_question_ids: tuple[str, ...] = ()
    write_result_page: bool = False
    write_target_bound: bool = True
    execution_pipeline: str | None = None


class QueryService:
    def __init__(self, database, retriever=None, *, user_id="local", signing_key,
                 deadline_seconds=DEFAULT_QUERY_DEADLINE_SECONDS, max_tokens=DEFAULT_QUERY_MAX_TOKENS,
                 task_filters_enabled=True,task_annotation_policy="VERIFIED",
                 compact_context=True, dynamic_tools=True, prompt_version=QUERY_AGENT_VERSION, today=None,
                 max_planning_calls=DEFAULT_QUERY_MAX_MODEL_CALLS,
                 max_total_model_calls=DEFAULT_QUERY_MAX_TOTAL_MODEL_CALLS,
                 feedback_root=None, collection_selection_threshold=100):
        self.database, self.retriever = database, retriever
        self.user_id, self.signing_key = user_id, signing_key
        self.deadline_seconds = deadline_seconds
        self.max_tokens,self.task_filters_enabled=max_tokens,task_filters_enabled
        self.task_annotation_policy=task_annotation_policy
        self.compact_context, self.dynamic_tools = compact_context, dynamic_tools
        self.prompt_version = prompt_version
        self.max_planning_calls = max_planning_calls
        self.max_total_model_calls = max_total_model_calls
        self.today = today or date.today
        self.context_compiler = ContextCompiler()
        self.owner_id=str(uuid4())
        self.preferences=PreferenceService(database,user_id)
        self.feedback_memory=FeedbackMemory(database, user_id, Path(feedback_root or "data/feedback"))
        self.journal=QueryJournal(database,user_id)
        self.reviews = ReviewService(database)
        self.runs: dict[str, QueryRun] = {}
        self.collection_selection = CollectionSelection(self, threshold=collection_selection_threshold)

    @staticmethod
    def _explicit_search_ranking(message: str) -> str | None:
        """Use the last explicit ranking cue in a correction, if any."""
        def negated(position: int) -> bool:
            clause = re.split(r"[，,。；;！？!?、\n]", message[max(0, position - 18):position])[-1]
            return bool(SEARCH_RANKING_NEGATION.search(clause))

        cues = [(match.start(), "relevance" if negated(match.start()) else "frequency")
                for match in SEARCH_FREQUENCY_CUES.finditer(message)]
        cues.extend((match.start(), "relevance") for match in SEARCH_RELEVANCE_CUES.finditer(message)
                    if not negated(match.start()))
        return max(cues)[1] if cues else None

    def _search_ranking(self, run: QueryRun) -> str:
        explicit = self._explicit_search_ranking(run.request.message)
        if explicit:
            return explicit
        if run.requery_origin.get("action") == "SEARCH":
            original = self._explicit_search_ranking(run.requery_origin.get("original_message", ""))
            if original:
                return original
        if (SEARCH_CONTINUATION_CUES.search(run.request.message.strip())
                and (run.state.get("last_plan") or {}).get("action") == "SEARCH"):
            return run.state.get("search_ranking", "relevance")
        return "relevance"

    def _revisions(self, session):
        corpus = session.get(CorpusState, 1)
        user = session.get(UserRevision, self.user_id)
        return (corpus.current_revision, corpus.task_annotation_revision, user.state_revision if user else 0)

    def _filter_catalog(self):
        """Expose bounded active metadata values; the model selects semantic scope."""
        catalog = {"company_aliases": dict(COMPANY_ALIASES)}
        with self.database.session() as session:
            for key, column in (("companies", Interview.company_normalized),
                                ("positions", Interview.position_normalized)):
                values = list(session.scalars(select(column).select_from(_active_from())
                    .where(*_conditions(FilterSpec()), column.is_not(None), column != "")
                    .distinct().order_by(column).limit(101)))
                catalog[key] = values[:100]
                catalog[f"{key}_complete"] = len(values) <= 100
            catalog["full_algorithm_companies"] = []
            for company in catalog["companies"]:
                try:
                    scope = load_semantic_scope(session, FilterSpec(company=company))
                except SemanticScopeIncomplete:
                    continue
                if scope.occurrences:
                    catalog["full_algorithm_companies"].append(company)
        return catalog

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
                  and value.get("source") in {"explicit_ui", "explicit_text", "sql_list"} and value.get("value") is not None}
        for key in SEARCH_SEMANTIC_FIELDS:
            value = (state.get("list_request") or {}).get(key)
            if key not in scopes and value is not None:
                scopes[key] = {"value": value, "source": "sql_list"}
        return scopes

    def _search_semantic_dimensions(self, run, plan, explicit, automatic_annotation):
        scopes = self._search_filter_scope(run.state) if continues_scope(run) else {}
        changes, fields = {}, {}
        for key in SEARCH_SEMANTIC_FIELDS:
            requested = getattr(plan.filters, key)
            scope = scopes.get(key, {})
            if key in explicit:
                source, decision, applied = "explicit_ui", "explicit_clear" if explicit[key] is None else "explicit", requested
            elif requested is not None and requested == scope.get("value"):
                source, decision, applied = "inherited_" + scope["source"], "trusted_inherited", requested
            elif explicit_taxonomy_value(user_messages(run), key, requested):
                source, decision, applied = "explicit_text", "explicit", requested
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
        if scope.get("version") == SEARCH_FILTER_POLICY_VERSION and scope.get("source") in {"explicit_ui", "explicit_text", "sql_list"}:
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
        scope = self._question_type_scope(run.state) if continues_scope(run) else {}
        if "question_type" in explicit:
            source, hard_scope = "explicit_ui", explicit["question_type"] is not None
            decision = "explicit" if hard_scope else "explicit_clear"
        elif requested is not None and requested == scope.get("value"):
            source, hard_scope = "inherited_" + scope["source"], True
            decision = "trusted_inherited"
        elif explicit_taxonomy_value(user_messages(run), "question_type", requested):
            source, hard_scope, decision = "explicit_text", True, "explicit"
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
        failed_workset = source.status in {"FAILED", "INTERRUPTED"} and workset_pages.is_workset(source.state_after or {})
        if source.status != "SUCCEEDED":
            if not failed_workset:
                raise ValueError("REQUERY_SOURCE_NOT_READY")
            # An explicit NEW request may restart a failed signed workset. The
            # historical signature is checked against its own source revision,
            # never rebound to current facts to make an old receipt pass.
            saved = deepcopy(source.state_after)
            snapshot = saved[workset_pages.STATE_KEY]
            historical = SimpleNamespace(state=saved, revisions=tuple(snapshot["revisions"]),
                conversation_id=source.conversation_id)
            workset_pages.validate(historical, signing_key=self.signing_key, user_id=self.user_id)
            root = QuerySpec.model_validate(snapshot["root_plan"])
            from interview_intelligence.search.workset import validate_engineering_authorization
            validate_engineering_authorization(saved.get("engineering_collection_authorization"),
                saved["relevance_scope"], root.filters)
            if (snapshot["filters"] != root.filters.model_dump(mode="json")
                    or snapshot["scope_hash"] != saved["relevance_scope"]["scope_hash"]
                    or snapshot["manifest"]["authorization"] != saved.get("engineering_collection_authorization")):
                raise ValueError("REQUERY_SOURCE_INVALID")
            response = {"intent": "SEARCH", "planning": {"spec": root.model_dump(mode="json")}}
        else:
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
        source_payload = source.request_payload or {}
        normalized = plan.model_dump(mode="json")
        requery_scope = after.get("relevance_scope")
        scope_recovery = None
        # Older successful SEARCH receipts predate the persisted scope object.
        # A user selecting a displayed mode is authorization; a model merely
        # proposing that option or writing it in a plan/answer is not.
        pending_choice = before.get("pending_clarification") or {}
        chosen = source.message.strip()
        original_goal = pending_choice.get("original_message")
        if (not requery_scope and plan.action == "SEARCH" and chosen == "按题意查找候选"
                and chosen in pending_choice.get("options", [])
                and source_payload.get("message", source.message) == source.message
                and isinstance(original_goal, str) and semantic_messages([original_goal])):
            saved_scope = after.get("query_scope_intent") or {}
            messages = saved_scope.get("messages") if saved_scope.get("version") == USER_SCOPE_POLICY_VERSION else None
            if not isinstance(messages, list) or not messages or not all(isinstance(item, str) for item in messages):
                messages = [original_goal]
            requery_scope = compile_relevance_scope(messages, candidate_mode=True)
            if requery_scope:
                scope_recovery = {"kind": "legacy_user_option_receipt", "source_run_id": source.id,
                    "selected_option": chosen, "authorization": "original_user_selection"}
        candidate_requery = bool(requery_scope and (
            plan.action == "SEARCH" or plan.action == "NEXT" and is_candidate_listing(after)))
        # A NEXT receipt describes the displayed page. Refresh its query at page
        # one instead of carrying an old, revision-bound cursor into a new run.
        listing = deepcopy(after.get("list_request") or {}) if plan.action in {"LIST", "STATS", "NEXT"} else {}
        if plan.action == "NEXT":
            normalized["action"] = "STATS" if listing.get("group_by") else "LIST"
        if candidate_requery:
            root_plan = (after.get(workset_pages.STATE_KEY) or after.get("semantic_candidate_pages") or {}).get("root_plan") or normalized
            normalized = {**deepcopy(root_plan), "action": "SEARCH", "semantic_task": None,
                "search_query": recall_intent(requery_scope["source_messages"])}
            listing = {}
        # Original UI scope remains a host constraint. A model-derived label in
        # planning.spec is context, and must not become an explicit UI filter.
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
        if plan.action == "SEARCH" and after.get("search_ranking") in {"frequency", "relevance"}:
            state["search_ranking"] = after["search_ranking"]
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
        if after.get("collection_selection"):
            state["collection_selection"] = deepcopy(after["collection_selection"])
            state["query_scope_intent"] = deepcopy(after.get("query_scope_intent") or {})
        if candidate_requery:
            state["relevance_scope"] = validate_relevance_scope(requery_scope)
            state["query_scope_intent"] = deepcopy(after.get("query_scope_intent") or {})
            if after.get("engineering_collection_authorization"):
                state["engineering_collection_authorization"] = deepcopy(after["engineering_collection_authorization"])
            if after.get("complete_workset_mode"):
                state["complete_workset_mode"] = after["complete_workset_mode"]
            if scope_recovery:
                state["relevance_scope_recovery"] = scope_recovery
        origin = {"run_id": source.id, "action": plan.action,
                  "original_message": source.message,
                  "user_scope_messages": (requery_scope["source_messages"] if candidate_requery else
                      (after.get("query_scope_intent") or {}).get("messages", [])),
                  "target_action": normalized["action"], "restart_from_first_page": True,
                  "instruction": "重新检索original_message所表达的用户目标，采用本轮显式筛选和纠正。"
                      "保留用户对象及范围；last_plan是旧模型计划，其推导分类不能当成用户指定条件叠加。"
                      "显式UI条件以explicit_filters为准。原消息若是‘下一页’，恢复原列表并重查第一页。"
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
            payload = {"request": request.model_dump(mode="json", exclude={"usage_purpose"} if request.usage_purpose == "practice" else set()),
                       "provided_fields": sorted(request.model_fields_set),
                       "provided_filter_fields": sorted(request.filters.model_fields_set)}
            digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            compatible_digests = {digest}
        else:
            # Existing v9 receipts predate the optional requery field. Preserve
            # their digest, and accept receipts from the transitional schema too.
            usage_exclude = {"usage_purpose"} if request.usage_purpose == "practice" else set()
            digest = hashlib.sha256(request.model_dump_json(exclude={"requery_of_run_id", *usage_exclude}).encode()).hexdigest()
            compatible_digests = {digest, hashlib.sha256(request.model_dump_json(exclude=usage_exclude).encode()).hexdigest()}
            if usage_exclude:
                compatible_digests.update({hashlib.sha256(request.model_dump_json(exclude={"requery_of_run_id"}).encode()).hexdigest(),
                                           hashlib.sha256(request.model_dump_json().encode()).hexdigest()})
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
            if receipt and ("host_conversation_base_version" in (receipt.request_payload or {})
                    or workset_pages.is_workset(receipt.state_after or {})):
                base_version = (receipt.request_payload or {}).get("host_conversation_base_version")
                if type(base_version) is not int or base_version != conversation.version:
                    raise ValueError("CONVERSATION_VERSION_CONFLICT")
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
                "host_conversation_base_version": conversation.version,
                "resolved_explicit_filters": self._explicit_filters(effective_request)}
            initial_state = (deepcopy(receipt.state_before) if request.requery_of_run_id and receipt.state_before else
                             requery_state if requery_state is not None else dict(conversation.state))
            if not receipt.state_before: receipt.state_before=deepcopy(initial_state)
            receipt.cancel_requested=False
            receipt.error_code,receipt.finished_at=None,None
            session.flush()
            run = QueryRun(receipt.id, effective_request, conversation.id, conversation.version,
                           initial_state, RequestLimits(time.monotonic() + self.deadline_seconds,
                               max_calls=self.max_total_model_calls, max_tokens=self.max_tokens),
                           self._revisions(session))
            run.owner_id=self.owner_id
            run.write_question_ids = tuple(initial_state.get("current_page_ids") or [])
            run.write_result_page = not bool(request.requery_of_run_id) and authorizes_result_page_write(effective_request.message)
            if run.write_result_page:
                run.write_question_ids, run.write_target_bound = (), False
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
                restore_completion(run, prior)
            elif not prior and workset_pages.is_workset(run.resume.get("state") or {}):
                # A committed manifest checkpoint is not a completed result.
                # Restore its signed universe, then resume normal processing.
                restored = deepcopy(run.resume["state"])
                if (restored[workset_pages.STATE_KEY].get("revisions") == list(run.revisions)):
                    run.state = restored
                    workset_pages.validate(run, signing_key=self.signing_key, user_id=self.user_id)
        if run.resume["write"]:
            run.parts = run.resume["parts"]
            if run.resume["result"]:
                restore_completion(run, run.resume["result"])
            run.recovered_write_state=dict(run.state)
            with self.database.session() as s: run.state=dict(s.get(AgentTurn,run.id).state_before)
        if run.write_result_page:
            for part in run.parts:
                if part["intent"] in {"LIST", "SEARCH"}:
                    run.write_question_ids = tuple(row["canonical_question_id"] for row in part["facts"].get("data", [])
                        if row.get("canonical_question_id"))
                    run.write_target_bound = True
                    break
        return run

    def _requested_pipeline(self, run):
        """Resolve explicit request presence before the current saved preference."""
        if run.execution_pipeline is not None:
            return run.execution_pipeline
        if "pipeline" in run.request.model_fields_set:
            return run.request.pipeline
        return self.preferences.defaults().get("pipeline", run.request.pipeline)

    def complete_collection_plan(self, run):
        """Execute an unambiguous audited collection without a model round trip.

        Extra natural-language qualifiers still require normal planning and host
        validation. This selects a typed collection, never question IDs or labels.
        """
        if isinstance(run.request, StructuredQueryRequest):
            return None
        candidate = self._semantic_candidate_plan(run)
        if candidate is not None:
            return candidate
        selected_plan = self.collection_selection.resolve(run)
        if selected_plan is not None:
            return selected_plan
        current = run.request.message.strip()
        saved = run.state.get("list_request") or {}
        if (saved.get("semantic_task") == "ENGINEERING_DESIGN"
                and re.fullmatch(r"(?:下一页|下页|继续|继续查看下一页)[。！!]?", current)):
            return QuerySpec(action="NEXT")
        messages = user_messages(run)
        filters = run.request.filters
        if continues_scope(run) and (run.state.get("conversation_focus") or {}).get("semantic_task") == "ENGINEERING_DESIGN":
            inherited = (run.state.get("conversation_focus") or {}).get("filters") or {}
            filters = FilterSpec.model_validate({**inherited, **self._explicit_filters(run.request)})
        prior_filters = ((run.state.get("conversation_focus") or {}).get("filters") or {}) if continues_scope(run) else {}
        engineering, broad = engineering_collection_request(messages, filters, prior_filters=prior_filters)
        if not (engineering and broad):
            return None
        text = "\n".join(messages)
        if not re.search(r"全库|全集|全部|所有|完整|全量|穷尽|多少|几道|数量|总数|总共|一共", text):
            return None
        action = "COUNT" if re.search(r"多少|几道|数量|总数|总共|一共", current) else "LIST"
        top_n = collection_top_n(messages)
        page_size = collection_page_size(messages)
        if page_size is not None and not 1 <= page_size <= 100:
            return None
        if top_n is not None and (not 1 <= top_n <= 1000 or action == "COUNT" and re.search(r"前\s*\d+", current)):
            return None
        inherited_sort = (run.state.get("conversation_focus") or {}).get("sort", "frequency") if continues_scope(run) else "frequency"
        sort = collection_sort(current) or inherited_sort
        return QuerySpec(action=action, semantic_task="ENGINEERING_DESIGN", filters=filters,
            page_size=page_size if page_size is not None else run.request.page_size,
            sort=sort, top_n=None if action == "COUNT" else top_n)

    @staticmethod
    def _engineering_authorization(messages, scope, filters):
        """Bind only trusted original utterances, never an inferred model plan."""
        return {"version": "engineering_collection_authorization_v1", "semantic_task": "ENGINEERING_DESIGN",
            "source": "original_user_collection_request", "source_messages": list(messages),
            "source_evidence": [{"message_index": index, "quote": message, "start_char": 0,
                "end_char": len(message), "input_sha256": hashlib.sha256(message.encode()).hexdigest()}
                for index, message in enumerate(messages)],
            "scope_hash": scope["scope_hash"], "filters": filters.model_dump(mode="json")}

    def _uses_complete_workset(self, run, plan):
        scope = run.state.get("relevance_scope")
        if not scope or scope["target_unit"] != "BUSINESS_APPLICATION" or scope["retrieval_mode"] != "CANDIDATES":
            return False
        source = run.state.get("engineering_collection_authorization") or {}
        messages = engineering_all_source_messages(source.get("source_messages", []), plan.filters)
        if not messages:
            return False
        run.state["engineering_collection_authorization"] = self._engineering_authorization(messages, scope, plan.filters)
        return True

    def _checkpoint_workset(self, run):
        """Commit the complete universe before starting its first verification."""
        with self.database.session() as session, session.begin():
            turn = session.get(AgentTurn, run.id, with_for_update=True)
            if (not turn or turn.user_id != self.user_id or turn.status != "RUNNING"
                    or turn.owner_id != run.owner_id or turn.cancel_requested):
                raise ValueError("QUERY_CANCELLED")
            turn.state_after = deepcopy(run.state)
            snapshot = run.state[workset_pages.STATE_KEY]
            self.journal.append(session, turn, "stage", {"stage": "candidate_workset_batch_started" if snapshot.get("batch_attempt") else
                "candidate_workset_batch_committed" if snapshot["frontier"] else "candidate_workset_prepared",
                "candidate_total": len(snapshot["statuses"]), "processed": snapshot["frontier"]})

    def _complete_workset_result(self, session, run, plan, *, initial):
        from interview_intelligence.search.service import (
            prepare_engineering_workset, process_engineering_workset_batch, validate_engineering_workset,
        )
        scope = validate_relevance_scope(run.state["relevance_scope"])
        prior = run.state.get(workset_pages.STATE_KEY) or {}
        control = run.request.message.strip().rstrip("。！!？? ")
        own_checkpoint = bool(prior and (prior.get("source_run_id") == run.id
            or prior.get("last_batch_run_id") == run.id or (prior.get("batch_attempt") or {}).get("run_id") == run.id))
        reuse = bool(prior and (not run.requery_origin or own_checkpoint) and prior.get("scope_hash") == scope["scope_hash"]
            and prior.get("filters") == plan.filters.model_dump(mode="json")
            and (own_checkpoint or not initial or control in {"按题意查找候选", "全量分页", "全量", "全部", "全部分页",
                "查看全量", "查看全部", "下一页", "下页", "继续", "更多"}))
        initial_meta = None
        if not reuse:
            if not initial:
                raise ValueError("SEARCH_CANDIDATE_SCOPE_CHANGED")
            prepared = prepare_engineering_workset(session, self.retriever, plan.search_query, plan.filters,
                relevance_scope=scope, engineering_authorization=run.state["engineering_collection_authorization"],
                on_progress=lambda event: self.journal.event(run.id, "stage", event))
            initial_meta = prepared.get("meta", {})
            workset_pages.create(run, prepared["workset"], plan, initial_meta,
                signing_key=self.signing_key, user_id=self.user_id)
            self._checkpoint_workset(run)
        else:
            snapshot = workset_pages.validate(run, signing_key=self.signing_key, user_id=self.user_id)
            validate_engineering_workset(session, self.retriever, plan.filters,
                workset=snapshot["manifest"], relevance_scope=scope)
            if snapshot.get("batch_attempt"):
                raise ValueError("WORKSET_BATCH_OUTCOME_UNAVAILABLE")
            if initial and snapshot["page_size"] != plan.page_size:
                snapshot["page_size"] = plan.page_size
                snapshot["root_plan"]["page_size"] = plan.page_size
                workset_pages.seal(run, signing_key=self.signing_key, user_id=self.user_id)
        snapshot = run.state[workset_pages.STATE_KEY]
        # A committed batch advances the signed state, so the request's old
        # NEXT cursor cannot be reused after interrupted presentation. The
        # checkpoint binds that request's original visible offset separately.
        committed_here = snapshot.get("last_batch_run_id") == run.id
        page_offset = (snapshot["last_batch_page_offset"] if committed_here else
            workset_pages.offset(run, initial=initial, signing_key=self.signing_key, user_id=self.user_id))
        identities = ([] if snapshot.get("last_batch_run_id") == run.id else
            workset_pages.batch_ids(snapshot, page_offset))
        if identities:
            root = QuerySpec.model_validate(snapshot["root_plan"])
            workset_pages.start_batch(run, identities, page_offset, signing_key=self.signing_key, user_id=self.user_id)
            self._checkpoint_workset(run)
            result = process_engineering_workset_batch(session, self.retriever, root.search_query, plan.filters,
                workset=snapshot["manifest"], batch_ids=identities, relevance_scope=scope,
                on_progress=lambda event: self.journal.event(run.id, "stage", event))
            workset_pages.apply_batch(run, identities, result, signing_key=self.signing_key, user_id=self.user_id,
                page_offset=page_offset)
            self._checkpoint_workset(run)
            initial_meta = {**(initial_meta or {}), **result.get("meta", {})}
        return workset_pages.page(run, page_offset, signing_key=self.signing_key, user_id=self.user_id,
            initial_meta=initial_meta)

    def _semantic_candidate_plan(self, run, proposal=None):
        """Resolve a trusted semantic-mode choice and its continuations.

        An explicit engineering ALL source may authorize complete candidate
        processing. Other semantic search retains its bounded-result contract.
        """
        if isinstance(run.request, StructuredQueryRequest):
            return None
        current = run.request.message.strip().rstrip("。！!？? ")
        if re.search(r"解答|答案|怎么回答|解释|讲解|解题思路", current):
            # Looking at an answer does not start a different retrieval goal.
            return None
        previous = run.state.get("relevance_scope")
        if previous is not None:
            previous = validate_relevance_scope(previous)
        pending = run.state.get("pending_clarification") or {}
        prior_collection_page_size = ((run.state.get("collection_selection") or {}).get("root_plan") or {}).get("page_size")
        selected = current == "按题意查找候选" and current in pending.get("options", [])
        same_goal = bool(previous and (continues_scope(run) or run.requery_origin
            or current in previous["source_messages"]))
        retained = bool(same_goal and previous["retrieval_mode"] == "CANDIDATES")
        messages = user_messages(run)
        grant_messages = engineering_all_source_messages(messages, proposal.filters if proposal else run.request.filters)
        if same_goal and not grant_messages:
            grant_messages = engineering_all_source_messages(
                (run.state.get("engineering_collection_authorization") or {}).get("source_messages", []), run.request.filters)
        originals = {message.strip(): message for message in
            (run.state.get("engineering_collection_authorization") or {}).get("source_messages", [])} if same_goal else {}
        if engineering_all_source_messages([run.request.message], run.request.filters):
            originals[run.request.message.strip()] = run.request.message
        grant_messages = [originals.get(message.strip(), message) for message in grant_messages]
        all_choice = current in {"全量分页", "全量", "全部", "全部分页", "查看全量", "查看全部"}
        # A wide audited engineering collection already has its SQL ALL
        # contract. Only a clarified business target selects the workset mode;
        # candidate_mode alone must not invent an ANY scope for a SQL button.
        semantic_target = compile_relevance_scope(messages, candidate_mode=False)
        selected_all = bool(all_choice and grant_messages and semantic_target
            and semantic_target["target_unit"] == "BUSINESS_APPLICATION")
        selected = selected or selected_all
        scope = compile_relevance_scope(messages, candidate_mode=selected or retained)
        if not same_goal:
            run.state.pop("relevance_scope_recovery", None)
            run.state.pop(workset_pages.STATE_KEY, None)
            run.state.pop("engineering_collection_authorization", None)
            run.state.pop("complete_workset_mode", None)
            if not prior_collection_page_size:
                run.state.pop("semantic_query_page_size", None)
        if scope is None:
            if previous and not same_goal:
                for key in ("relevance_scope", "semantic_candidate_pages", "semantic_recall", "relevance_scope_recovery",
                        workset_pages.STATE_KEY, "engineering_collection_authorization", "complete_workset_mode"):
                    run.state.pop(key, None)
                if is_candidate_listing(run.state):
                    run.state.pop("list_request", None)
            return None
        # Old broad-category buttons have no authority over a newly clarified
        # business goal, even while the semantic mode is still undecided.
        run.state.pop("collection_selection", None)
        if not is_candidate_listing(run.state):
            for key in ("list_request", "list_goal", "list_explicit_filters"):
                run.state.pop(key, None)
        run.state["relevance_scope"] = scope
        if grant_messages and scope["target_unit"] == "BUSINESS_APPLICATION":
            run.state["engineering_collection_authorization"] = self._engineering_authorization(grant_messages, scope, run.request.filters)
            if selected or retained:
                run.state["complete_workset_mode"] = "ALL" if selected_all else run.state.get("complete_workset_mode", "CANDIDATES")
        if all_choice and not selected_all:
            return QuerySpec(action="CLARIFY", clarification=(
                "当前仍限定具体业务应用和业务流程，未增加传统或排除AI的条件。"
                "该细分范围尚无全量审核，不能把有限候选当作全库全集；可按当前题意检索候选，"
                "或明确切换到宽范围工程设计集合。"),
                clarification_options=["按题意查找候选", "查看完整工程设计集合"])
        requested = bool(selected or retained or re.search(r"题|找|查|列|有哪些", current))
        if not requested:
            return None
        complete = bool(re.search(r"全库|全集|全部|所有|完整|全量|穷尽", "\n".join(semantic_messages(messages))))
        if not complete and scope["retrieval_mode"] == "UNDECIDED":
            scope = compile_relevance_scope(messages, candidate_mode=True)
            run.state["relevance_scope"] = scope
            if proposal is None:
                return None
        snapshot = run.state.get(workset_pages.STATE_KEY) or run.state.get("semantic_candidate_pages") or {}
        explicit = self._explicit_filters(run.request)
        changed_filters = any(snapshot.get("filters", {}).get(key) != value for key, value in explicit.items())
        changed_page_size = ("page_size" in run.request.model_fields_set
            and snapshot.get("page_size") != run.request.page_size)
        if (retained and not run.requery_origin and is_candidate_listing(run.state)
                and snapshot.get("scope_hash") == scope["scope_hash"] and not changed_filters and not changed_page_size
                and re.fullmatch(r"下一页|下页|继续|继续找|继续查找|更多", current)):
            root = QuerySpec.model_validate(snapshot["root_plan"])
            return root.model_copy(update={"action": "NEXT", "search_query": None,
                "relevance_query": None, "lexical_facets": [], "preferred_question_type": None})
        old = run.state.get("last_plan") or {}
        # Typed mode choices are deterministic; parsing a new textual company,
        # date, role or taxonomy still belongs to the regular planner. Never
        # overwrite its supported filter with an empty shortcut proposal.
        from interview_intelligence.company import COMPANY_ALIASES
        from interview_intelligence.domain.models import Interview
        with self.database.session() as session:
            companies = set(session.scalars(select(Interview.company_normalized).distinct())) - {None, ""}
        filter_language = "\n".join(messages)
        needs_filter_plan = (any(alias.casefold() in filter_language.casefold()
            for alias in {*COMPANY_ALIASES, *COMPANY_ALIASES.values(), *companies})
            or bool(re.search(r"公司|岗位|职位|轮次|[一二三四1234]面|最近|近\s*\d|今年|去年|\d{4}|日期|时间范围|题型|分类标签|主题标签|Java|Python|\bGo\b|Rust|SQL", filter_language, re.I)))
        if needs_filter_plan and proposal is None:
            return None
        values = (run.state.get("filters") or {}) if same_goal else {}
        if proposal is not None and needs_filter_plan:
            values = {**values, **proposal.filters.model_dump(mode="json")}
        filters = FilterSpec.model_validate({**values, **explicit})
        from interview_intelligence.agent.query_scope import _engineering_filters
        trusted = {**inherited_ui_filters(run), **explicit}
        filters, _ = _engineering_filters(QuerySpec(action="LIST", filters=filters), trusted, messages)
        page_size = collection_page_size(messages)
        if page_size is None:
            if "page_size" in run.request.model_fields_set:
                page_size = run.request.page_size
            elif same_goal or prior_collection_page_size:
                page_size = prior_collection_page_size or run.state.get("semantic_query_page_size") or old.get("page_size", run.request.page_size)
            else:
                page_size = run.request.page_size
        if not 1 <= page_size <= 50:
            return QuerySpec(action="CLARIFY", clarification=(
                "已保留当前题意和候选模式。候选分页每页支持1到50题，请调整每页数量；"
                "这不会改变当前业务范围。"))
        run.state["semantic_query_page_size"] = page_size
        goal = relevance_intent(messages)
        recall = proposal if proposal is not None and proposal.action == "SEARCH" else None
        prior_recall = run.state.get("semantic_recall") or {}
        if (recall is None and same_goal and prior_recall.get("version") == RECALL_VERSION
                and prior_recall.get("scope_hash") == scope["scope_hash"]):
            saved_recall = snapshot.get("root_plan") or old
            if saved_recall.get("action") == "SEARCH":
                recall = QuerySpec.model_validate(saved_recall)
        original_recall = recall_intent(messages)
        recall_query = recall.search_query if recall else original_recall
        run.state["semantic_recall"] = {"version": RECALL_VERSION, "scope_hash": scope["scope_hash"],
            "source": "user_messages" if recall_query == original_recall else "planner_soft_expansion",
            "query": recall_query, "hard_constraint": False}
        return QuerySpec(action="SEARCH", filters=filters, search_query=recall_query,
            relevance_query=goal if len(goal) <= 500 else None,
            lexical_facets=recall.lexical_facets if recall else [],
            preferred_question_type=recall.preferred_question_type if recall else None,
            page_size=page_size,
            sort=collection_sort(current) or old.get("sort", "frequency"), final=True)

    def model_context(self, run):
        session_state = deepcopy(run.state)
        for key in (workset_pages.STATE_KEY, "engineering_collection_authorization"):
            session_state.pop(key, None)
        if run.requery_origin.get("action") == "LIST" and run.tool_calls == 0:
            # A previous model's SQL list is a reference for replanning, not
            # another copy of the user's active filter constraints. Keep its
            # complete last_plan available, without presenting inferred fields
            # as active filters or inherited SQL capabilities to the model.
            explicit = self._explicit_filters(run.request)
            for key in ("filters", "list_request"):
                if key in session_state:
                    for name, field in FilterSpec.model_fields.items():
                        session_state[key][name] = explicit.get(name, field.default)
            for key in ("question_type_scope", "search_filter_scope"):
                session_state.pop(key, None)
        focus = self.context_compiler.conversation_focus(run.state)
        if focus:
            session_state["conversation_focus"] = focus
        context = {"message": run.request.message, "today": self.today().isoformat(),
                "user_goal": run.user_goal or None, "completion": run.completion or None,
                "filter_catalog": self._filter_catalog(),
                "explicit_filters": self._explicit_filters(run.request),
                "default_page_size":run.request.page_size if "page_size" in run.request.model_fields_set else self.preferences.defaults().get("page_size",run.request.page_size),
                "requested_pipeline": self._requested_pipeline(run),
                "preferences":self.preferences.defaults(),"session": session_state,
                "query_feedback":self.feedback_memory.selected(run.request.feedback_ids),
                "query_memory":self.feedback_memory.matching(run.request.message),
                "requery_origin":run.requery_origin,
                "completed_tools":[{"intent":p["intent"],"answer":p["answer"],"meta":{
                    key: value for key, value in p["facts"]["meta"].items()
                    if key not in {"workset_preparation_audit", "workset_batch_audit", workset_pages.STATE_KEY}}}
                                   for p in run.parts]}
        if self.compact_context:
            context = self.context_compiler.compile(
                **{k: v for k, v in context.items() if k not in {"session", "completed_tools"}},
                state=session_state, parts=run.parts, terminal=run.terminal,
                remaining_tools=8-run.tool_calls)
        if self.dynamic_tools:
            context["tool_policy"] = self.tool_policy(run, planning_pending=True).model_dump()
        if not self.dynamic_tools:
            context.pop("tool_policy", None)
        context["budget"] = {"remaining_tools": max(0, 8 - run.tool_calls),
            **self.tool_policy(run, planning_pending=True).model_budget}
        return context

    def tool_policy(self, run, *, current_reserved=False, planning_pending=False):
        policy = ToolPolicy.compile(run.request.message, run.state, terminal=run.terminal,
                                  remaining_tools=8-run.tool_calls + int(current_reserved),
                                  read_only=bool(run.request.requery_of_run_id))
        # Before declaration, keep one-call DIRECT_RESULT queries discoverable.
        # bind_goal precedes execution, where SYNTHESIS must leave its reserve.
        reserve = int(run.user_goal.get("response_mode") == "SYNTHESIS")
        pipeline = self._requested_pipeline(run)
        search_cost = {"BM25": 0, "DENSE": 1, "HYBRID": 1, "HYBRID_RERANK": 3}[pipeline]
        snapshot = run.state.get(workset_pages.STATE_KEY) or {}
        # Cached paging is free; advancing an unprocessed workset may invoke
        # ranking/verification and must not be advertised as a zero-cost read.
        next_cost = 0
        if snapshot:
            try:
                offset = workset_pages.offset(run, signing_key=self.signing_key, user_id=self.user_id)
                next_cost = 3 if workset_pages.batch_ids(snapshot, offset) else 0
            except ValueError:
                # An invalid cursor never grants a cheaper processing route;
                # the normal host scope check still rejects its execution.
                next_cost = 3
        return policy.with_budget(remaining_shared_calls=max(0, run.limits.max_calls-run.limits.calls),
            remaining_planning_calls=max(0, self.max_planning_calls-run.model_calls),
            pending_planning_calls=int(planning_pending), completion_reserve_calls=reserve,
            search_model_calls=search_cost, next_model_calls=next_cost)

    def _attach_harness(self, run):
        """Return refreshed capabilities after a tool changes the trusted state."""
        compiled = self.context_compiler.compile(message=run.request.message,
            today=self.today().isoformat(), explicit_filters={}, preferences={},
            state=run.state, default_page_size=run.request.page_size,
            requested_pipeline=run.request.pipeline, terminal=run.terminal,
            remaining_tools=8-run.tool_calls, requery_origin=run.requery_origin,
            user_goal=run.user_goal or None, completion=run.completion or None)
        metadata = {"context_contract": compiled["context_contract"], "session": compiled["session"]}
        metadata.update(user_goal=run.user_goal or None, completion=run.completion or None)
        metadata["budget"] = {"remaining_tools": max(0, 8-run.tool_calls),
            **self.tool_policy(run, planning_pending=True).model_budget}
        metadata["evidence"] = [self.context_compiler.evidence_row(row) for row in run.result["facts"].get("data", [])[:EVIDENCE_RESULT_ROWS]]
        if self.dynamic_tools:
            metadata["tool_policy"] = self.tool_policy(run, planning_pending=True).model_dump()
        run.result["facts"]["meta"]["harness"] = metadata

    def execute(self, run: QueryRun, name: str, plan: QuerySpec) -> dict:
        try:
            run.execution_pipeline = self._requested_pipeline(run)
            return self._execute(run,name,plan)
        except BaseException as error:
            self.journal.fail_tool(run,name,plan,error)
            raise
        finally:
            run.execution_pipeline = None

    def _execute(self, run: QueryRun, name: str, plan: QuerySpec) -> dict:
        run.limits.check()
        if run.tool_calls >= 8 or run.terminal:
            raise ValueError("QUERY_TOOL_BUDGET_EXCEEDED")
        if name not in TOOL_ACTIONS or plan.action not in TOOL_ACTIONS[name]:
            raise ValueError("INVALID_TOOL_PLAN")
        bind_goal(run, plan.user_goal, structured=isinstance(run.request, StructuredQueryRequest))
        semantic_plan = self._semantic_candidate_plan(run, proposal=plan)
        if semantic_plan is not None and plan.action in {"LIST", "SEARCH", "NEXT", "CLARIFY", "COUNT", "STATS"}:
            plan = semantic_plan
            name = "search_questions" if plan.action == "SEARCH" else "list_questions"
        # This must also run on direct provider/tool calls, not only the API's
        # planner shortcut. A forged or misspelled category cannot become an
        # unscoped model LIST, and typed UI provenance remains authoritative.
        if not run.collection_event:
            choice_plan = self.collection_selection.resolve(run)
            if choice_plan is not None:
                plan = choice_plan
                name = "get_question_count" if plan.action == "COUNT" else "list_questions"
        if (plan.action == "NEXT" and run.tool_calls == 0 and run.requery_origin and
                run.requery_origin["target_action"] in {"LIST", "STATS"}):
            plan = QuerySpec.model_validate({**run.state["last_plan"], "final": plan.final})
            name = "get_question_stats" if plan.action == "STATS" else "list_questions"
        semantic_next = plan.action == "NEXT" and is_candidate_listing(run.state)
        if plan.action == "NEXT" and not semantic_next and not (run.state.get("list_request") or {}).get("cursor"):
            plan = QuerySpec(action="CLARIFY", clarification="当前没有下一页，请先查询一个题目列表。")
        # Exposure is an optimization; authorization always remains a host check,
        # including when the decision provider bypasses Pi's visible tool schema.
        self.tool_policy(run).require(name, plan.action)
        if plan.action == "ANSWER":
            if any(identity not in run.state.get("current_page_ids", []) for identity in plan.question_ids):
                raise ValueError("QUESTION_NOT_IN_SESSION")
            available_evidence = evidence_by_ordinal(run.parts)
            for ordinal in plan.evidence_tool_ordinals:
                if ordinal not in available_evidence or not referenceable_evidence(available_evidence[ordinal]):
                    raise ValueError("QUERY_ANSWER_TOOL_EVIDENCE_INVALID")
            selected_parts = ([available_evidence[index] for index in plan.evidence_tool_ordinals]
                              if plan.evidence_tool_ordinals else run.parts)
            has_aggregate_evidence = any(referenceable_evidence(part) for part in selected_parts)
            if (run.user_goal.get("source") == "model_plan" and plan.answer_basis in {"CORPUS", "MIXED"}
                    and not plan.question_ids and not plan.evidence_tool_ordinals):
                raise ValueError("QUERY_ANSWER_TOOL_EVIDENCE_REQUIRED")
            if plan.answer_basis in {"CORPUS", "MIXED"} and not plan.question_ids and not has_aggregate_evidence:
                raise ValueError("QUERY_ANSWER_EVIDENCE_REQUIRED")
            if plan.answer_basis == "GENERAL_KNOWLEDGE" and plan.question_ids:
                raise ValueError("QUERY_ANSWER_BASIS_CONFLICT")
            if plan.answer_basis == "GENERAL_KNOWLEDGE" and plan.evidence_tool_ordinals:
                raise ValueError("QUERY_ANSWER_BASIS_CONFLICT")
            if (run.user_goal.get("source") == "model_plan" and run.parts
                    and plan.answer_basis == "GENERAL_KNOWLEDGE"):
                raise ValueError("QUERY_ANSWER_BASIS_CONFLICT")
            if any(part.get("facts", {}).get("meta", {}).get("relevance_status") == "UNAVAILABLE"
                   for part in run.parts):
                raise ValueError("QUERY_RELEVANCE_UNAVAILABLE")
        if plan.action == "RECORD_REVIEW" and any(
            item.status not in authorized_statuses(run.request.message) for item in plan.review_items
        ):
            raise ValueError("QUERY_WRITE_STATUS_CONFLICT")
        if plan.action == "RECORD_REVIEW" and any(
            item.canonical_question_id not in run.write_question_ids for item in plan.review_items
        ):
            raise ValueError("QUERY_WRITE_SCOPE_CONFLICT")
        run.tool_calls += 1
        automatic_annotation = bool((plan.filters.response_form or plan.filters.coding_focus) and not plan.filters.annotation_status
                                    and not run.collection_event)
        if automatic_annotation:
            plan=plan.model_copy(update={"filters":plan.filters.model_copy(update={"annotation_status":self.task_annotation_policy})})
        if not self.task_filters_enabled and (plan.filters.coding_focus or plan.filters.response_form):
            raise ValueError("TASK_FILTERS_DISABLED")
        if plan.action == "NEXT" and not semantic_next:
            saved = run.state.get("list_request")
            if not saved or not saved.get("cursor"):
                plan = QuerySpec(action="CLARIFY", clarification="当前没有下一页，请先查询一个题目列表。")
            else:
                saved_request = (StatsListRequest if saved.get("group_by") else ListRequest).model_validate(saved)
                saved_filters = filter_fields(saved_request)
                plan = plan.model_copy(update={"filters": saved_filters, "sort": saved_request.sort,
                    "top_n": saved_request.top_n, "page_size": saved_request.page_size,
                    "group_by": getattr(saved_request, "group_by", "question"),
                    "topic_level": getattr(saved_request, "topic_level", "L1"),
                    "semantic_task": saved_request.semantic_task,
                    "review_statuses":saved_request.review_statuses,"review_order":saved_request.review_order})
        explicit = self._explicit_filters(run.request)
        if any(plan.filters.model_dump(mode="json").get(k) != v for k, v in explicit.items()) and plan.action != "CLARIFY":
            raise ValueError("EXPLICIT_FILTER_CONFLICT")
        name, plan, user_scope_policy = guard_plan(run, name, plan, explicit,
            structured=isinstance(run.request, StructuredQueryRequest) or bool(run.collection_event))
        selection = self.collection_selection.prepare(run, plan)
        plan = selection.plan
        if plan.action == "CLARIFY" and selection.metadata:
            name = "list_questions"
        if selection.metadata:
            user_scope_policy["route_decision"] = "host_collection_selection"
            user_scope_policy["user_messages"] = list(run.state["collection_selection"]["user_messages"])
        self.tool_policy(run, current_reserved=True).require(name, plan.action)
        question_type_policy = None
        search_dimension_policy = None
        search_ranking = self._search_ranking(run) if plan.action == "SEARCH" else None
        if plan.action == "SEARCH":
            plan, question_type_policy = self._search_question_type(run, plan, explicit)
            plan, search_dimension_policy = self._search_semantic_dimensions(run, plan, explicit, automatic_annotation)
            if search_ranking == "frequency":
                plan = plan.model_copy(update={"sort": "frequency"})
        invocation_id,previous=self.journal.prepare(run,run.tool_calls,name,plan)
        if previous:
            run.result=previous
            restore_completion(run, previous)
            return previous
        started = time.perf_counter()
        warnings = []
        if plan.action == "SEARCH":
            requested_pipeline = self._requested_pipeline(run)
            if run.state.get("relevance_scope") and requested_pipeline != "HYBRID_RERANK":
                raise ValueError("RELEVANCE_SCOPE_REQUIRES_VERIFICATION")
            plan = plan.model_copy(update={"pipeline":requested_pipeline})
        token = current_limits.set(run.limits)
        reserve_token = current_call_reserve.set(int(run.user_goal.get("response_mode") == "SYNTHESIS"
            and plan.action not in {"ANSWER", "CLARIFY", "RECORD_REVIEW"}))
        try:
            with self.database.session() as session:
                if session.bind.dialect.name == "postgresql":
                    session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                if self._revisions(session) != run.revisions:
                    raise ValueError("SNAPSHOT_CHANGED")
                if semantic_next:
                    result = (self._complete_workset_result(session, run, plan, initial=False)
                        if workset_pages.is_workset(run.state) else
                        candidate_page(run, signing_key=self.signing_key, user_id=self.user_id))
                    list_state = result["meta"]["list_request"]
                elif plan.action == "SEARCH" and self._uses_complete_workset(run, plan):
                    result = self._complete_workset_result(session, run, plan, initial=True)
                    list_state = result["meta"]["list_request"]
                    search_ranking = "relevance"
                    progress = result["meta"]["candidate_workset_progress"]
                    if progress["unprocessed"]:
                        warnings.append(f"完整候选范围共 {progress['candidate_total']} 道，尚有 {progress['unprocessed']} 道未核验；候选数量不是业务题总数。")
                    if progress["pending"]:
                        warnings.append(f"已保留 {progress['pending']} 道待核验候选。")
                elif plan.action in {"LIST", "NEXT","STATS"}:
                    req = (run.request.list_request if isinstance(run.request, StructuredQueryRequest) else
                           (StatsListRequest if run.state["list_request"].get("group_by") else ListRequest).model_validate(run.state["list_request"]) if plan.action == "NEXT" else
                           StatsListRequest(**plan.filters.model_dump(),group_by=plan.group_by,topic_level=plan.topic_level,sort=plan.sort,
                               top_n=plan.top_n,page_size=plan.page_size,review_statuses=plan.review_statuses,review_order=plan.review_order,semantic_task=plan.semantic_task) if plan.action=="STATS" else
                           ListRequest(**plan.filters.model_dump(), sort=plan.sort, top_n=plan.top_n, page_size=plan.page_size,
                                       review_statuses=plan.review_statuses,review_order=plan.review_order,semantic_task=plan.semantic_task))
                    if isinstance(run.request,StructuredQueryRequest):
                        req=req.model_copy(update={"annotation_status":plan.filters.annotation_status})
                    if selection.category_id and plan.action in {"LIST", "NEXT"}:
                        req = req.model_copy(update={"collection_category": selection.category_id})
                    selection_as_of = (run.state.get("collection_selection") or {}).get("as_of") if selection.metadata else None
                    result = list_questions(session, req, signing_key=self.signing_key, user_id=self.user_id,
                        as_of=date.fromisoformat(selection_as_of) if selection_as_of else self.today(),
                        canonical_ids=selection.canonical_ids if plan.action == "STATS" or
                            plan.action == "NEXT" and plan.group_by != "question" else None)
                    warnings = result.get("warnings", [])
                    list_state = req.model_dump(mode="json")
                    list_state["cursor"] = result["meta"]["pagination"]["next_cursor"]
                    next_filters = filter_fields(req)
                    plan = plan.model_copy(update={"filters": next_filters, "sort": req.sort,
                                                  "top_n": req.top_n, "page_size": req.page_size,
                                                  "group_by":getattr(req,"group_by","question"),
                                                  "topic_level":getattr(req,"topic_level","L1"), "semantic_task":req.semantic_task})
                elif plan.action == "COUNT":
                    semantic_scope = load_semantic_scope(session, plan.filters, task=plan.semantic_task) if plan.semantic_task else None
                    accepted_ids = semantic_scope.accepted_occurrence_ids if semantic_scope else None
                    pending_ids = semantic_scope.pending_occurrence_ids if semantic_scope else ()
                    if semantic_scope and selection.canonical_ids is not None:
                        from interview_intelligence.domain.models import QuestionOccurrence
                        selected_occurrences = set(session.scalars(select(QuestionOccurrence.id).where(
                            QuestionOccurrence.canonical_question_id.in_(selection.canonical_ids))))
                        accepted_ids = tuple(identity for identity in accepted_ids if identity in selected_occurrences)
                        pending_ids = tuple(identity for identity in pending_ids if identity in selected_occurrences)
                    counts = count_questions(session, plan.filters, user_id=self.user_id, review_statuses=plan.review_statuses,
                        occurrence_ids=accepted_ids)
                    scoped = any(value is not None for key, value in plan.filters.model_dump(mode="json").items()
                                 if key != "date_basis") or bool(plan.review_statuses)
                    result = {"data": [], "meta": {"route": "SQL", "counts": counts,
                        "count_scope": "filtered_corpus" if scoped or plan.semantic_task else "full_corpus"}}
                    if semantic_scope:
                        result["meta"].update(semantic_task=plan.semantic_task, semantic_scope=semantic_scope.metadata())
                        if plan.semantic_task == "ENGINEERING_DESIGN" and not semantic_scope.metadata()["complete"]:
                            result["meta"]["pending_counts"] = count_questions(session, plan.filters,
                                user_id=self.user_id, review_statuses=plan.review_statuses,
                                occurrence_ids=pending_ids)
                            warnings.append("工程设计范围仍有待核验提问，确认计数不能当作完整总数。")
                elif plan.action == "ANSWER":
                    rows = [get_question_detail(session, identity, plan.filters) for identity in plan.question_ids]
                    if any(not row.get("occurrence_count") for row in rows):
                        raise ValueError("QUERY_ANSWER_EVIDENCE_OUT_OF_SCOPE")
                    result = {"data": rows, "meta": {"route": "ANSWER", "answer_kind": plan.answer_kind,
                        "answer_basis": plan.answer_basis, "evidence_question_ids": list(plan.question_ids),
                        "evidence_tool_ordinals": list(plan.evidence_tool_ordinals),
                        "evidence_notice": ("根据通用知识生成的参考回答。" if plan.answer_basis == "GENERAL_KNOWLEDGE" else
                            "题库来源仅证明题目与提问记录；解答为模型生成的参考内容。")}}
                    if plan.evidence_tool_ordinals:
                        result["meta"]["answer_evidence"] = [{"ordinal": ordinal,
                            "intent": available_evidence[ordinal]["intent"],
                            "meta": self.context_compiler.evidence_meta(available_evidence[ordinal]["facts"]["meta"])}
                            for ordinal in plan.evidence_tool_ordinals]
                elif plan.action == "SEARCH":
                    corpus = session.get(CorpusState, 1)
                    if not self.retriever or not corpus.current_revision or corpus.indexed_revision != corpus.current_revision:
                        raise ValueError("INDEX_NOT_READY")
                    semantic_scope = run.state.get("relevance_scope")
                    stages = {"HYBRID_RERANK": ["HYBRID_RERANK", "HYBRID", "BM25"],
                              "HYBRID": ["HYBRID", "BM25"], "DENSE": ["DENSE", "BM25"], "BM25": ["BM25"]}[plan.pipeline]
                    if semantic_scope:
                        stages = ["HYBRID_RERANK"]
                    for index, executed in enumerate(stages):
                        run.limits.check()
                        try:
                            result = search_questions(session, self.retriever, plan.search_query, plan.filters,
                                top_k=50 if search_ranking == "frequency" or semantic_scope else plan.page_size,
                                pipeline=executed, relevance_query=user_scope_policy["relevance"]["applied"],
                                lexical_facets=plan.lexical_facets,
                                preferred_question_type=(plan.preferred_question_type or (question_type_policy["planned_question_type"]
                                    if question_type_policy and question_type_policy["decision"] == "model_inferred_softened" else None)),
                                on_progress=lambda event:self.journal.event(run.id,"stage",event),
                                **({"relevance_scope": semantic_scope} if semantic_scope else {}))
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
                    empty_scope = result["meta"]["eligible_count"] == 0 and not result["data"]
                    if empty_scope:
                        result["meta"]["verification_basis"] = "empty_sql_scope"
                    verified = executed == "HYBRID_RERANK" and (empty_scope or
                        result["meta"].get("rerank_status") == "COMPLETED"
                        and result["meta"].get("candidate_verification_status") in {None, "COMPLETE"}
                        and result["meta"].get("invalid_candidate_count", 0) == 0
                        or partial and bool(result["data"]))
                    if plan.pipeline == "HYBRID_RERANK" and not verified and result["meta"].get("eligible_count", 0):
                        result["data"] = []
                        warnings = ["相关性核验未完成，请重新检索，或明确选择原始混合检索查看候选。"]
                    if search_ranking == "frequency":
                        # The retriever verifies at most 50 candidates. Rank that
                        # entire verified set before applying the displayed page size.
                        matches = result["data"]
                        result["data"] = sorted(matches, key=lambda row: (
                            (row.get("relevance_scope_status") or (row.get("retrieval") or {}).get("relevance_scope_status")) == "PENDING",
                            -row["occurrence_count"]))
                        if not semantic_scope:
                            result["data"] = result["data"][:plan.page_size]
                        retrieval_selection = result["meta"].get("result_selection_policy")
                        if retrieval_selection:
                            result["meta"]["retrieval_selection_policy"] = retrieval_selection
                        result["meta"]["result_selection_policy"] = "occurrence_count_desc_then_retrieval_order_v1"
                        visible = {row["canonical_question_id"] for row in result["data"]}
                        audit = result["meta"].get("rerank_audit")
                        if isinstance(audit, dict) and isinstance(audit.get("candidates"), list):
                            result["meta"]["rerank_audit"] = {**audit, "candidates": [
                                {**item, "decision": "PAGE_CUTOFF"}
                                if item.get("decision") == "RETURNED" and item.get("canonical_question_id") not in visible
                                else item for item in audit["candidates"]]}
                        result["meta"].update({"frequency_ranking": {
                            "basis": "occurrence_count", "matched_candidates": len(matches),
                            "candidate_pool_limit": 50,
                            "scope": "verified_semantic_candidates" if verified else "retrieval_candidates"}})
                        warnings.append("频次排序仅覆盖本次最多 50 个检索候选，不能据此断言全库最高频算法题。")
                    result["meta"].update({"relevance_status": "VERIFIED" if verified else
                        "UNAVAILABLE" if plan.pipeline == "HYBRID_RERANK" else "UNVERIFIED",
                        "returned_count": len(result["data"])})
                    result["meta"].update({"route": "SEARCH", "requested_pipeline": plan.pipeline,
                        "executed_pipeline": executed, "degraded": executed != plan.pipeline,
                        "ranking_scope": "semantic_candidates", "search_ranking": search_ranking})
                    result["meta"]["result_completeness"] = search_completeness(result["meta"], len(result["data"]))
                    if semantic_scope:
                        result["meta"]["semantic_recall"] = deepcopy(run.state.get("semantic_recall") or {})
                        if run.state.get("relevance_scope_recovery"):
                            result["meta"]["relevance_scope_recovery"] = deepcopy(run.state["relevance_scope_recovery"])
                        result = store_candidates(run, result, plan, signing_key=self.signing_key, user_id=self.user_id)
                        list_state = result["meta"]["list_request"]
                    if partial:
                        warnings.append(partial_verification_warning(result["meta"]))
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
            current_call_reserve.reset(reserve_token)
            current_limits.reset(token)
        result.setdefault("meta", {}).update({"corpus_revision": run.revisions[0],
            "request_id": run.request.request_id,
            "task_annotation_revision": run.revisions[1], "user_state_revision": latest[2],
            "applied_filters": plan.filters.model_dump(mode="json")})
        if selection.metadata:
            result["meta"]["collection_selection"] = selection.metadata
        if question_type_policy:
            result["meta"]["question_type_filter_policy"] = question_type_policy
        if search_dimension_policy:
            result["meta"]["search_filter_policy"] = search_dimension_policy
        user_scope_policy["applied_action"] = plan.action
        user_scope_policy["semantic_task"]["applied"] = plan.semantic_task
        result["meta"]["user_scope_policy"] = user_scope_policy
        if plan.action in {"LIST", "NEXT", "STATS", "SEARCH", "COUNT"}:
            trusted_ui = {**inherited_ui_filters(run), **explicit}
            run.state["query_scope_intent"] = {"version": USER_SCOPE_POLICY_VERSION,
                "messages": user_scope_policy["user_messages"],
                "explicit_filters": {key: value for key, value in trusted_ui.items()
                    if value is not None and plan.filters.model_dump(mode="json").get(key) == value}}
        if run.request.requery_of_run_id:
            result["meta"]["requery_of_run_id"] = run.request.requery_of_run_id
        result["meta"].setdefault("timings",{}).update({"tool_ms":int((time.perf_counter()-started)*1000)})
        run.plan = plan
        rows = result.get("data", [])
        run.result = build_query_response(plan, result, search_ranking=search_ranking,
            tool_name=name, prompt_version=self.prompt_version, warnings=warnings)
        run.completion = completion_for(run, plan)
        run.result["facts"]["meta"].update(user_goal=dict(run.user_goal), completion=dict(run.completion))
        run.parts.append(run.result)
        run.terminal=run.completion["terminal"]
        run.state["user_goal"] = dict(run.user_goal)
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
                "semantic_task": plan.semantic_task,
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
        if plan.action == "SEARCH":
            run.state["search_ranking"] = search_ranking
        run.state["current_page_summary"] = [self.context_compiler.evidence_row(row) for row in rows[:10]]
        if run.write_result_page and not run.write_target_bound and plan.action in {"LIST", "SEARCH"}:
            run.write_question_ids = tuple(run.state["current_page_ids"])
            run.write_target_bound = True
        if plan.filters.question_type is None:
            run.state.pop("question_type_scope", None)
        elif plan.action in {"LIST", "STATS", "NEXT", "SEARCH"}:
            inherited = self._question_type_scope(run.state)
            source = ("explicit_ui" if "question_type" in explicit else
                      "explicit_text" if plan.action == "SEARCH" and explicit_taxonomy_value(user_messages(run), "question_type", plan.filters.question_type) else
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
            elif explicit_taxonomy_value(user_messages(run), key, value):
                search_scope[key] = {"value": value, "source": "explicit_text"}
            elif value == inherited.get("value"):
                search_scope[key] = inherited
        if search_scope:
            run.state["search_filter_scope"] = {"version": SEARCH_DIMENSION_POLICY_VERSION, "fields": search_scope}
        else:
            run.state.pop("search_filter_scope", None)
        self._remember(run)
        semantic_listing = bool(run.state.get("relevance_scope") and (
            plan.action == "SEARCH" or semantic_next))
        if plan.action in {"LIST", "NEXT","STATS"} or semantic_listing:
            run.state["list_request"] = list_state
            run.state["result_set_id"]=result["meta"].get("result_set_id")
            if plan.action in {"LIST", "STATS"} or semantic_listing and plan.action == "SEARCH":
                run.state["list_goal"] = {"message": run.request.message, "run_id": run.id,
                                          "intent": plan.action}
                run.state["list_explicit_filters"] = (deepcopy((run.state.get("query_scope_intent") or {}).get("explicit_filters", {}))
                    if semantic_listing else self._explicit_filters(run.request))
            elif "list_explicit_filters" in run.state or self._explicit_filters(run.request):
                run.state["list_explicit_filters"] = {**run.state.get("list_explicit_filters", {}),
                                                       **self._explicit_filters(run.request)}
        elif plan.action not in {"CLARIFY", "ANSWER", "DETAILS", "REVIEW_STATE", "RECORD_REVIEW"}:
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
        if run.state.get("relevance_scope") and (intent == "SEARCH" or intent == "NEXT" and is_candidate_listing(run.state)):
            intent = "SEARCH"
            message = relevance_intent(run.state["relevance_scope"]["source_messages"])
        elif intent == "NEXT":
            intent = "STATS" if (run.state.get("list_request") or {}).get("group_by") else "LIST"
            message = ((run.state.get("list_goal") or {}).get("message") or
                       (previous.get("message") if previous.get("intent") in {"LIST", "STATS"} else None) or message)
        run.state["conversation_focus"] = {
            "intent": intent, "source_action": plan.action, "message": message,
            "filters": plan.filters.model_dump(mode="json"), "sort": plan.sort, "top_n": plan.top_n,
            "review_statuses": list(plan.review_statuses), "review_order": plan.review_order,
            "group_by": plan.group_by, "topic_level": plan.topic_level, "run_id": run.id,
            "semantic_task": plan.semantic_task,
            **({"answer_kind": plan.answer_kind, "answer_basis": plan.answer_basis,
                "question_ids": list(plan.question_ids)} if plan.action == "ANSWER" else {})}

    def finish(self, run: QueryRun, *, provider: str, timings: dict | None = None):
        if not run.result:
            raise ValueError("QUERY_PLAN_MISSING")
        restore_completion(run, run.result)
        if not run.terminal:
            raise ValueError("QUERY_RESPONSE_INCOMPLETE")
        response = run.result
        if response["facts"]["meta"].get("relevance_status") == "UNAVAILABLE":
            raise ValueError("QUERY_RELEVANCE_UNAVAILABLE")
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
            record_usage(session, user_id=self.user_id, request_key="agent:" + turn.id,
                parts=run.parts or run.resume.get("parts") or [response],
                purpose=run.request.usage_purpose, occurred_at=turn.finished_at)
            self.journal.append(session,turn,"completed",{"result":response})
        self.runs.pop(run.id, None)
        return response

    def fail(self, run, error=None):
        run.limits.cancelled.set()
        with self.database.session() as session, session.begin():
            turn = session.scalar(select(AgentTurn).where(AgentTurn.user_id == self.user_id,
                                                           AgentTurn.request_id == run.request.request_id))
            if turn and turn.status == "RUNNING":
                if (turn.owner_id == run.owner_id and workset_pages.is_workset(run.state)
                        and run.state[workset_pages.STATE_KEY].get("last_batch_run_id") == run.id):
                    # A separate failure transaction can salvage a completed
                    # in-memory batch after its checkpoint failed. If this also
                    # fails, the pre-provider PROCESSING receipt prevents an
                    # automatic second call; exactly-once cannot be promised.
                    conversation = session.get(AgentConversation, run.conversation_id)
                    if conversation and conversation.version == run.version:
                        workset_pages.validate(run, signing_key=self.signing_key, user_id=self.user_id)
                        turn.state_after = deepcopy(run.state)
                interrupted=type(error).__name__=="CancelledError" and not turn.cancel_requested
                turn.status = "INTERRUPTED" if interrupted else "CANCELLED" if str(error)=="QUERY_CANCELLED" else "FAILED"
                turn.error_code="QUERY_INTERRUPTED" if interrupted else str(error or "QUERY_FAILED")[:128]
                turn.finished_at=now_utc()
                self.journal.close_tools(session,turn,"CANCELLED" if turn.status=="CANCELLED" else "INTERRUPTED",turn.error_code)
                self.journal.append(session,turn,"interrupted" if interrupted else "failed",{"code":turn.error_code,"partial":bool(turn.response)})
        self.runs.pop(run.id, None)
