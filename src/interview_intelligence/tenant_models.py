"""Account-scoped credentials, sessions and durable lifetime trial admissions."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from interview_intelligence.domain.models import Base, new_id, now_utc


class TenantAccount(Base):
    __tablename__ = "tenant_account"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    trial_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_key: Mapped[str | None] = mapped_column(Text)
    provider_key_hint: Mapped[str | None] = mapped_column(String(16))
    provider_base_url: Mapped[str | None] = mapped_column(String(500))
    provider_model: Mapped[str | None] = mapped_column(String(160))
    provider_reranker_model: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class TenantSession(Base):
    __tablename__ = "tenant_session"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant_account.id"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TenantAdmission(Base):
    __tablename__ = "tenant_admission"
    __table_args__ = (UniqueConstraint("tenant_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant_account.id"), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
