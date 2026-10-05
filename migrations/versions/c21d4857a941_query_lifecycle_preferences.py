"""Durable query runs/events, write intentions and explicitly saved preferences."""
from alembic import op
import sqlalchemy as sa

revision = "c21d4857a941"
down_revision = "b16ab30d6171"
branch_labels = depends_on = None

def upgrade():
    op.add_column("occurrence_task_annotation",sa.Column("classification_status",sa.String(16),nullable=False,server_default="NEEDS_REVIEW"))
    op.create_index("ix_occurrence_task_annotation_classification_status","occurrence_task_annotation",["classification_status"])
    op.execute("UPDATE occurrence_task_annotation SET classification_status='UNKNOWN' WHERE response_form='UNKNOWN' OR coding_focus='UNKNOWN'")
    op.create_table("task_annotation_draft",
        sa.Column("occurrence_id",sa.String(36),sa.ForeignKey("question_occurrence.id"),primary_key=True),
        sa.Column("reviewer_id",sa.String(128),nullable=False),sa.Column("payload",sa.JSON(),nullable=False),
        sa.Column("evidence",sa.JSON(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))
    for col in (
        sa.Column("owner_id",sa.String(36)), sa.Column("lease_until",sa.DateTime(timezone=True)),
        sa.Column("request_payload",sa.JSON(),nullable=False,server_default="{}"),
        sa.Column("state_before",sa.JSON(),nullable=False,server_default="{}"),
        sa.Column("state_after",sa.JSON(),nullable=False,server_default="{}"),
        sa.Column("event_sequence",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("cancel_requested",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column("error_code",sa.String(128)),sa.Column("finished_at",sa.DateTime(timezone=True)),
    ): op.add_column("agent_turn",col)
    op.add_column("model_call",sa.Column("ttft_ms",sa.Integer()))
    op.add_column("model_call",sa.Column("query_run_id",sa.String(36),sa.ForeignKey("agent_turn.id")))
    op.add_column("model_call",sa.Column("token_budget_charge",sa.Integer()))
    op.create_index("ix_model_call_query_run_id","model_call",["query_run_id"])
    op.create_table("agent_event",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("run_id",sa.String(36),sa.ForeignKey("agent_turn.id"),nullable=False),
        sa.Column("sequence",sa.Integer(),nullable=False),
        sa.Column("kind",sa.String(32),nullable=False),sa.Column("payload",sa.JSON(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("run_id","sequence"))
    op.create_index("ix_agent_event_run_id","agent_event",["run_id"])
    op.create_table("agent_tool_invocation",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("run_id",sa.String(36),sa.ForeignKey("agent_turn.id"),nullable=False),
        sa.Column("ordinal",sa.Integer(),nullable=False),sa.Column("tool_name",sa.String(64),nullable=False),
        sa.Column("payload_hash",sa.String(64),nullable=False),sa.Column("arguments",sa.JSON(),nullable=False),
        sa.Column("status",sa.String(16),nullable=False),sa.Column("result",sa.JSON(),nullable=False),
        sa.UniqueConstraint("run_id","ordinal"))
    op.create_index("ix_agent_tool_invocation_run_id","agent_tool_invocation",["run_id"])
    op.create_table("user_preference",
        sa.Column("user_id",sa.String(128),primary_key=True),sa.Column("key",sa.String(64),primary_key=True),
        sa.Column("value",sa.JSON()),sa.Column("source_message",sa.Text(),nullable=False),
        sa.Column("version",sa.Integer(),nullable=False),sa.Column("deleted",sa.Boolean(),nullable=False),
        sa.Column("expires_at",sa.DateTime(timezone=True)),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False))

def downgrade():
    op.drop_table("task_annotation_draft")
    op.drop_index("ix_occurrence_task_annotation_classification_status","occurrence_task_annotation")
    op.drop_column("occurrence_task_annotation","classification_status")
    op.drop_table("user_preference")
    op.drop_table("agent_tool_invocation")
    op.drop_table("agent_event")
    op.drop_column("model_call","ttft_ms")
    op.drop_index("ix_model_call_query_run_id","model_call")
    op.drop_column("model_call","query_run_id")
    op.drop_column("model_call","token_budget_charge")
    for name in ("owner_id","lease_until","request_payload","state_before","state_after",
                 "event_sequence","cancel_requested","error_code","finished_at"):
        op.drop_column("agent_turn",name)
