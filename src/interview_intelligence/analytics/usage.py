"""Observed retrieval use, separate from corpus frequency and explicit review."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import Field
from sqlalchemy import and_, case, distinct, func, or_, select

from interview_intelligence.analytics.listing import filter_fields
from interview_intelligence.analytics.stats import _active_from, _conditions
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusState, QuestionOccurrence, QuestionUsageEvent,
    QuestionUsageRun, UsageTrackingState, UserQuestionState, now_utc,
)


class UsageRequest(FilterSpec):
    usage_state: Literal["ALL", "NOT_CANDIDATE", "NOT_RETURNED", "RETURNED_UNREVIEWED", "REVIEW_DUE"] = "ALL"
    usage_sort: Literal["least_returned", "most_returned", "least_candidate", "frequency", "review_due"] = "least_returned"
    usage_days: int | None = Field(default=None, ge=1, le=3650)
    review_after_days: int = Field(default=7, ge=1, le=3650)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _json_dates(row):
    return {key: _aware(value).isoformat() if isinstance(value, datetime) else value for key, value in row.items()}


def record_usage(session, *, user_id, request_key, parts, source="QUERY_AGENT", purpose="practice", occurred_at=None):
    """Called in the completion transaction. One event per run/question, including multi-tool runs."""
    if purpose not in {"practice", "evaluation"}:
        raise ValueError("INVALID_USAGE_PURPOSE")
    existing = session.scalar(select(QuestionUsageRun).where(
        QuestionUsageRun.user_id == user_id, QuestionUsageRun.request_key == request_key))
    if existing:
        return existing.id
    observations, snapshots, complete = {}, [], []

    def observe(identity, **flags):
        if not isinstance(identity, str) or not identity:
            return
        item = observations.setdefault(identity, {"candidate": False, "accepted": False,
            "returned": False, "listed": False, "decisions": set()})
        for key, value in flags.items():
            if key == "decision":
                if isinstance(value, str):
                    item["decisions"].add(value)
            else:
                item[key] = item[key] or value

    for part in parts:
        action, facts = part.get("intent"), part.get("facts") or {}
        meta, data = facts.get("meta") or {}, facts.get("data") or []
        if action == "SEARCH":
            audit = meta.get("rerank_audit") or {}
            candidates = audit.get("candidates") or []
            ids = meta.get("candidate_ids")
            if isinstance(ids, list) and all(isinstance(identity, str) for identity in ids):
                observed = len(set(ids)) == meta.get("candidate_count", len(set(ids)))
                for identity in ids:
                    observe(identity, candidate=True)
            elif candidates:
                ids = [item.get("canonical_question_id") for item in candidates]
                observed = (all(isinstance(identity, str) for identity in ids)
                    and len(set(ids)) == meta.get("candidate_count", len(set(ids))))
            else:
                observed = meta.get("eligible_count") == 0
            complete.append(observed)
            for item in candidates:
                decision = item.get("decision")
                accepted = (meta.get("relevance_status") == "VERIFIED"
                    and decision in {"ACCEPTED", "RETURNED", "PAGE_CUTOFF"}
                    and type(item.get("relevance_grade")) is int and item["relevance_grade"] >= 2
                    and item.get("quote_grounded") is True)
                observe(item.get("canonical_question_id"), candidate=True, accepted=accepted, decision=decision)
            for item in data:
                observe(item.get("canonical_question_id"), candidate=True, returned=True,
                    accepted=meta.get("relevance_status") == "VERIFIED", decision="RETURNED")
        elif action in {"LIST", "STATS", "NEXT"} and meta.get("group_by", "question") == "question":
            for item in data:
                observe(item.get("canonical_question_id"), listed=True)
        else:
            continue
        snapshots.append({"action": action, **{key: meta[key] for key in (
            "corpus_revision", "task_annotation_revision", "index_schema_version", "embedding_version",
            "pipeline", "requested_pipeline", "reranker_version", "candidate_count", "relevance_status",
            "candidate_verification_status", "invalid_candidate_count") if key in meta}})
    if not snapshots:
        return None
    # Old/fake retrievers may lack a full candidate audit. Only known facts count.
    valid = set(session.scalars(select(CanonicalQuestion.id).where(CanonicalQuestion.id.in_(observations))))
    if set(observations) - valid and complete:
        complete.append(False)
    run = QuestionUsageRun(user_id=user_id, request_key=request_key, source=source, purpose=purpose,
        search_performed=bool(complete), candidate_complete=bool(complete) and all(complete),
        metadata_snapshot={"version": "question_usage_v1", "tools": snapshots}, occurred_at=occurred_at or now_utc())
    session.add(run)
    session.flush()
    for identity in valid:
        item = observations[identity]
        session.add(QuestionUsageEvent(usage_run_id=run.id, canonical_question_id=identity,
            **{key: item[key] for key in ("candidate", "accepted", "returned", "listed")},
            decisions=sorted(item["decisions"])))
    return run.id


def _run_conditions(user_id, start, end):
    return (QuestionUsageRun.user_id == user_id, QuestionUsageRun.purpose == "practice",
            QuestionUsageRun.occurred_at >= start, QuestionUsageRun.occurred_at <= end)


def usage_aggregate(user_id, start, end):
    # Keep old observations immutable after a canonical merge. Count each run
    # once even when it contains both the redirected ID and its active target.
    identity = case((and_(CanonicalQuestion.lifecycle == "REDIRECT", CanonicalQuestion.redirect_to_id.is_not(None)),
                     CanonicalQuestion.redirect_to_id), else_=QuestionUsageEvent.canonical_question_id)
    columns = [identity.label("canonical_question_id")]
    for stage in ("candidate", "accepted", "returned", "listed"):
        flag = getattr(QuestionUsageEvent, stage)
        columns.extend((func.count(distinct(case((flag.is_(True), QuestionUsageRun.id)))).label(stage + "_count"),
                        func.max(case((flag.is_(True), QuestionUsageRun.occurred_at))).label("last_" + stage + "_at")))
    return select(*columns).select_from(QuestionUsageEvent).join(QuestionUsageRun).join(CanonicalQuestion,
        CanonicalQuestion.id == QuestionUsageEvent.canonical_question_id).where(
        *_run_conditions(user_id, start, end)).group_by(identity).subquery()


def tracking_window(session, usage_days=None, *, now=None):
    now = _aware(now or now_utc())
    state = session.get(UsageTrackingState, 1)
    if not state:
        raise ValueError("USAGE_TRACKING_NOT_READY")
    started = _aware(state.started_at)
    start = max(started, now - timedelta(days=usage_days)) if usage_days else started
    return started, start, now


def question_usage_states(session, canonical_ids, *, user_id="local", now=None):
    started, start, end = tracking_window(session, now=now)
    aggregate = usage_aggregate(user_id, start, end)
    rows = {row["canonical_question_id"]: _json_dates(row) for row in session.execute(select(aggregate).where(
        aggregate.c.canonical_question_id.in_(canonical_ids))).mappings()}
    return {identity: {**{stage + "_count": 0 for stage in ("candidate", "accepted", "returned", "listed")},
        **{"last_" + stage + "_at": None for stage in ("candidate", "accepted", "returned", "listed")},
        **rows.get(identity, {}), "tracking_started_at": started.isoformat()} for identity in canonical_ids}


def question_usage(session, request: UsageRequest, *, user_id="local", now=None):
    started, start, end = tracking_window(session, request.usage_days, now=now)
    filters = filter_fields(request)
    active = select(QuestionOccurrence.canonical_question_id.label("canonical_question_id"),
        func.count().label("occurrence_count")).select_from(_active_from()).where(
        *_conditions(filters), CanonicalQuestion.lifecycle == "ACTIVE").group_by(
            QuestionOccurrence.canonical_question_id).subquery()
    aggregate = usage_aggregate(user_id, start, end)
    base = select(CanonicalQuestion.id.label("canonical_question_id"), CanonicalQuestion.canonical_text,
        CanonicalQuestion.primary_topic_id.label("topic_id"), CanonicalQuestion.question_type,
        active.c.occurrence_count, func.coalesce(UserQuestionState.status, "UNSEEN").label("user_status"),
        func.coalesce(UserQuestionState.review_count, 0).label("review_count"),
        UserQuestionState.last_reviewed_at).join(active, active.c.canonical_question_id == CanonicalQuestion.id
        ).outerjoin(aggregate, aggregate.c.canonical_question_id == CanonicalQuestion.id).outerjoin(UserQuestionState,
        and_(UserQuestionState.canonical_question_id == CanonicalQuestion.id, UserQuestionState.user_id == user_id))
    for stage in ("candidate", "accepted", "returned", "listed"):
        base = base.add_columns(func.coalesce(getattr(aggregate.c, stage + "_count"), 0).label(stage + "_count"),
            getattr(aggregate.c, "last_" + stage + "_at"))
    b = base.subquery()
    due = or_(b.c.user_status.in_(["UNSEEN", "WEAK"]), and_(b.c.user_status == "REVIEWED",
        or_(b.c.last_reviewed_at.is_(None), b.c.last_reviewed_at <= end - timedelta(days=request.review_after_days))))
    states = {"NOT_CANDIDATE": b.c.candidate_count == 0, "NOT_RETURNED": b.c.returned_count == 0,
        "RETURNED_UNREVIEWED": and_(b.c.returned_count > 0, b.c.review_count == 0), "REVIEW_DUE": due}
    summary_row = session.execute(select(func.count().label("active_questions"),
        func.coalesce(func.sum(case((b.c.candidate_count > 0, 1), else_=0)), 0).label("candidate_questions"),
        func.coalesce(func.sum(case((b.c.returned_count > 0, 1), else_=0)), 0).label("returned_questions"),
        func.coalesce(func.sum(case((b.c.review_count > 0, 1), else_=0)), 0).label("reviewed_questions"),
        func.coalesce(func.sum(case((states["RETURNED_UNREVIEWED"], 1), else_=0)), 0).label("returned_unreviewed"),
        func.coalesce(func.sum(case((due, 1), else_=0)), 0).label("review_due_questions"))).mappings().one()
    summary = dict(summary_row)
    summary.update(not_candidate_questions=summary["active_questions"] - summary["candidate_questions"],
                   not_returned_questions=summary["active_questions"] - summary["returned_questions"])
    selection = select(b)
    if request.usage_state != "ALL":
        selection = selection.where(states[request.usage_state])
    total = session.scalar(select(func.count()).select_from(selection.subquery())) or 0
    orders = {
        "least_returned": (b.c.returned_count, b.c.occurrence_count.desc()),
        "most_returned": (b.c.returned_count.desc(), b.c.occurrence_count.desc()),
        "least_candidate": (b.c.candidate_count, b.c.occurrence_count.desc()),
        "frequency": (b.c.occurrence_count.desc(),),
        "review_due": (case((b.c.user_status == "WEAK", 0), (b.c.user_status == "UNSEEN", 1), else_=2),
                       b.c.last_reviewed_at.asc().nullsfirst(), b.c.occurrence_count.desc()),
    }
    rows = [_json_dates(row) for row in session.execute(selection.order_by(*orders[request.usage_sort], b.c.canonical_question_id
        ).offset(request.offset).limit(request.limit)).mappings()]
    for row in rows:
        row["usage_hint"] = ("NO_CANDIDATE_OBSERVED" if row["candidate_count"] == 0 else
            "CANDIDATE_NOT_RETURNED" if row["returned_count"] == 0 else
            "RETURNED_UNREVIEWED" if row["review_count"] == 0 else "IN_USE")
    observation = dict(session.execute(select(func.count().label("completed_runs"),
        func.coalesce(func.sum(case((QuestionUsageRun.search_performed.is_(True), 1), else_=0)), 0).label("search_runs"),
        func.coalesce(func.sum(case((and_(QuestionUsageRun.search_performed.is_(True),
            QuestionUsageRun.candidate_complete.is_(True)), 1), else_=0)), 0).label("complete_candidate_runs")
        ).where(*_run_conditions(user_id, start, end))).mappings().one())
    corpus = session.get(CorpusState, 1)
    return {"data": rows, "meta": {"route": "SQL_USAGE", "version": "question_usage_v1", "summary": summary,
        "tracking_started_at": started.isoformat(), "window_start": start.isoformat(), "window_end": end.isoformat(),
        "usage_days": request.usage_days, "review_after_days": request.review_after_days, "observation": observation,
        "usage_state": request.usage_state, "usage_sort": request.usage_sort, "purpose": "practice",
        "applied_filters": filters.model_dump(mode="json"),
        "corpus_revision": corpus.current_revision, "indexed_revision": corpus.indexed_revision,
        "index_ready": corpus.current_revision > 0 and corpus.indexed_revision == corpus.current_revision,
        "pagination": {"total": total, "offset": request.offset, "returned": len(rows), "limit": request.limit,
            "next_offset": request.offset + len(rows) if request.offset + len(rows) < total else None}},
        "warnings": ["零次数仅表示统计期内未观察到；请结合查询主题和筛选范围排查。"] + (
            ["部分检索器没有完整候选记录，未入候选清单只能用于排查，不能断言从未召回。"]
            if observation["complete_candidate_runs"] < observation["search_runs"] else [])}
