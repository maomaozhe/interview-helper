import importlib

import pytest
from sqlalchemy.exc import IntegrityError


def setup_database():
    models = importlib.import_module("interview_intelligence.domain.models")
    db = models.create_database("sqlite+pysqlite:///:memory:")
    return models, db


def add_base_facts(models, session):
    source = models.SourceDocument(source_identity="xhs:post-1", original_relative_path="one.md", source_type="xiaohongshu")
    session.add(source)
    session.flush()
    revision = models.SourceRevision(source_document_id=source.id, raw_file_hash="a" * 64, snapshot_path="snapshots/a.md", raw_file_path="one.md")
    session.add(revision)
    session.flush()
    build = models.DocumentBuild(source_revision_id=revision.id, processing_fingerprint="extract-v1", document_kind="INTERVIEW_REPORT", processing_state="READY")
    session.add(build)
    session.flush()
    interview = models.Interview(build_id=build.id, session_key="first", session_order=1, session_kind="SINGLE", analytics_eligible=True)
    canonical = models.CanonicalQuestion(canonical_text="Redis 为什么快？", primary_topic_id="redis.performance", taxonomy_version="v1", question_type="PRINCIPLE")
    session.add_all([interview, canonical])
    session.flush()
    return source, revision, build, interview, canonical


def test_question_order_is_unique_per_interview():
    models, db = setup_database()
    with db.session() as session:
        with pytest.raises(IntegrityError):
            with session.begin():
                _, _, _, interview, canonical = add_base_facts(models, session)
                for raw in ("Redis为什么快？", "为什么Redis性能高？"):
                    session.add(models.QuestionOccurrence(
                        interview_id=interview.id, canonical_question_id=canonical.id,
                        raw_question=raw, normalized_question=raw, question_order=1,
                        topic_id="redis.performance", taxonomy_version="v1", question_type="PRINCIPLE",
                        source_spans=[{"quote": raw}], confidence=0.95,
                    ))
                session.flush()


def test_relation_requires_occurrence_endpoints_for_observed_followup():
    models, db = setup_database()
    with db.session() as session:
        with pytest.raises(IntegrityError):
            with session.begin():
                _, _, _, _, canonical = add_base_facts(models, session)
                session.add(models.QuestionRelation(
                    relation_type="OBSERVED_FOLLOWUP", source_canonical_id=canonical.id,
                    target_canonical_id=canonical.id, provenance="SOURCE", confidence=0.9,
                ))
                session.flush()


def test_review_event_idempotency_is_enforced_by_database():
    models, db = setup_database()
    with db.session() as session:
        with pytest.raises(IntegrityError):
            with session.begin():
                _, _, _, _, canonical = add_base_facts(models, session)
                for _ in range(2):
                    session.add(models.ReviewEvent(
                        user_id="local", canonical_question_id=canonical.id,
                        requested_status="WEAK", idempotency_key="request-1", item_index=0,
                        resolution_status="RESOLVED",
                    ))
                session.flush()
