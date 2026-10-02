"""Atomic publication and current corpus views."""

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from interview_intelligence.domain.models import (
    CanonicalQuestion, CorpusRevision, CorpusState, DocumentBuild, IndexSyncTask,
    Interview, QuestionOccurrence, SourceDocument, SourceRevision,
)


def active_occurrences(session: Session) -> list[QuestionOccurrence]:
    stmt = (
        select(QuestionOccurrence)
        .join(Interview, QuestionOccurrence.interview_id == Interview.id)
        .join(DocumentBuild, Interview.build_id == DocumentBuild.id)
        .join(SourceRevision, DocumentBuild.source_revision_id == SourceRevision.id)
        .join(SourceDocument, SourceRevision.source_document_id == SourceDocument.id)
        .where(SourceDocument.active_build_id == DocumentBuild.id)
        .where(DocumentBuild.decision == "INCLUDED")
        .where(Interview.analytics_eligible.is_(True))
        .order_by(QuestionOccurrence.id)
    )
    return list(session.scalars(stmt))


def manifest_at(session: Session, revision: int) -> dict[str, str]:
    entry = session.get(CorpusRevision, revision)
    if entry is None:
        raise KeyError(f"unknown corpus revision {revision}")
    return dict(entry.activation_manifest)


def refresh_canonical_classifications(session: Session) -> list[str]:
    """Derive display labels from current evidence, without changing question identity."""
    votes = defaultdict(dict)
    rows = session.execute(select(
        QuestionOccurrence.canonical_question_id, QuestionOccurrence.topic_id,
        QuestionOccurrence.question_type, QuestionOccurrence.taxonomy_version,
        func.count(QuestionOccurrence.id),
    ).join(Interview, QuestionOccurrence.interview_id == Interview.id)
     .join(DocumentBuild, Interview.build_id == DocumentBuild.id)
     .join(SourceRevision, DocumentBuild.source_revision_id == SourceRevision.id)
     .join(SourceDocument, SourceRevision.source_document_id == SourceDocument.id)
     .where(SourceDocument.active_build_id == DocumentBuild.id,
            DocumentBuild.decision == "INCLUDED", Interview.analytics_eligible.is_(True),
            QuestionOccurrence.canonical_question_id.is_not(None))
     .group_by(QuestionOccurrence.canonical_question_id, QuestionOccurrence.topic_id,
               QuestionOccurrence.question_type, QuestionOccurrence.taxonomy_version))
    for canonical_id, topic, kind, taxonomy_version, count in rows:
        votes[canonical_id][(topic, kind, taxonomy_version)] = count
    changed = []
    for question in session.scalars(select(CanonicalQuestion).where(CanonicalQuestion.id.in_(votes))):
        counts = votes[question.id]
        highest = max(counts.values())
        winners = sorted(label for label, count in counts.items() if count == highest)
        previous = (question.primary_topic_id, question.question_type, question.taxonomy_version)
        selected = previous if previous in winners else winners[0]
        if selected != previous:
            question.primary_topic_id, question.question_type, question.taxonomy_version = selected
            changed.append(question.id)
    return changed


def publish_build(session: Session, build_id: str) -> int:
    build = session.get(DocumentBuild, build_id)
    if build is None:
        raise KeyError(f"unknown build {build_id}")
    if build.processing_state != "READY":
        raise ValueError("build is not validated")
    revision = session.get(SourceRevision, build.source_revision_id)
    source = session.scalar(select(SourceDocument).where(
        SourceDocument.id == revision.source_document_id).with_for_update()
                            .execution_options(populate_existing=True))
    state = session.scalar(select(CorpusState).where(CorpusState.id == 1).with_for_update()
                           .execution_options(populate_existing=True))
    if source.active_build_id == build_id:
        return state.current_revision
    if source.active_build_id:
        previous = session.get(DocumentBuild, source.active_build_id)
        previous.publication_state = "SUPERSEDED"
    source.active_build_id = build_id
    build.publication_state = "ACTIVE"
    previous_revision = state.current_revision
    next_revision = previous_revision + 1
    state.current_revision = next_revision
    session.flush()
    refresh_canonical_classifications(session)
    manifest = {
        document.source_identity: document.active_build_id
        for document in session.scalars(select(SourceDocument).where(SourceDocument.active_build_id.is_not(None)))
    }
    canonical_snapshot = {
        question.id: {
            "canonical_text": question.canonical_text,
            "primary_topic_id": question.primary_topic_id,
            "question_type": question.question_type,
            "taxonomy_version": question.taxonomy_version,
            "lifecycle": question.lifecycle,
            "redirect_to_id": question.redirect_to_id,
        }
        for question in session.scalars(select(CanonicalQuestion))
    }
    session.add(CorpusRevision(
        id=next_revision, parent_revision=previous_revision,
        activation_manifest=manifest,
        canonical_snapshot=canonical_snapshot,
        versions={"taxonomy_version": state.taxonomy_version,
                  "publication_version": "publication_v2_active_classification"},
    ))
    session.add(IndexSyncTask(corpus_revision=next_revision, affected_canonical_ids=list(canonical_snapshot)))
    return next_revision
