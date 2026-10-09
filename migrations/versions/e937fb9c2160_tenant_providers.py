"""Authenticated tenants and encrypted provider trials.

Revision ID: e937fb9c2160
Revises: d82f9c1a7054
"""
from alembic import op
import sqlalchemy as sa

revision = "e937fb9c2160"
down_revision = "d82f9c1a7054"
branch_labels = None
depends_on = None


def tables():
    metadata = sa.MetaData()
    account = sa.Table("tenant_account", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("trial_used", sa.Integer(), nullable=False),
        sa.Column("provider_version", sa.Integer(), nullable=False),
        sa.Column("provider_key", sa.Text()), sa.Column("provider_key_hint", sa.String(16)),
        sa.Column("provider_base_url", sa.String(500)), sa.Column("provider_model", sa.String(160)),
        sa.Column("provider_reranker_model", sa.String(160)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    session = sa.Table("tenant_session", metadata,
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant_account.id"), nullable=False, index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    admission = sa.Table("tenant_admission", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenant_account.id"), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("provider_snapshot", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "request_id"))
    return account, session, admission


def upgrade():
    for table in tables():
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    for table in reversed(tables()):
        table.drop(op.get_bind(), checkfirst=True)
