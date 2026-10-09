"""Persistent access controls and operational telemetry.

Revision ID: d82f9c1a7054
Revises: c21d4857a941
"""
from alembic import op
import sqlalchemy as sa

revision = "d82f9c1a7054"
down_revision = "c21d4857a941"
branch_labels = None
depends_on = None


def tables():
    # Freeze this revision's schema: future ORM changes must not rewrite history.
    metadata = sa.MetaData()
    def column(name, type_, **kwargs):
        return sa.Column(name, type_, nullable=False, **kwargs)
    policy = sa.Table("access_policy", metadata,
        column("id", sa.Integer(), primary_key=True), column("version", sa.Integer()),
        column("quota_enabled", sa.Boolean()), column("quota_limit", sa.Integer()),
        column("quota_period", sa.String(16)), column("quota_scope", sa.String(16)),
        column("rate_enabled", sa.Boolean()), column("rate_per_minute", sa.Integer()),
        column("cookie_key", sa.String(64)))
    visitor = sa.Table("access_visitor", metadata,
        column("id", sa.String(36), primary_key=True), column("ip", sa.String(64), index=True),
        column("user_agent", sa.String(512)), column("device", sa.String(32)), column("browser", sa.String(32)),
        column("first_seen", sa.DateTime(timezone=True)), column("last_seen", sa.DateTime(timezone=True), index=True),
        column("page_views", sa.Integer()), column("requests", sa.Integer()), column("chat_requests", sa.Integer()),
        column("blocked", sa.Boolean()))
    bans = sa.Table("access_ip_block", metadata,
        column("ip", sa.String(64), primary_key=True), column("blocked", sa.Boolean()), column("reason", sa.String(500)))
    counter = sa.Table("access_counter", metadata, column("key", sa.String(160), primary_key=True),
        column("used", sa.Integer()), column("updated_at", sa.DateTime(timezone=True), index=True))
    admission = sa.Table("access_admission", metadata, column("id", sa.String(36), primary_key=True),
        sa.Column("visitor_id", sa.String(36), sa.ForeignKey("access_visitor.id"), nullable=False),
        column("request_id", sa.String(128)), column("payload_hash", sa.String(64)),
        column("created_at", sa.DateTime(timezone=True)), sa.UniqueConstraint("visitor_id", "request_id"))
    request = sa.Table("access_request", metadata, column("id", sa.String(36), primary_key=True),
        sa.Column("visitor_id", sa.String(36), sa.ForeignKey("access_visitor.id"), nullable=False),
        column("ip", sa.String(64)), column("path", sa.String(200)), column("status", sa.Integer()),
        column("duration_ms", sa.Float()), column("page_view", sa.Boolean()), column("chat", sa.Boolean()),
        column("blocked", sa.Boolean()), column("created_at", sa.DateTime(timezone=True), index=True),
        sa.Index("ix_access_request_visitor_time", "visitor_id", "created_at"))
    audit = sa.Table("access_audit", metadata, column("id", sa.String(36), primary_key=True),
        column("created_at", sa.DateTime(timezone=True), index=True), column("actor", sa.String(32)),
        column("action", sa.String(64)), column("target", sa.String(160)), column("details", sa.JSON()))
    return (policy, visitor, bans, counter, admission, request, audit)


def upgrade():
    for table in tables():
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    for table in reversed(tables()):
        table.drop(op.get_bind(), checkfirst=True)
