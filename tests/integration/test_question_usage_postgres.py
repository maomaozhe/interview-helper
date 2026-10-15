"""Opt-in real PostgreSQL migration/aggregation test in an isolated schema."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from interview_intelligence.analytics.usage import UsageRequest, question_usage, record_usage
from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusState, DocumentBuild, Interview, QuestionOccurrence,
    SourceDocument, SourceRevision, create_database,
)


@pytest.mark.skipif(not os.environ.get("USAGE_TEST_DATABASE_URL"), reason="explicit PostgreSQL test connection required")
def test_postgres_usage_migration_and_concurrent_aggregation():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    url = make_url(os.environ["USAGE_TEST_DATABASE_URL"])
    assert url.get_backend_name() == "postgresql"
    schema = "usage_acceptance_" + uuid4().hex
    control = create_engine(url)
    database = None
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        isolated = url.update_query_dict({"options": f"-csearch_path={schema}"})
        database = create_database(isolated.render_as_string(hide_password=False))
        with database.session() as session, session.begin():
            question = CanonicalQuestion(canonical_text="并发使用统计测试题", primary_topic_id="redis.performance",
                taxonomy_version="v1", question_type="KNOWLEDGE")
            document = SourceDocument(source_identity="usage-fixture", source_type="file", original_relative_path="fixture.md")
            session.add_all([question, document]); session.flush()
            revision = SourceRevision(source_document_id=document.id, raw_file_hash="0" * 64,
                snapshot_path="fixture.md", raw_file_path="fixture.md")
            session.add(revision); session.flush()
            build = DocumentBuild(source_revision_id=revision.id, processing_fingerprint="fixture", document_kind="INTERVIEW_REPORT")
            session.add(build); session.flush()
            document.active_build_id = build.id
            interview = Interview(build_id=build.id, session_key="fixture", session_order=1,
                session_kind="SINGLE", analytics_eligible=True)
            session.add(interview); session.flush()
            session.add(QuestionOccurrence(interview_id=interview.id, canonical_question_id=question.id,
                raw_question=question.canonical_text, normalized_question=question.canonical_text, question_order=1,
                source_spans=[], topic_id=question.primary_topic_id, taxonomy_version="v1", question_type="KNOWLEDGE", confidence=1))
            session.get(CorpusState, 1).current_revision = 1
            identity = question.id
        migration = importlib.import_module("migrations.versions.f047ac821369_question_usage")
        with database.engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.downgrade(); migration.upgrade(); migration.upgrade()
            assert connection.scalar(select(CorpusState.current_revision)) == 1
        part = {"intent": "SEARCH", "facts": {"data": [{"canonical_question_id": identity}],
            "meta": {"candidate_ids": [identity], "candidate_count": 1}}}

        def complete(index):
            with database.session() as session, session.begin():
                record_usage(session, user_id="local", request_key=f"pg-{index}", parts=[part])
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(complete, range(24)))
        # Replaying each committed observation does not add another event.
        for index in range(24):
            complete(index)
        with database.session() as session:
            result = question_usage(session, UsageRequest())
            assert len(result["data"]) == 1
            assert result["data"][0]["candidate_count"] == result["data"][0]["returned_count"] == 24
            assert result["data"][0]["accepted_count"] == 0
            assert result["meta"]["observation"]["completed_runs"] == 24
            assert question_usage(session, UsageRequest(), user_id="other")["data"][0]["returned_count"] == 0
    finally:
        if database:
            database.engine.dispose()
        with control.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()
