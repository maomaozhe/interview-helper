"""Exact counts in the active corpus, independent of displayed pages."""
from sqlalchemy import and_, distinct, func, select

from interview_intelligence.analytics.stats import _active_from, _conditions
from interview_intelligence.domain.models import (
    CanonicalQuestion, Interview, QuestionOccurrence, SourceDocument, UserQuestionState,
)


def count_questions(session, filters, *, user_id="local", review_statuses=()):
    source = _active_from().outerjoin(UserQuestionState, and_(
        UserQuestionState.canonical_question_id == CanonicalQuestion.id,
        UserQuestionState.user_id == user_id,
    ))
    conditions = _conditions(filters)
    if review_statuses:
        conditions.append(func.coalesce(UserQuestionState.status, "UNSEEN").in_(review_statuses))
    row = session.execute(select(
        func.count(distinct(CanonicalQuestion.id)),
        func.count(QuestionOccurrence.id),
        func.count(distinct(Interview.id)),
        func.count(distinct(SourceDocument.id)),
        func.count(distinct(Interview.company_normalized)),
    ).select_from(source).where(*conditions)).one()
    return dict(zip(("canonical_questions", "occurrences", "interviews", "source_documents", "known_companies"), row))
