"""Durable question usage observations, independent of vectors and reviews.

Revision ID: f047ac821369
Revises: e937fb9c2160
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "f047ac821369"
down_revision = "e937fb9c2160"
branch_labels = None
depends_on = None


def tables():
    metadata = sa.MetaData()
    sa.Table("canonical_question", metadata, sa.Column("id", sa.String(36), primary_key=True))
    state = sa.Table("usage_tracking_state", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.String(32), nullable=False))
    run = sa.Table("question_usage_run", metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("request_key", sa.String(160), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("search_performed", sa.Boolean(), nullable=False),
        sa.Column("candidate_complete", sa.Boolean(), nullable=False),
        sa.Column("metadata_snapshot", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "request_key"))
    sa.Index("ix_usage_run_user_time", run.c.user_id, run.c.occurred_at)
    event = sa.Table("question_usage_event", metadata,
        sa.Column("usage_run_id", sa.String(36), sa.ForeignKey("question_usage_run.id"), primary_key=True),
        sa.Column("canonical_question_id", sa.String(36), sa.ForeignKey("canonical_question.id"), primary_key=True),
        sa.Column("candidate", sa.Boolean(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("returned", sa.Boolean(), nullable=False),
        sa.Column("listed", sa.Boolean(), nullable=False),
        sa.Column("decisions", sa.JSON(), nullable=False))
    sa.Index("ix_question_usage_event_canonical_question_id", event.c.canonical_question_id)
    return state, run, event


def upgrade():
    state, run, event = tables()
    for table in (state, run, event):
        table.create(op.get_bind(), checkfirst=True)
    if not op.get_bind().scalar(sa.select(state.c.id).where(state.c.id == 1)):
        op.get_bind().execute(state.insert().values(id=1, started_at=datetime.now(timezone.utc), version="question_usage_v1"))


def downgrade():
    for table in reversed(tables()):
        table.drop(op.get_bind(), checkfirst=True)
