import importlib
from datetime import date


def seed_corpus():
    models = importlib.import_module("interview_intelligence.domain.models")
    db = models.create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session:
        with session.begin():
            canonical = models.CanonicalQuestion(canonical_text="Redis为什么快？", primary_topic_id="redis.performance", taxonomy_version="v1", question_type="PRINCIPLE")
            other = models.CanonicalQuestion(canonical_text="Redis持久化方式？", primary_topic_id="redis.persistence", taxonomy_version="v1", question_type="KNOWLEDGE")
            session.add_all([canonical, other])
            session.flush()
            for number, (company, round_name, when, question) in enumerate((
                ("字节", "FIRST", date(2026, 8, 1), canonical),
                ("腾讯", "SECOND", date(2026, 8, 2), canonical),
                ("字节", "SECOND", None, other),
            ), 1):
                source = models.SourceDocument(source_identity=f"post:{number}", source_type="file", original_relative_path=f"{number}.md")
                session.add(source)
                session.flush()
                revision = models.SourceRevision(source_document_id=source.id, raw_file_hash=str(number) * 64, snapshot_path=f"{number}.md", raw_file_path=f"{number}.md")
                session.add(revision)
                session.flush()
                build = models.DocumentBuild(source_revision_id=revision.id, processing_fingerprint="v1", document_kind="INTERVIEW_REPORT", processing_state="READY", publication_state="ACTIVE")
                session.add(build)
                session.flush()
                source.active_build_id = build.id
                interview = models.Interview(build_id=build.id, session_key="round", session_order=1, session_kind="SINGLE", analytics_eligible=True,
                                             company_normalized=company, round=round_name, interview_date=when)
                session.add(interview)
                session.flush()
                session.add(models.QuestionOccurrence(
                    interview_id=interview.id, canonical_question_id=question.id,
                    raw_question=question.canonical_text, normalized_question=question.canonical_text,
                    question_order=1, source_spans=[{"quote": question.canonical_text}],
                    topic_id=question.primary_topic_id, taxonomy_version="v1",
                    question_type=question.question_type, confidence=0.9,
                ))
            fast_id, persistence_id = canonical.id, other.id
    return db, fast_id, persistence_id


def test_company_and_round_filters_must_match_same_occurrence():
    analytics = importlib.import_module("interview_intelligence.analytics.stats")
    contracts = importlib.import_module("interview_intelligence.contracts")
    db, fast_id, persistence_id = seed_corpus()
    with db.session() as session:
        result = analytics.query_question_stats(session, contracts.StatsRequest(company="字节", round="SECOND"), as_of=date(2026, 9, 30))
    assert [item["canonical_question_id"] for item in result["data"]] == [persistence_id]
    assert result["data"][0]["occurrence_count"] == 1


def test_unknown_dates_are_excluded_only_when_time_window_is_active():
    analytics = importlib.import_module("interview_intelligence.analytics.stats")
    contracts = importlib.import_module("interview_intelligence.contracts")
    db, _, _ = seed_corpus()
    with db.session() as session:
        all_time = analytics.query_question_stats(session, contracts.StatsRequest(), as_of=date(2026, 9, 30))
        recent = analytics.query_question_stats(session, contracts.StatsRequest(start_date=date(2026, 6, 30), end_date=date(2026, 10, 1)), as_of=date(2026, 9, 30))
    assert sum(row["occurrence_count"] for row in all_time["data"]) == 3
    assert sum(row["occurrence_count"] for row in recent["data"]) == 2
    assert recent["meta"]["date_coverage"]["unknown_date_count"] == 1


def test_question_frequency_is_occurrence_count_not_canonical_count():
    analytics = importlib.import_module("interview_intelligence.analytics.stats")
    contracts = importlib.import_module("interview_intelligence.contracts")
    db, fast_id, _ = seed_corpus()
    with db.session() as session:
        result = analytics.query_question_stats(session, contracts.StatsRequest(topic_l1="Redis"), as_of=date(2026, 9, 30))
    fast = next(row for row in result["data"] if row["canonical_question_id"] == fast_id)
    assert fast["occurrence_count"] == 2
    assert fast["interview_count"] == 2
    assert fast["company_count"] == 2
    assert 0 <= fast["importance_score"] <= 1


def test_month_bucket_only_counts_known_dates():
    analytics = importlib.import_module("interview_intelligence.analytics.stats")
    contracts = importlib.import_module("interview_intelligence.contracts")
    db, fast_id, persistence_id = seed_corpus()
    with db.session() as session:
        result = analytics.query_question_stats(session, contracts.StatsRequest(time_bucket="month"),
                                                as_of=date(2026, 9, 30))
    fast = next(row for row in result["data"] if row["canonical_question_id"] == fast_id)
    persistence = next(row for row in result["data"] if row["canonical_question_id"] == persistence_id)
    assert fast["time_buckets"] == {"2026-08": 2}
    assert persistence["time_buckets"] == {}
