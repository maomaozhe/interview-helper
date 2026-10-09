"""Database entities. PostgreSQL is the deployment store; SQLite supports local tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import contextmanager, nullcontext
from threading import RLock
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint, create_engine, event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def new_id() -> str:
    return str(uuid4())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SourceDocument(Base):
    __tablename__ = "source_document"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_identity: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    original_relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    active_build_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("document_build.id", name="fk_source_active_build", use_alter=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class SourceRevision(Base):
    __tablename__ = "source_revision"
    __table_args__ = (UniqueConstraint("source_document_id", "raw_file_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_document_id: Mapped[str] = mapped_column(ForeignKey("source_document.id"), nullable=False, index=True)
    raw_file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_path: Mapped[str] = mapped_column(Text, nullable=False)
    raw_file_path: Mapped[str] = mapped_column(Text, nullable=False)
    decoded_text_hash: Mapped[str | None] = mapped_column(String(64))
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class DocumentBuild(Base):
    __tablename__ = "document_build"
    __table_args__ = (UniqueConstraint("source_revision_id", "processing_fingerprint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_revision_id: Mapped[str] = mapped_column(ForeignKey("source_revision.id"), nullable=False, index=True)
    processing_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    document_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), default="INCLUDED", nullable=False)
    processing_state: Mapped[str] = mapped_column(String(20), default="STAGING", nullable=False)
    publication_state: Mapped[str] = mapped_column(String(20), default="UNPUBLISHED", nullable=False)
    exclusion_reason: Mapped[str | None] = mapped_column(Text)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class Interview(Base):
    __tablename__ = "interview"
    __table_args__ = (UniqueConstraint("build_id", "session_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    build_id: Mapped[str] = mapped_column(ForeignKey("document_build.id"), nullable=False, index=True)
    session_key: Mapped[str] = mapped_column(String(128), nullable=False)
    session_order: Mapped[int] = mapped_column(Integer, nullable=False)
    session_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    company_raw: Mapped[str | None] = mapped_column(Text)
    company_normalized: Mapped[str | None] = mapped_column(String(128), index=True)
    department_raw: Mapped[str | None] = mapped_column(Text)
    position_raw: Mapped[str | None] = mapped_column(Text)
    position_normalized: Mapped[str | None] = mapped_column(String(128))
    job_family: Mapped[str | None] = mapped_column(String(32), index=True)
    language_tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    round_raw: Mapped[str | None] = mapped_column(Text)
    round: Mapped[str | None] = mapped_column(String(32), index=True)
    interview_date: Mapped[date | None] = mapped_column(Date, index=True)
    publish_date: Mapped[date | None] = mapped_column(Date, index=True)
    date_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metadata_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    analytics_eligible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), default="v1", nullable=False)
    taxonomy_version: Mapped[str] = mapped_column(String(64), default="v1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class CanonicalQuestion(Base):
    __tablename__ = "canonical_question"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    canonical_text: Mapped[str] = mapped_column(Text, nullable=False)
    primary_topic_id: Mapped[str] = mapped_column(String(128), nullable=False)
    taxonomy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    question_type: Mapped[str] = mapped_column(String(32), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    redirect_to_id: Mapped[str | None] = mapped_column(ForeignKey("canonical_question.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class EmbeddingCache(Base):
    __tablename__ = "embedding_cache"
    __table_args__ = (UniqueConstraint("text_hash", "embedding_version"), CheckConstraint("dimension > 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(128), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class QuestionOccurrence(Base):
    __tablename__ = "question_occurrence"
    __table_args__ = (UniqueConstraint("interview_id", "question_order"), CheckConstraint("confidence >= 0 AND confidence <= 1"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interview.id"), nullable=False, index=True)
    canonical_question_id: Mapped[str | None] = mapped_column(ForeignKey("canonical_question.id"), index=True)
    raw_question: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_question: Mapped[str] = mapped_column(Text, nullable=False)
    question_order: Mapped[int] = mapped_column(Integer, nullable=False)
    context_before: Mapped[str | None] = mapped_column(Text)
    context_after: Mapped[str | None] = mapped_column(Text)
    source_spans: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    topic_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    taxonomy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    question_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_origin: Mapped[str] = mapped_column(String(16), default="TEXT", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class OccurrenceTaskAnnotation(Base):
    """Orthogonal task labels; canonical identity and legacy taxonomy stay stable."""
    __tablename__ = "occurrence_task_annotation"
    occurrence_id: Mapped[str] = mapped_column(ForeignKey("question_occurrence.id"), primary_key=True)
    response_form: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    coding_focus: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    producer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    classification_status: Mapped[str] = mapped_column(String(16),default="NEEDS_REVIEW",nullable=False,index=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class TaskAnnotationDraft(Base):
    __tablename__="task_annotation_draft"
    occurrence_id: Mapped[str]=mapped_column(ForeignKey("question_occurrence.id"),primary_key=True)
    reviewer_id: Mapped[str]=mapped_column(String(128),nullable=False)
    payload: Mapped[dict[str,Any]]=mapped_column(JSON,nullable=False)
    evidence: Mapped[dict[str,Any]]=mapped_column(JSON,nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now_utc,nullable=False)


class AgentConversation(Base):
    __tablename__ = "agent_conversation"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class AgentTurn(Base):
    __tablename__ = "agent_turn"
    __table_args__ = (UniqueConstraint("user_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("agent_conversation.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    state_before: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    state_after: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentEvent(Base):
    __tablename__ = "agent_event"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_turn.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class ToolInvocation(Base):
    __tablename__ = "agent_tool_invocation"
    __table_args__ = (UniqueConstraint("run_id", "ordinal"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_turn.id"), nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class UserPreference(Base):
    __tablename__ = "user_preference"
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    source_message: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class CanonicalAssignment(Base):
    __tablename__ = "canonical_assignment"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    occurrence_id: Mapped[str] = mapped_column(ForeignKey("question_occurrence.id"), nullable=False, index=True)
    canonical_question_id: Mapped[str] = mapped_column(ForeignKey("canonical_question.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    candidate_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    judge_version: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    valid_from_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_to_revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


Index(
    "uq_current_canonical_assignment", CanonicalAssignment.occurrence_id,
    unique=True,
    sqlite_where=CanonicalAssignment.valid_to_revision.is_(None),
    postgresql_where=CanonicalAssignment.valid_to_revision.is_(None),
)


class QuestionRelation(Base):
    __tablename__ = "question_relation"
    __table_args__ = (
        CheckConstraint("(relation_type = 'OBSERVED_FOLLOWUP' AND source_occurrence_id IS NOT NULL AND target_occurrence_id IS NOT NULL AND source_canonical_id IS NULL AND target_canonical_id IS NULL) OR (relation_type IN ('RELATED','SIMILAR') AND source_occurrence_id IS NULL AND target_occurrence_id IS NULL AND source_canonical_id IS NOT NULL AND target_canonical_id IS NOT NULL)", name="relation_endpoint_type"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_occurrence_id: Mapped[str | None] = mapped_column(ForeignKey("question_occurrence.id"))
    target_occurrence_id: Mapped[str | None] = mapped_column(ForeignKey("question_occurrence.id"))
    source_canonical_id: Mapped[str | None] = mapped_column(ForeignKey("canonical_question.id"))
    target_canonical_id: Mapped[str | None] = mapped_column(ForeignKey("canonical_question.id"))
    source_interview_id: Mapped[str | None] = mapped_column(ForeignKey("interview.id"))
    evidence_spans: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    producer_version: Mapped[str] = mapped_column(String(64), default="v1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class AlgorithmMatch(Base):
    __tablename__ = "algorithm_match"
    __table_args__ = (UniqueConstraint("occurrence_id", "platform", "problem_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    occurrence_id: Mapped[str] = mapped_column(ForeignKey("question_occurrence.id"), nullable=False)
    coding_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(64))
    problem_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(Text)
    match_status: Mapped[str] = mapped_column(String(16), nullable=False)
    match_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    catalog_version: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class UserQuestionState(Base):
    __tablename__ = "user_question_state"
    __table_args__ = (UniqueConstraint("user_id", "canonical_question_id"), CheckConstraint("review_count >= 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_question_id: Mapped[str] = mapped_column(ForeignKey("canonical_question.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="UNSEEN", nullable=False)
    binding_status: Mapped[str] = mapped_column(String(16), default="RESOLVED", nullable=False)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_score: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ReviewEvent(Base):
    __tablename__ = "review_event"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", "item_index"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_question_id: Mapped[str | None] = mapped_column(ForeignKey("canonical_question.id"))
    raw_question: Mapped[str | None] = mapped_column(Text)
    requested_status: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    item_index: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_status: Mapped[str] = mapped_column(String(16), nullable=False)


class IdempotencyReceipt(Base):
    __tablename__ = "idempotency_receipt"
    __table_args__ = (UniqueConstraint("namespace", "actor_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    namespace: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class UserRevision(Base):
    __tablename__ = "user_revision"
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    state_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class CorpusState(Base):
    __tablename__ = "corpus_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    current_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    indexed_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    taxonomy_version: Mapped[str] = mapped_column(String(64), default="v1", nullable=False)
    canonical_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    task_annotation_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class CorpusRevision(Base):
    __tablename__ = "corpus_revision"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    activation_manifest: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    canonical_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    versions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class PipelineRun(Base):
    __tablename__ = "pipeline_run"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    pipeline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    taxonomy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_documents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_questions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_documents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_documents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    excluded_documents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    needs_review_documents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", nullable=False)


class StageArtifact(Base):
    __tablename__ = "stage_artifact"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class PipelineTask(Base):
    __tablename__ = "pipeline_task"
    __table_args__ = (UniqueConstraint("run_id", "task_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("pipeline_run.id"), nullable=False)
    source_document_id: Mapped[str | None] = mapped_column(ForeignKey("source_document.id"))
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("source_revision.id"))
    build_id: Mapped[str | None] = mapped_column(ForeignKey("document_build.id"))
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    task_key: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("stage_artifact.id"))
    state: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class ModelCall(Base):
    __tablename__ = "model_call"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    query_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_turn.id"),index=True)
    token_budget_charge: Mapped[int | None] = mapped_column(Integer)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("pipeline_run.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("pipeline_task.id"))
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    model_revision: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    queue_ms: Mapped[int | None] = mapped_column(Integer)
    interval_ms: Mapped[int | None] = mapped_column(Integer)
    provider_ms: Mapped[int | None] = mapped_column(Integer)
    ttft_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost: Mapped[float | None] = mapped_column(Float)
    usage_source: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class IndexSyncTask(Base):
    __tablename__ = "index_sync_task"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    corpus_revision: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    affected_canonical_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


@dataclass
class Database:
    engine: Any
    session_factory: sessionmaker
    session_lock: Any = field(default_factory=RLock)

    @contextmanager
    def session(self):
        # In-memory SQLite's StaticPool shares one connection across threads.
        # Keep independent query/event sessions from rolling back each other.
        lock=self.session_lock if isinstance(self.engine.pool,StaticPool) else nullcontext()
        with lock, self.session_factory() as session:
            yield session


def create_database(url: str, *, create_tables: bool = True) -> Database:
    if url.startswith("sqlite:///:"):
        pass
    elif url.startswith("sqlite:///"):
        path = Path(url.removeprefix("sqlite:///"))
        path.parent.mkdir(parents=True, exist_ok=True)
    engine_options = ({"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
                      if url in {"sqlite+pysqlite:///:memory:", "sqlite:///:memory:"} else {})
    engine = create_engine(url, future=True, **engine_options)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def sqlite_fk(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    if create_tables:
        # Register ancillary tables before create_all, including callers outside the API.
        from interview_intelligence import access_models  # noqa: F401
        from interview_intelligence import tenant_models  # noqa: F401
        Base.metadata.create_all(engine)
        with sessionmaker(engine)() as session:
            if session.get(CorpusState, 1) is None:
                session.add(CorpusState(id=1))
                session.commit()
    return Database(engine=engine, session_factory=sessionmaker(engine, expire_on_commit=False))
