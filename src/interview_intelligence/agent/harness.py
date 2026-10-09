"""Compile bounded, attributable model context and enforce host tool capabilities.

Policies are derived from the current request and authoritative state. A tool
description, previous conversation, or model plan cannot grant write permission.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from interview_intelligence.agent.query_contract import TOOL_ACTIONS
from interview_intelligence.contracts import FilterSpec

CONTEXT_VERSION = "query_context_v4"
POLICY_VERSION = "query_tool_policy_v2"
STATE_FIELDS = ("filters", "sort", "last_plan", "list_request", "current_page_ids",
                "corpus_revision", "task_annotation_revision", "pending_clarification", "question_type_scope",
                "search_filter_scope", "last_response", "last_count", "current_page_summary", "conversation_focus")
PLAN_FIELDS = ("action", "filters", "sort", "top_n", "page_size", "group_by", "topic_level",
               "search_query", "relevance_query", "lexical_facets", "pipeline", "scope", "review_statuses", "review_order")


def authorized_statuses(message: str) -> set[str]:
    """A conservative mutation guard, not a query-intent classifier.

    Ambiguous requests can still be answered/clarified. They cannot mutate review
    state. Quoted commands and explicit prohibitions never authorize a write.
    """
    text = re.sub(r'“[^”]*”|「[^」]*」|"[^"]*"|`[^`]*`', "", message).strip()
    statuses = r"已掌握|掌握了|薄弱|未掌握|已复习|复习过|未复习|MASTERED|WEAK|REVIEWED|UNSEEN"
    if re.search(r"(?:不要|别|禁止|不许|无需|不用|不需要|不要真的|do\s+not|don't|never).{0,20}(?:标|记录|保存|更新|修改|改|设|掌握|mark|save|record|update)", text, re.I):
        return set()
    if re.search(r"(?:是否|能否|可以吗|吗[？?]|如何|怎么|怎样|如果|假如|例如|原文|引用|有人说)", text):
        return set()
    command = re.search(r"(?:标记|标为|记为|设为|改为|记录|保存|更新).{0,80}(?:" + statuses + r")", text, re.I)
    english = re.search(r"^(?:please\s+)?(?:mark|set|save|record|update)\b.{0,160}(?:mastered|weak|reviewed|unseen)", text, re.I)
    declaration = re.fullmatch(r"(?:这些|这几题|这三题|这\d+题|当前页(?:的)?(?:这些)?(?:题目|题)?|我(?:已经|已)?).{0,15}(?:" + statuses + r")[。.!！]?", text, re.I)
    if not (command or english or declaration):
        return set()
    choices = {"MASTERED": r"已掌握|掌握了|\bMASTERED\b", "WEAK": r"薄弱|未掌握|\bWEAK\b",
               "REVIEWED": r"已复习|复习过|\bREVIEWED\b", "UNSEEN": r"未复习|\bUNSEEN\b"}
    return {status for status, pattern in choices.items() if re.search(pattern, text, re.I)}


def write_authorized(message: str) -> bool:
    return bool(authorized_statuses(message))


@dataclass(frozen=True)
class ToolPolicy:
    actions: dict[str, list[str]]
    reasons: dict[str, str]
    write_authorized: bool

    @classmethod
    def compile(cls, message, state, *, terminal=False, remaining_tools=8, read_only=False):
        if terminal or remaining_tools <= 0:
            return cls({}, {name: "run_terminal_or_budget" for name in TOOL_ACTIONS}, False)
        actions = {"list_questions": ["LIST", "CLARIFY"], "search_questions": ["SEARCH"],
                   "get_question_stats": ["STATS"], "get_question_count": ["COUNT"],
                   "answer_question": ["ANSWER"]}
        reasons = {}
        page = state.get("current_page_ids") or []
        listing = state.get("list_request") or {}
        if listing.get("cursor"):
            actions["list_questions"].append("NEXT")
        if page:
            actions["get_question_details"] = ["DETAILS"]
        else:
            reasons["get_question_details"] = "displayed_page_required"
        if page or (listing and listing.get("group_by") in {None, "question"}):
            actions["get_review_state"] = ["REVIEW_STATE"]
        else:
            reasons["get_review_state"] = "question_scope_required"
        grant = not read_only and write_authorized(message)
        if grant and page:
            actions["record_review"] = ["RECORD_REVIEW"]
        else:
            reasons["record_review"] = ("historical_requery_is_read_only" if read_only else
                "displayed_page_required" if grant else "explicit_write_request_required")
        return cls(actions, reasons, grant)

    def require(self, name, action):
        if action not in self.actions.get(name, []):
            raise ValueError("QUERY_TOOL_NOT_ALLOWED")

    def model_dump(self):
        return {"version": POLICY_VERSION, "allowed_tools": list(self.actions),
                "allowed_actions": self.actions, "denied_reasons": self.reasons,
                "write_authorized": self.write_authorized}


class ContextCompiler:
    def __init__(self, *, max_bytes=24000, recent_messages=2):
        self.max_bytes, self.recent_messages = max_bytes, recent_messages

    @staticmethod
    def conversation_focus(state):
        """Project the completed user goal independently of the paging scope.

        Old sessions have no focus record. Recover only a scope attributable to
        their latest response; an old count must never override a newer list.
        This is state migration, not natural-language intent classification.
        """
        saved = state.get("conversation_focus")
        if isinstance(saved, dict):
            focus = saved
            provenance = "completed_host_tool"
        else:
            response = state.get("last_response") or {}
            plan = state.get("last_plan") or {}
            intent = response.get("intent") or plan.get("action")
            if intent == "COUNT":
                scope = state.get("last_count") or {}
            elif intent in {"LIST", "STATS", "SEARCH", "NEXT"}:
                scope = plan
            elif intent == "ANSWER":
                # An old answer did not persist its executed filters. Do not
                # mistake the still-retained paging scope for its subject.
                scope = {}
            else:
                return {}
            focus = {**scope, "intent": intent,
                     "message": response.get("message") or scope.get("message", "")}
            if intent == "NEXT":
                focus["intent"] = "STATS" if (state.get("list_request") or {}).get("group_by") else "LIST"
            provenance = "legacy_saved_state"
        projected = {key: value for key, value in focus.items() if key in {
            "intent", "message", "filters", "sort", "top_n", "review_statuses", "review_order",
            "group_by", "topic_level", "run_id", "source_action", "answer_kind", "answer_basis", "question_ids"}}
        projected["message"] = str(projected.get("message") or "")[:800]
        if isinstance(projected.get("filters"), dict):
            projected["filters"] = {key: value for key, value in projected["filters"].items()
                                    if key in FilterSpec.model_fields}
        projected["provenance"] = provenance
        return projected

    @staticmethod
    def state_projection(state):
        projected = {key: state[key] for key in STATE_FIELDS if key in state}
        focus = ContextCompiler.conversation_focus(state)
        if focus:
            projected["conversation_focus"] = focus
        # Signed opaque cursors are consumed by the host, never interpreted by a
        # model. Expose availability while retaining the actual cursor in PG.
        if "list_request" in projected:
            listing = dict(projected["list_request"])
            projected["has_next_page"] = bool(listing.pop("cursor", None))
            projected["list_request"] = listing
        if "last_plan" in projected:
            projected["last_plan"] = {k: v for k, v in projected["last_plan"].items() if k in PLAN_FIELDS}
        if "last_response" in projected:
            response = projected["last_response"]
            projected["last_response"] = {"message": response.get("message", "")[:800],
                "intent": response.get("intent"), "answer": response.get("answer", "")[:1800],
                "provenance": "previous_assistant_response"}
        if "current_page_summary" in projected:
            projected["current_page_summary"] = [{key: (value[:200] if key == "canonical_text" else value)
                for key, value in row.items() if key in {"canonical_question_id", "canonical_text", "occurrence_count"}}
                for row in projected["current_page_summary"][:5]]
        ids = projected.get("current_page_ids", [])
        if not isinstance(ids, list) or len(ids) > 100 or len(set(ids)) != len(ids):
            raise ValueError("QUERY_STATE_INVALID")
        return projected

    def compile(self, *, message, today, explicit_filters, preferences, state,
                default_page_size, requested_pipeline, parts=(), terminal=False, remaining_tools=8,
                query_feedback=(), query_memory=(), requery_origin=None):
        session = self.state_projection(state)
        previous = [m[:800] for m in state.get("recent_messages", [])[-self.recent_messages:]
                    if isinstance(m, str) and m != message]
        session["recent_messages"] = previous
        # Keep recall expansions available without presenting them as evidence
        # of what the user required. This changes only the model projection;
        # the authoritative plan, receipt and PG state remain complete.
        last_plan = session.get("last_plan", {})
        expansion = {key: last_plan.pop(key) for key in ("search_query", "lexical_facets") if key in last_plan}
        user_sources = ["message"]
        if (session.get("pending_clarification") or {}).get("original_message"):
            user_sources.append("session.pending_clarification.original_message")
        if previous:
            user_sources.append("session.recent_messages")
        relevance_intent = {"user_sources": user_sources,
            "previous_target_source": "session.last_plan.relevance_query" if last_plan.get("relevance_query") else None,
            "previous_target_kind": "model_reformulation",
            "correction_sources": [key for key, values in (("query_feedback", query_feedback),
                                                          ("query_memory", query_memory)) if values],
            "allow_inferred_requirements": False}
        policy = ToolPolicy.compile(message, state, terminal=terminal, remaining_tools=remaining_tools,
                                    read_only=bool(requery_origin))
        context = {"message": message, "today": today, "explicit_filters": explicit_filters,
                   "default_page_size": default_page_size, "requested_pipeline": requested_pipeline,
                   "preferences": preferences, "session": session,
                   "relevance_intent": relevance_intent,
                   "retrieval_expansions": {"previous_plan": expansion},
                   "query_feedback": list(query_feedback), "query_memory": list(query_memory),
                   "completed_tools": [{"intent": p["intent"], "answer": p["answer"][:1800],
                       "data": [self.evidence_row(row) for row in p["facts"].get("data", [])[:5]],
                       "meta": self.evidence_meta(p["facts"]["meta"])} for p in parts[-3:]],
                   "tool_policy": policy.model_dump(),
                   "context_contract": {"version": CONTEXT_VERSION,
                       "precedence": ["current_message", "explicit_filters", "session", "preferences", "defaults"],
                       "sources": {"session": "postgresql_query_state", "preferences": "explicit_user_preferences",
                                   "message": "current_user_request", "relevance_intent": "host_evidence_paths",
                                   "retrieval_expansions": "previous_model_recall_expansion",
                                   "conversation_focus": "completed_user_goal_independent_of_pagination"},
                       "recent_message_count": len(previous)}}
        if requery_origin:
            context["requery_origin"] = requery_origin
        encode = lambda: json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        raw = encode()
        # Optional prose and duplicate result excerpts share one budget. Keep
        # identities, filters, numerical facts and the current request intact.
        optional_rows = [session.get("current_page_summary", [])]
        optional_rows.extend(item["data"] for item in context["completed_tools"])
        for rows in optional_rows:
            while len(raw) > self.max_bytes and rows:
                rows.pop()
                context["context_contract"]["evidence_compacted"] = True
                raw = encode()
        if len(raw) > self.max_bytes and session.get("last_response"):
            previous_answer = session["last_response"]
            while len(raw) > self.max_bytes and len(previous_answer["answer"]) > 200:
                previous_answer["answer"] = previous_answer["answer"][:len(previous_answer["answer"]) // 2]
                context["context_contract"]["evidence_compacted"] = True
                raw = encode()
        if len(raw) > self.max_bytes:
            # Never silently truncate the filters, page identities, or commands.
            raise ValueError("QUERY_CONTEXT_TOO_LARGE")
        context["context_contract"].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        return context

    @staticmethod
    def evidence_meta(meta):
        result = {key: value for key, value in meta.items() if key in {
            "route", "corpus_revision", "task_annotation_revision", "user_state_revision",
            "applied_filters", "group_by", "topic_level", "sort", "counts", "sample_counts", "total_groups", "count_scope"}}
        if meta.get("pagination"):
            result["pagination"] = {key: value for key, value in meta["pagination"].items()
                if key in {"total", "result_total", "returned", "offset", "page_size", "top_n"}}
            result["pagination"]["has_next_page"] = bool(meta["pagination"].get("next_cursor"))
        return result

    @staticmethod
    def evidence_row(row):
        """Bound model evidence without truncating trusted identities or totals."""
        result = {key: row[key] for key in ("canonical_question_id", "key", "occurrence_count",
            "interview_count", "source_document_count", "company_count") if key in row}
        if row.get("canonical_text"):
            result["canonical_text"] = row["canonical_text"][:400]
        if row.get("sources"):
            result["sources"] = [{key: (value[:160] if key == "quote" else value)
                for key, value in source.items() if key in {"revision_id", "start_line", "end_line", "quote"}}
                for source in row["sources"][:2]]
        return result
