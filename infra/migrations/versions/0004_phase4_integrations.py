"""Encrypted integrations, published trigger bindings, and webhook deduplication.

Revision ID: 0004_phase4_integrations
Revises: 0003_phase2_runs
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004_phase4_integrations"
down_revision = "0003_phase2_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integrations",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("encrypted_credentials", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("credential_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("rotated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("provider IN ('github','slack')", name="ck_integrations_provider_valid"),
        sa.CheckConstraint("key_version > 0", name="ck_integrations_key_version_positive"),
        sa.CheckConstraint(
            "credential_version > 0", name="ck_integrations_credential_version_positive"
        ),
        sa.UniqueConstraint("id", "workspace_id", name="uq_integrations_id_workspace"),
    )
    op.create_index(
        "ix_integrations_workspace_provider", "integrations", ["workspace_id", "provider"]
    )
    op.create_table(
        "integration_assignments",
        sa.Column("integration_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("integration_id", "user_id", name="pk_integration_assignments"),
        sa.ForeignKeyConstraint(
            ["integration_id", "workspace_id"],
            ["integrations.id", "integrations.workspace_id"],
            name="fk_integration_assignments_workspace_integration",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["memberships.workspace_id", "memberships.user_id"],
            name="fk_integration_assignments_membership",
        ),
    )
    op.create_index(
        "ix_integration_assignments_workspace_user",
        "integration_assignments",
        ["workspace_id", "user_id"],
    )
    op.create_table(
        "triggers",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column(
            "workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("workflow_id", UUID(as_uuid=True), sa.ForeignKey("workflows.id"), nullable=False),
        sa.Column(
            "version_id", UUID(as_uuid=True), sa.ForeignKey("workflow_versions.id"), nullable=False
        ),
        sa.Column("integration_id", UUID(as_uuid=True), sa.ForeignKey("integrations.id")),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("config", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("public_id", name="uq_triggers_public_id"),
        sa.CheckConstraint("type IN ('github.issue','schedule')", name="ck_triggers_type_valid"),
    )
    op.create_index("ix_triggers_workspace_workflow", "triggers", ["workspace_id", "workflow_id"])
    op.create_table(
        "webhook_deliveries",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("trigger_id", UUID(as_uuid=True), sa.ForeignKey("triggers.id"), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("delivery_id", sa.String(80), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("result_run_id", UUID(as_uuid=True), sa.ForeignKey("workflow_runs.id")),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("trigger_id", "delivery_id", name="uq_webhook_trigger_delivery"),
    )
    op.create_index("ix_webhook_deliveries_received", "webhook_deliveries", ["received_at"])


def downgrade() -> None:
    raise RuntimeError("Integration history migration is forward-only; restore a reviewed backup")
