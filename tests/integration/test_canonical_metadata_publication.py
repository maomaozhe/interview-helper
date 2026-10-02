"""Corrected source classifications must also appear on the stable standard question."""
import hashlib

import pytest

from interview_intelligence.domain import models
from interview_intelligence.repository.corpus import publish_build


def staged_build(session, canonical, identity, labels, *, source=None, decision="INCLUDED",
                 eligible=True):
    if source is None:
        source = models.SourceDocument(source_identity=identity, original_relative_path=f"{identity}.md",
                                       source_type="file")
        session.add(source)
        session.flush()
    revision = models.SourceRevision(source_document_id=source.id,
        raw_file_hash=hashlib.sha256(str(labels).encode()).hexdigest(),
        snapshot_path=f"{identity}.md", raw_file_path=f"{identity}.md")
    session.add(revision)
    session.flush()
    build = models.DocumentBuild(source_revision_id=revision.id, processing_fingerprint=identity,
        document_kind="INTERVIEW_REPORT" if decision == "INCLUDED" else "COMPILATION",
        decision=decision, processing_state="READY")
    session.add(build)
    session.flush()
    interview = models.Interview(build_id=build.id, session_key="first", session_order=1,
                                session_kind="SINGLE", analytics_eligible=eligible)
    session.add(interview)
    session.flush()
    for order, (topic, kind) in enumerate(labels, 1):
        session.add(models.QuestionOccurrence(interview_id=interview.id,
            canonical_question_id=canonical.id, raw_question="原始问法", normalized_question="标准问法",
            question_order=order, source_spans=[{"quote": "原始问法"}],
            topic_id=topic, taxonomy_version="v1", question_type=kind, confidence=.95))
    session.flush()
    return source, build


def canonical(session, topic="ai.system_design", kind="AI"):
    question = models.CanonicalQuestion(canonical_text="AI 代码上线出错，如何定位？",
        primary_topic_id=topic, taxonomy_version="v1", question_type=kind)
    session.add(question)
    session.flush()
    return question


def test_publishing_corrected_task_updates_display_and_snapshot_without_changing_identity():
    db = models.create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session, session.begin():
        question = canonical(session)
        question_id, text = question.id, question.canonical_text
        _, build = staged_build(session, question, "bug", [("system_design.incident", "SCENARIO")])
        revision = publish_build(session, build.id)
        assert (question.primary_topic_id, question.question_type) == ("system_design.incident", "SCENARIO")
        assert (question.id, question.canonical_text) == (question_id, text)
        snapshot = session.get(models.CorpusRevision, revision).canonical_snapshot[question.id]
        assert snapshot["primary_topic_id"] == "system_design.incident"
        assert snapshot["question_type"] == "SCENARIO"


def test_replaced_source_votes_do_not_outweigh_the_corrected_current_build():
    db = models.create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session, session.begin():
        question = canonical(session)
        source, old = staged_build(session, question, "old", [("ai.system_design", "AI")] * 4)
        publish_build(session, old.id)
        _, corrected = staged_build(session, question, "corrected",
            [("ai.system_design", "SYSTEM_DESIGN")], source=source)
        publish_build(session, corrected.id)
        assert question.question_type == "SYSTEM_DESIGN"
        assert old.publication_state == "SUPERSEDED"


def test_primary_display_uses_an_observed_joint_classification_and_keeps_ties_stable():
    db = models.create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session, session.begin():
        question = canonical(session, "other.unknown", "OTHER")
        _, a = staged_build(session, question, "a", [("ai.rag", "AI")] * 2)
        publish_build(session, a.id)
        _, b = staged_build(session, question, "b", [("project.deep_dive", "PROJECT")])
        publish_build(session, b.id)
        _, c = staged_build(session, question, "c", [("ai.rag", "PROJECT")])
        publish_build(session, c.id)
        assert (question.primary_topic_id, question.question_type) == ("ai.rag", "AI")
        _, d = staged_build(session, question, "d", [("project.deep_dive", "PROJECT")])
        publish_build(session, d.id)
        assert (question.primary_topic_id, question.question_type) == ("ai.rag", "AI")


def test_unpublished_excluded_and_ineligible_rows_cannot_change_primary_display():
    db = models.create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session, session.begin():
        question = canonical(session)
        _, valid = staged_build(session, question, "valid", [("ai.system_design", "SYSTEM_DESIGN")])
        staged_build(session, question, "unpublished", [("ai.system_design", "AI")] * 5)
        _, excluded = staged_build(session, question, "excluded",
            [("ai.system_design", "AI")] * 5, decision="EXCLUDED")
        publish_build(session, excluded.id)
        _, ineligible = staged_build(session, question, "ineligible",
            [("ai.system_design", "AI")] * 5, eligible=False)
        publish_build(session, ineligible.id)
        publish_build(session, valid.id)
        assert question.question_type == "SYSTEM_DESIGN"


def test_failed_publication_rolls_back_display_labels_and_source_activation():
    db = models.create_database("sqlite+pysqlite:///:memory:")
    with db.session() as session:
        with session.begin():
            question = canonical(session)
            source, old = staged_build(session, question, "old", [("ai.system_design", "AI")])
            publish_build(session, old.id)
            _, corrected = staged_build(session, question, "corrected",
                [("ai.system_design", "SYSTEM_DESIGN")], source=source)
            source_id, old_id, corrected_id, question_id = source.id, old.id, corrected.id, question.id
        with pytest.raises(RuntimeError, match="publication failed"), session.begin():
            publish_build(session, corrected_id)
            assert session.get(models.CanonicalQuestion, question_id).question_type == "SYSTEM_DESIGN"
            raise RuntimeError("publication failed")
        assert session.get(models.CanonicalQuestion, question_id).question_type == "AI"
        assert session.get(models.SourceDocument, source_id).active_build_id == old_id
        assert session.get(models.CorpusState, 1).current_revision == 1
