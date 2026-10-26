"""User response goals and host-owned completion, independent of tool actions.

A goal describes what the current user needs from the response. It never grants
scope, question identities or mutation permission. ``final`` remains a proposal;
only the host completion receipt controls termination and recovery.
"""
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field

from interview_intelligence.contracts import StrictModel


VERSION = "query_goal_completion_v1"
READ_ACTIONS = {"LIST", "NEXT", "COUNT", "STATS", "SEARCH", "DETAILS", "REVIEW_STATE"}
TERMINAL_STATUSES = {"COMPLETE", "CLARIFICATION", "WRITE_COMMITTED"}


class UserGoal(StrictModel):
    objective: str = Field(min_length=1, max_length=800)
    response_mode: Literal["DIRECT_RESULT", "SYNTHESIS"] = Field(default="SYNTHESIS", description=(
        "Choose from the entire user objective before selecting a tool. DIRECT_RESULT is only for one "
        "authoritative result that alone satisfies every requested outcome in one filter scope. "
        "Multiple scopes, a list plus separate totals, comparisons, explanations or analysis require "
        "SYNTHESIS: gather all needed evidence, then submit ANSWER. A COUNT or LIST action does not "
        "by itself imply DIRECT_RESULT."))


def bind_goal(run, proposal: UserGoal | None, *, structured=False):
    """Bind once to this request, including on journal recovery.

    Typed legacy adapters may omit the new field. Modern model entry points
    supply it through their required schema (or a conservative host fallback).
    Neither old conversation state nor subsequent tool plans can replace it.
    """
    if run.user_goal:
        current = {key: run.user_goal[key] for key in ("objective", "response_mode")}
        if proposal is not None and proposal.model_dump() != current:
            raise ValueError("QUERY_GOAL_CONFLICT")
        return run.user_goal
    goal = proposal or UserGoal(objective=run.request.message[:800], response_mode="DIRECT_RESULT")
    run.user_goal = {"version": VERSION, "run_id": run.id,
        "request_sha256": hashlib.sha256(run.request.message.encode("utf-8")).hexdigest(),
        "source": "structured_ui" if structured else "model_plan" if proposal else "legacy_typed_plan",
        **goal.model_dump()}
    return run.user_goal


def completion_for(run, plan):
    """Evaluate a checked tool outcome without claiming semantic correctness."""
    if plan.action == "CLARIFY":
        status, reason = "CLARIFICATION", "waiting_for_user"
    elif plan.action == "RECORD_REVIEW":
        status, reason = "WRITE_COMMITTED", "authorized_write_committed"
    elif plan.action == "ANSWER":
        status, reason = "COMPLETE", "response_submitted_with_checked_evidence"
    elif run.user_goal["response_mode"] == "SYNTHESIS":
        status, reason = "NEEDS_RESPONSE", "tool_evidence_does_not_complete_response_goal"
    elif plan.final:
        status, reason = "COMPLETE", "direct_result_requested"
    else:
        status, reason = "CONTINUE", "additional_evidence_requested"
    return {"version": VERSION, "status": status, "terminal": status in TERMINAL_STATUSES,
        "reason": reason, "tool_action": plan.action, "tool_ordinal": run.tool_calls,
        "proposed_final": plan.final,
        "answer_completed": status == "COMPLETE", "response_mode": run.user_goal["response_mode"]}


def restore_completion(run, result):
    """Prefer persisted host state; old receipts retain their explicit legacy path."""
    meta = result.get("facts", {}).get("meta", {})
    completion = meta.get("completion")
    goal = meta.get("user_goal")
    if completion is None:
        if goal is not None:
            raise ValueError("QUERY_COMPLETION_INVALID")
        run.terminal = result.get("planning", {}).get("spec", {}).get("final", True)
        return
    if (not isinstance(completion, dict) or completion.get("version") != VERSION
            or completion.get("status") not in TERMINAL_STATUSES | {"CONTINUE", "NEEDS_RESPONSE"}
            or type(completion.get("terminal")) is not bool
            or completion["terminal"] != (completion["status"] in TERMINAL_STATUSES)
            or not isinstance(goal, dict) or goal.get("version") != VERSION
            or goal.get("run_id") != run.id
            or goal.get("request_sha256") != hashlib.sha256(run.request.message.encode("utf-8")).hexdigest()):
        raise ValueError("QUERY_COMPLETION_INVALID")
    UserGoal.model_validate({key: goal.get(key) for key in ("objective", "response_mode")})
    run.user_goal, run.completion, run.terminal = dict(goal), dict(completion), completion["terminal"]


def tool_evidence(part):
    """Fresh completed receipts can support scope analysis even with zero rows.

    A failed/unavailable search is not converted into an answerable empty set.
    Full rows and source validation remain in the domain tools.
    """
    facts = part.get("facts")
    if not isinstance(facts, dict) or not isinstance(facts.get("meta"), dict):
        return False
    meta, action = facts["meta"], part.get("intent")
    if meta.get("relevance_status") == "UNAVAILABLE":
        return False
    if action in {"COUNT", "STATS"}:
        return isinstance(meta.get("counts"), dict) or isinstance(facts.get("data"), list)
    if action in {"LIST", "NEXT"} and type(meta.get("pagination", {}).get("result_total")) is int:
        return True
    if action not in {"SEARCH", "NEXT"} or not isinstance(facts.get("data"), list):
        return False
    if meta.get("relevance_status") not in {"VERIFIED", "UNVERIFIED"}:
        return False
    coverage = meta.get("result_completeness")
    if not isinstance(coverage, dict):
        return False
    if coverage.get("scope") in {"retrieval_candidates", "empty_sql_scope", "engineering_candidate_workset"}:
        return (type(coverage.get("corpus_complete")) is bool
            and type(coverage.get("verification_complete")) is bool
            and type(coverage.get("returned_count")) is int and coverage["returned_count"] >= 0)
    from interview_intelligence.agent.evidence_projection import project_workset_progress
    return (project_workset_progress(meta.get("candidate_workset_progress")) is not None
        and type(coverage.get("complete")) is bool
        and type(coverage.get("candidate_universe_complete")) is bool
        and type(coverage.get("business_membership_complete")) is bool)


def referenceable_evidence(part):
    if tool_evidence(part):
        return True
    facts = part.get("facts", {})
    return (part.get("intent") in {"DETAILS", "REVIEW_STATE"}
        and isinstance(facts, dict) and isinstance(facts.get("meta"), dict)
        and facts["meta"].get("route") == "SQL" and isinstance(facts.get("data"), list))


def evidence_by_ordinal(parts):
    """Failed invocations can leave gaps; ordinals are not list indexes."""
    return {part.get("facts", {}).get("meta", {}).get("completion", {}).get("tool_ordinal", index + 1): part
            for index, part in enumerate(parts)}
