"""Task annotations, durable agent conversations and provider phase timings."""
from alembic import op
import sqlalchemy as sa

revision = "b16ab30d6171"
down_revision = "630dc9fee8e6"
branch_labels = depends_on = None


def upgrade():
    op.add_column("corpus_state", sa.Column("task_annotation_revision", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("occurrence_task_annotation",
        sa.Column("occurrence_id", sa.String(36), sa.ForeignKey("question_occurrence.id"), primary_key=True),
        sa.Column("response_form", sa.String(16), nullable=False),
        sa.Column("coding_focus", sa.String(16), nullable=False),
        sa.Column("producer_version", sa.String(64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    for field in ("response_form", "coding_focus"):
        op.create_index(f"ix_occurrence_task_annotation_{field}", "occurrence_task_annotation", [field])
    op.create_table("agent_conversation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_agent_conversation_user_id", "agent_conversation", ["user_id"])
    op.create_table("agent_turn",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("agent_conversation.id"), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "request_id"))
    op.create_index("ix_agent_turn_conversation_id", "agent_turn", ["conversation_id"])
    for name in ("queue_ms", "interval_ms", "provider_ms"):
        op.add_column("model_call", sa.Column(name, sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("corpus_state", "task_annotation_revision")
    for name in ("provider_ms", "interval_ms", "queue_ms"):
        op.drop_column("model_call", name)
    op.drop_table("agent_turn")
    op.drop_table("agent_conversation")
    op.drop_table("occurrence_task_annotation")
