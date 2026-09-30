from interview_intelligence.contracts import FilterSpec
from interview_intelligence.analytics.detail import get_question_detail, list_occurrences
from test_analytics import seed_corpus


def test_detail_uses_only_matching_occurrences_for_counts_and_sources():
    db, fast_id, _ = seed_corpus()
    with db.session() as session:
        detail = get_question_detail(session, fast_id, FilterSpec(company="字节"))
        refs = list_occurrences(session, FilterSpec(company="字节"), canonical_id=fast_id)
    assert detail["occurrence_count"] == 1
    assert detail["company_distribution"] == {"字节": 1}
    assert detail["round_distribution"] == {"FIRST": 1}
    assert len(refs["data"]) == 1
    assert refs["data"][0]["source_document_id"]
    assert refs["data"][0]["source_api_url"].startswith("/api/sources/")


def test_detail_empty_filtered_scope_is_zero_not_global_count():
    db, fast_id, _ = seed_corpus()
    with db.session() as session:
        detail = get_question_detail(session, fast_id, FilterSpec(company="不存在"))
    assert detail["occurrence_count"] == 0
    assert detail["variants"] == []
