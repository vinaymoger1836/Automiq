"""Create durable run projections, event stream, outbox, and mock effect ledger.

Revision ID: 0003_phase2_runs
Revises: 0002_immutable_versions
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0003_phase2_runs"
down_revision = "0002_immutable_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("temporal_workflow_id", sa.String(80), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("input_json", JSONB, nullable=False),
        sa.Column("error", sa.String(255)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_runs"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_workflow_runs_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], name="fk_workflow_runs_workflow_id_workflows"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["workflow_versions.id"],
            name="fk_workflow_runs_version_id_workflow_versions",
        ),
        sa.UniqueConstraint(
            "workspace_id", "workflow_id", "idempotency_key", name="uq_run_idempotency"
        ),
        sa.UniqueConstraint("temporal_workflow_id", name="uq_run_temporal_id"),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed')",
            name="ck_workflow_runs_status_valid",
        ),
    )
    op.create_index("ix_runs_workspace_started", "workflow_runs", ["workspace_id", "started_at"])
    op.create_table(
        "step_runs",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("output_json", JSONB),
        sa.Column("error", sa.String(255)),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_step_runs"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["workflow_runs.id"], name="fk_step_runs_run_id_workflow_runs"
        ),
        sa.UniqueConstraint("run_id", "node_id", "attempt", name="uq_step_attempt"),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed','skipped')", name="ck_step_runs_status_valid"
        ),
    )
    op.create_index("ix_step_runs_run_node", "step_runs", ["run_id", "node_id"])
    op.create_table(
        "run_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_key", sa.String(120), nullable=False),
        sa.Column("type", sa.String(80), nullable=False),
        sa.Column("payload_json", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_run_events_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["workflow_runs.id"], name="fk_run_events_run_id_workflow_runs"
        ),
        sa.UniqueConstraint("run_id", "event_key", name="uq_run_event_key"),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id", "id"])
    op.create_table(
        "run_start_outbox",
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(120)),
        sa.PrimaryKeyConstraint("run_id", name="pk_run_start_outbox"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["workflow_runs.id"], name="fk_run_start_outbox_run_id_workflow_runs"
        ),
    )
    op.create_index("ix_outbox_pending", "run_start_outbox", ["started_at", "created_at"])
    op.create_table(
        "action_effects",
        sa.Column("effect_key", sa.String(120), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("invocations", sa.Integer(), nullable=False),
        sa.Column("output_json", JSONB),
        sa.ForeignKeyConstraint(
            ["run_id"], ["workflow_runs.id"], name="fk_action_effects_run_id_workflow_runs"
        ),
        sa.CheckConstraint("invocations > 0", name="ck_action_effects_invocations_positive"),
    )


def downgrade() -> None:
    raise RuntimeError("Run history migration is forward-only; restore a reviewed backup")
