"""Durable anonymous access controls, telemetry and administrator audit records."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from interview_intelligence.domain.models import Base, new_id, now_utc


class AccessPolicy(Base):
    __tablename__ = "access_policy"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    quota_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quota_limit: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    quota_period: Mapped[str] = mapped_column(String(16), default="DAY", nullable=False)
    quota_scope: Mapped[str] = mapped_column(String(16), default="DEVICE", nullable=False)
    rate_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rate_per_minute: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    cookie_key: Mapped[str] = mapped_column(String(64), nullable=False)


class AccessVisitor(Base):
    __tablename__ = "access_visitor"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ip: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_agent: Mapped[str] = mapped_column(String(512), nullable=False)
    device: Mapped[str] = mapped_column(String(32), nullable=False)
    browser: Mapped[str] = mapped_column(String(32), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False, index=True)
    page_views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chat_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AccessIPBlock(Base):
    __tablename__ = "access_ip_block"
    ip: Mapped[str] = mapped_column(String(64), primary_key=True)
    blocked: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)


class AccessCounter(Base):
    __tablename__ = "access_counter"
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False, index=True)


class AccessAdmission(Base):
    __tablename__ = "access_admission"
    __table_args__ = (UniqueConstraint("visitor_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    visitor_id: Mapped[str] = mapped_column(ForeignKey("access_visitor.id"), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class AccessRequest(Base):
    __tablename__ = "access_request"
    __table_args__ = (Index("ix_access_request_visitor_time", "visitor_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    visitor_id: Mapped[str] = mapped_column(ForeignKey("access_visitor.id"), nullable=False)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    page_view: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    chat: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False, index=True)


class AccessAudit(Base):
    __tablename__ = "access_audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(32), default="admin", nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(160), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
