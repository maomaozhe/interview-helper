"""Complete-corpus aggregation; retrieval samples never determine frequency."""

from __future__ import annotations

import calendar
import math
from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import String, and_, case, cast, distinct, func, select
from sqlalchemy.orm import Session

from interview_intelligence.contracts import DateBasis, StatsRequest
from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusState, DocumentBuild, Interview,
    QuestionOccurrence, SourceDocument, SourceRevision, UserQuestionState,
)
from interview_intelligence.taxonomy import load_taxonomy


def subtract_calendar_months(value: date, months: int) -> date:
    year = value.year + (value.month - 1 - months) // 12
    month = (value.month - 1 - months) % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _effective_date(basis: DateBasis):
    if basis == DateBasis.INTERVIEW:
        return Interview.interview_date
    if basis == DateBasis.PUBLISH:
        return Interview.publish_date
    return func.coalesce(Interview.interview_date, Interview.publish_date)


def _active_from():
    return (
        QuestionOccurrence.__table__
        .join(Interview, QuestionOccurrence.interview_id == Interview.id)
        .join(DocumentBuild, Interview.build_id == DocumentBuild.id)
        .join(SourceRevision, DocumentBuild.source_revision_id == SourceRevision.id)
        .join(SourceDocument, SourceRevision.source_document_id == SourceDocument.id)
        .join(CanonicalQuestion, QuestionOccurrence.canonical_question_id == CanonicalQuestion.id)
    )


def _conditions(request: StatsRequest, *, include_time: bool = True):
    conditions = [
        SourceDocument.active_build_id == DocumentBuild.id,
        DocumentBuild.decision == "INCLUDED",
        Interview.analytics_eligible.is_(True),
    ]
    if request.company:
        conditions.append(Interview.company_normalized == request.company)
    if request.position:
        if "后端" in request.position or "服务端" in request.position or "后台" in request.position:
            conditions.append(Interview.job_family == "BACKEND")
            if "java" in request.position.casefold():
                conditions.append(cast(Interview.language_tags, String).like("%JAVA%"))
        else:
            conditions.append(Interview.position_normalized == request.position)
    if request.job_family:
        conditions.append(Interview.job_family == request.job_family)
    if request.language:
        conditions.append(cast(Interview.language_tags, String).like(f"%{request.language.upper()}%"))
    if request.topic_l1:
        taxonomy = load_taxonomy()
        if request.topic_l2:
            conditions.append(QuestionOccurrence.topic_id == taxonomy.resolve(request.topic_l1, request.topic_l2))
        else:
            conditions.append(QuestionOccurrence.topic_id.in_(taxonomy.topics[request.topic_l1].values()))
    elif request.topic_l2:
        taxonomy = load_taxonomy()
        ids = [topic_id for children in taxonomy.topics.values()
               for label, topic_id in children.items() if label == request.topic_l2]
        if not ids:
            raise ValueError("unknown topic_l2")
        conditions.append(QuestionOccurrence.topic_id.in_(ids))
    if request.question_type:
        conditions.append(QuestionOccurrence.question_type == request.question_type.value)
    if request.round:
        conditions.append(Interview.round == request.round)
    if include_time:
        effective = _effective_date(request.date_basis)
        if request.start_date:
            conditions.append(effective >= request.start_date)
        if request.end_date:
            conditions.append(effective < request.end_date)
    return conditions


def _group_key(request: StatsRequest):
    if request.group_by == "question":
        return CanonicalQuestion.id
    if request.group_by == "company":
        return func.coalesce(Interview.company_normalized, "UNKNOWN")
    if request.group_by == "round":
        return func.coalesce(Interview.round, "UNKNOWN")
    if request.topic_level == "L2":
        return QuestionOccurrence.topic_id
    taxonomy = load_taxonomy()
    whens = [
        (QuestionOccurrence.topic_id.in_(list(children.values())), l1)
        for l1, children in taxonomy.topics.items()
    ]
    return case(*whens, else_="其他")


