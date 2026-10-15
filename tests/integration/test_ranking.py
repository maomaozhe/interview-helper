"""Published ranking values stay consistent across stats and paginated lists."""
from datetime import date

import pytest
from sqlalchemy import select

from interview_intelligence.analytics.listing import ListRequest, list_questions
from interview_intelligence.analytics.stats import query_question_stats
from interview_intelligence.contracts import ReviewRequest, StatsRequest
from interview_intelligence.domain.models import Interview
from interview_intelligence.review.service import ReviewService
from test_analytics import seed_corpus


AS_OF = date(2026, 9, 30)


def all_pages(session, **filters):
    request = ListRequest(page_size=1, **filters)
    rows = []
    while True:
        result = list_questions(session, request, signing_key="test", as_of=AS_OF)
        rows.extend(result["data"])
        cursor = result["meta"]["pagination"]["next_cursor"]
        if cursor is None:
            return rows
        request = request.model_copy(update={"cursor": cursor})


@pytest.mark.parametrize("status,expected_fast_gap", [
    ("UNSEEN", 0.8), ("WEAK", 1.0), ("REVIEWED", 0.4), ("MASTERED", 0.0),
])
def test_published_scores_and_review_multipliers_survive_pagination(status, expected_fast_gap):
    db, fast_id, other_id = seed_corpus()
    ReviewService(db).record("local", ReviewRequest(idempotency_key="ranking-fixture", items=[
        {"canonical_question_id": fast_id, "status": status}]))
    # Fixture: fast occurs twice in two known companies, both recently; other
    # occurs once in one company, with no known date. These values are an
    # independent oracle, not computed by either production ranking adapter.
    expected_importance = {fast_id: 1.0, other_id: 0.4654648767857287}
    expected_gap = {fast_id: expected_fast_gap, other_id: 0.372371901428583}
    with db.session() as session:
        legacy = query_question_stats(session, StatsRequest(sort="gap"), as_of=AS_OF)
        paginated = all_pages(session, sort="gap")
    expected_order = sorted(expected_gap, key=lambda key: (-expected_gap[key], key))
    for rows in [legacy["data"], paginated]:
        assert [row["key"] for row in rows] == expected_order
        for row in rows:
            assert row["importance_score"] == pytest.approx(expected_importance[row["key"]])
            assert row["gap_score"] == pytest.approx(expected_gap[row["key"]])
            assert row["importance_band"] == ("CORE" if row["key"] == fast_id else "COMMON")
    assert legacy["meta"]["scoring_versions"] == {"importance": "importance_v1", "gap": "gap_v1"}


def test_no_known_companies_or_dates_keeps_scores_finite_and_empty_scope_empty():
    db, fast_id, other_id = seed_corpus()
    with db.session() as session, session.begin():
        for interview in session.scalars(select(Interview)):
            interview.company_normalized = None
            interview.interview_date = interview.publish_date = None
    with db.session() as session:
        legacy = query_question_stats(session, StatsRequest(sort="importance"), as_of=AS_OF)
        paginated = all_pages(session, sort="importance")
        assert query_question_stats(session, StatsRequest(company="missing"), as_of=AS_OF)["data"] == []
        assert all_pages(session, company="missing") == []
    expected = {fast_id: 0.5, other_id: 0.3154648767857287}
    for rows in [legacy["data"], paginated]:
        assert [row["key"] for row in rows] == [fast_id, other_id]
        for row in rows:
            assert row["importance_score"] == pytest.approx(expected[row["key"]])
            assert row["importance_components"]["company_coverage"] == 0
            assert row["importance_components"]["recent_frequency"] == 0
