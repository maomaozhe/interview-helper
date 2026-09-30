"""Atomic publication and current corpus views."""

from sqlalchemy import select
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
    manifest = {
        document.source_identity: document.active_build_id
        for document in session.scalars(select(SourceDocument).where(SourceDocument.active_build_id.is_not(None)))
    }
    canonical_snapshot = {
        question.id: {
            "canonical_text": question.canonical_text,
            "lifecycle": question.lifecycle,
            "redirect_to_id": question.redirect_to_id,
        }
        for question in session.scalars(select(CanonicalQuestion))
    }
    session.add(CorpusRevision(
        id=next_revision, parent_revision=previous_revision,
        activation_manifest=manifest,
        canonical_snapshot=canonical_snapshot,
        versions={"taxonomy_version": state.taxonomy_version},
    ))
    session.add(IndexSyncTask(corpus_revision=next_revision, affected_canonical_ids=list(canonical_snapshot)))
    return next_revision
