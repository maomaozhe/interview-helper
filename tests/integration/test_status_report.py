from interview_intelligence.reports.status import corpus_status
from test_analytics import seed_corpus


def test_status_uses_active_occurrences_for_dedup_ratio():
    db, _, _ = seed_corpus()
    report = corpus_status(db)
    assert report["active_occurrences"] == 3
    assert report["active_canonical_questions"] == 2
    assert report["dedup_ratio"] == 1 - 2 / 3
