"""Exact corpus counts respect personal review status without duplicating facts."""
import pytest

from interview_intelligence.analytics.counts import count_questions
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import UserQuestionState
from test_analytics import seed_corpus


def reviewed_corpus():
    db, fast_id, persistence_id = seed_corpus()
    with db.session() as session, session.begin():
        session.add_all([
            UserQuestionState(user_id="local", canonical_question_id=fast_id, status="MASTERED"),
            UserQuestionState(user_id="other", canonical_question_id=fast_id, status="WEAK"),
            UserQuestionState(user_id="other", canonical_question_id=persistence_id, status="MASTERED"),
        ])
    return db, fast_id, persistence_id


@pytest.mark.parametrize("user_id,statuses,expected", [
    ("local", (), (2, 3, 3, 3, 2)),
    ("local", ("UNSEEN",), (1, 1, 1, 1, 1)),
    ("local", ("MASTERED",), (1, 2, 2, 2, 2)),
    ("local", ("WEAK",), (0, 0, 0, 0, 0)),
    ("local", ("UNSEEN", "WEAK", "REVIEWED"), (1, 1, 1, 1, 1)),
    ("other", ("MASTERED",), (1, 1, 1, 1, 1)),
    ("other", ("WEAK",), (1, 2, 2, 2, 2)),
    ("other", ("UNSEEN",), (0, 0, 0, 0, 0)),
    ("new-user", ("UNSEEN",), (2, 3, 3, 3, 2)),
    ("new-user", ("MASTERED",), (0, 0, 0, 0, 0)),
])
def test_review_counts_keep_distinct_questions_occurrences_and_actor_scope(user_id, statuses, expected):
    db, _, _ = reviewed_corpus()
    with db.session() as session:
        actual = count_questions(session, FilterSpec(), user_id=user_id, review_statuses=statuses)
    assert actual == dict(zip(
        ("canonical_questions", "occurrences", "interviews", "source_documents", "known_companies"),
        expected,
    ))


@pytest.mark.parametrize("status", ["UNSEEN", "WEAK", "REVIEWED", "MASTERED"])
def test_explicit_review_status_and_absent_status_are_counted_consistently(status):
    db, _, persistence_id = reviewed_corpus()
    with db.session() as session, session.begin():
        session.add(UserQuestionState(user_id="local", canonical_question_id=persistence_id, status=status))
    with db.session() as session:
        result = count_questions(session, FilterSpec(), review_statuses=[status])
    expected = 2 if status == "MASTERED" else 1
    assert result["canonical_questions"] == expected
    assert result["occurrences"] == (3 if status == "MASTERED" else 1)


def test_company_round_and_review_status_apply_to_the_same_active_occurrences():
    db, _, _ = reviewed_corpus()
    filters = FilterSpec(company="字节", round="SECOND")
    with db.session() as session:
        unseen = count_questions(session, filters, review_statuses=["UNSEEN"])
        mastered = count_questions(session, filters, review_statuses=["MASTERED"])
    assert unseen == {
        "canonical_questions": 1, "occurrences": 1, "interviews": 1,
        "source_documents": 1, "known_companies": 1,
    }
    assert all(value == 0 for value in mastered.values())


def test_duplicate_status_filters_do_not_multiply_occurrences():
    db, _, _ = reviewed_corpus()
    with db.session() as session:
        result = count_questions(session, FilterSpec(), review_statuses=["MASTERED", "MASTERED"])
    assert result["canonical_questions"] == 1 and result["occurrences"] == 2
