"""Durable approval checkpoints.

Revision ID: 0005_phase5_approvals
Revises: 0004_phase4_integrations
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0005_phase5_approvals"
down_revision = "0004_phase4_integrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("signal_sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "node_id", name="uq_approvals_run_node"),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','expired')",
            name="ck_approvals_status_valid",
        ),
    )
    op.create_index(
        "ix_approvals_workspace_status", "approvals", ["workspace_id", "status", "created_at"]
    )
    op.create_table(
        "issue_contents",
        sa.Column(
            "run_id", UUID(as_uuid=True), sa.ForeignKey("workflow_runs.id"), primary_key=True
        ),
        sa.Column(
            "workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("encrypted_content", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
    )
    op.drop_constraint("ck_step_runs_status_valid", "step_runs", type_="check")
    op.alter_column("step_runs", "status", type_=sa.String(24), existing_type=sa.String(16))
    op.create_check_constraint(
        "status_valid",
        "step_runs",
        "status IN ('running','awaiting_approval','succeeded','failed','skipped')",
    )


def downgrade() -> None:
    op.drop_table("issue_contents")
    op.drop_constraint("ck_step_runs_status_valid", "step_runs", type_="check")
    op.alter_column("step_runs", "status", type_=sa.String(16), existing_type=sa.String(24))
    op.create_check_constraint(
        "status_valid", "step_runs", "status IN ('running','succeeded','failed','skipped')"
    )
    op.drop_index("ix_approvals_workspace_status", table_name="approvals")
    op.drop_table("approvals")