def query_question_stats(
    session: Session, request: StatsRequest, *, as_of: date | None = None,
    user_id: str = "local",
) -> dict:
    as_of = as_of or date.today()
    source = _active_from()
    conditions = _conditions(request)
    date_expr = _effective_date(request.date_basis)
    recent_start = subtract_calendar_months(as_of, 3)
    recent_end = as_of + timedelta(days=1)
    group_key = _group_key(request)
    stmt = (
        select(
            group_key.label("group_key"),
            func.count(QuestionOccurrence.id).label("occurrence_count"),
            func.count(distinct(Interview.id)).label("interview_count"),
            func.count(distinct(SourceDocument.id)).label("source_document_count"),
            func.count(distinct(Interview.company_normalized)).label("company_count"),
            func.sum(case((and_(date_expr >= recent_start, date_expr < recent_end), 1), else_=0)).label("recent_count"),
        )
        .select_from(source).where(*conditions).group_by(group_key)
    )
    raw_rows = list(session.execute(stmt))
    time_buckets = defaultdict(Counter)
    if request.time_bucket:
        dated = session.execute(select(group_key, date_expr).select_from(source).where(
            *conditions, date_expr.is_not(None)))
        for key, when in dated:
            if isinstance(when, str):
                when = date.fromisoformat(when)
            bucket = (when.strftime("%Y-%m") if request.time_bucket == "month"
                      else (when - timedelta(days=when.weekday())).isoformat())
            time_buckets[key][bucket] += 1
    total = session.execute(select(
        func.count(QuestionOccurrence.id), func.count(distinct(Interview.id)),
        func.count(distinct(SourceDocument.id)), func.count(distinct(Interview.company_normalized)),
    ).select_from(source).where(*conditions)).one()
    unknown_dates = session.scalar(select(func.count(QuestionOccurrence.id)).select_from(source).where(
        *_conditions(request, include_time=False), date_expr.is_(None),
    )) or 0
    fallback_count = 0
    if request.date_basis == DateBasis.BEST_AVAILABLE:
        fallback_count = session.scalar(select(func.count(QuestionOccurrence.id)).select_from(source).where(
            *conditions, Interview.interview_date.is_(None), Interview.publish_date.is_not(None),
        )) or 0
    highest_frequency = max((row.occurrence_count for row in raw_rows), default=0)
    highest_recent = max((row.recent_count or 0 for row in raw_rows), default=0)
    known_companies = total[3] or 0
    user_states = {}
    if request.group_by == "question" and request.sort == "gap":
        user_states = {
            state.canonical_question_id: state.status
            for state in session.scalars(select(UserQuestionState).where(UserQuestionState.user_id == user_id))
        }
    canonical_texts = {}
    if request.group_by == "question" and raw_rows:
        ids = [row.group_key for row in raw_rows]
        canonical_texts = {
            canonical_id: text
            for canonical_id, text in session.execute(select(
                CanonicalQuestion.id, CanonicalQuestion.canonical_text,
            ).where(CanonicalQuestion.id.in_(ids)))
        }
    data = []
    for row in raw_rows:
        frequency = math.log1p(row.occurrence_count) / math.log1p(highest_frequency) if highest_frequency else 0.0
        coverage = row.company_count / known_companies if known_companies else 0.0
        recent = (row.recent_count or 0) / highest_recent if highest_recent else 0.0
        importance = 0.5 * frequency + 0.3 * coverage + 0.2 * recent
        band = "CORE" if importance >= 0.7 else "COMMON" if importance >= 0.3 else "LONG_TAIL"
        item = {
            "key": row.group_key,
            "occurrence_count": row.occurrence_count,
            "interview_count": row.interview_count,
            "source_document_count": row.source_document_count,
            "company_count": row.company_count,
            "recent_count": row.recent_count or 0,
            "importance_score": importance,
            "importance_band": band,
            "importance_components": {"frequency": frequency, "company_coverage": coverage, "recent_frequency": recent},
        }
        if request.time_bucket:
            item["time_buckets"] = dict(sorted(time_buckets[row.group_key].items()))
        if request.group_by == "question":
            item["canonical_question_id"] = row.group_key
            item["canonical_text"] = canonical_texts[row.group_key]
            if request.sort == "gap":
                status = user_states.get(row.group_key, "UNSEEN")
                weight = {"WEAK": 1.0, "UNSEEN": 0.8, "REVIEWED": 0.4, "MASTERED": 0.0}[status]
                item["user_status"] = status
                item["gap_score"] = importance * weight
        data.append(item)
    sort_key = "occurrence_count" if request.sort == "frequency" else "importance_score" if request.sort == "importance" else "gap_score"
    data.sort(key=lambda item: (-item[sort_key], str(item["key"])))
    state = session.get(CorpusState, 1)
    return {
        "data": data[:request.limit],
        "meta": {
            "corpus_revision": state.current_revision,
            "as_of": as_of.isoformat(),
            "applied_filters": request.model_dump(mode="json", exclude={"group_by", "sort", "limit", "cursor"}),
            "sample_counts": {"occurrences": total[0] or 0, "interviews": total[1] or 0,
                              "source_documents": total[2] or 0, "known_companies": known_companies},
            "date_coverage": {"unknown_date_count": unknown_dates, "publish_fallback_count": fallback_count},
            "scoring_versions": {"importance": "importance_v1", "gap": "gap_v1"},
        },
    }
