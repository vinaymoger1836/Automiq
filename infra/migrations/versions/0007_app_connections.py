"""Workspace custom app connections and durable event/callback receipts.

Revision ID: 0007_app_connections
Revises: 0006_phase6_trace_context
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0007_app_connections"
down_revision = "0006_phase6_trace_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_connections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("public_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("callback_url", sa.String(500)),
        sa.Column("inbound_schemas", JSONB, nullable=False),
        sa.Column("outbound_schemas", JSONB, nullable=False),
        sa.Column("contract_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("encrypted_credentials", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("public_id", name="uq_app_connections_public_id"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_app_connections_id_workspace"),
        sa.CheckConstraint("key_version > 0", name="ck_app_connections_key_version_positive"),
        sa.CheckConstraint("credential_version > 0", name="ck_app_connections_credential_version_positive"),
    )
    op.create_index("ix_app_connections_workspace", "app_connections", ["workspace_id"])
    op.create_table(
        "app_event_receipts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("connection_id", UUID(as_uuid=True), sa.ForeignKey("app_connections.id"), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("event_id", sa.String(80), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("run_ids", JSONB, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("connection_id", "source", "event_id", name="uq_app_event_identity"),
    )
    op.create_index("ix_app_event_receipts_received", "app_event_receipts", ["received_at"])
    op.create_table(
        "app_callback_deliveries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("connection_id", UUID(as_uuid=True), sa.ForeignKey("app_connections.id"), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("event_id", sa.String(80), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "node_id", name="uq_app_callback_run_node"),
        sa.UniqueConstraint("event_id", name="uq_app_callback_event_id"),
        sa.CheckConstraint("status IN ('pending','delivered','failed')", name="ck_app_callback_deliveries_status_valid"),
    )
    op.drop_constraint("ck_triggers_type_valid", "triggers", type_="check")
    op.create_check_constraint(
        "ck_triggers_type_valid", "triggers", "type IN ('github.issue','schedule','app.event')"
    )


def downgrade() -> None:
    raise RuntimeError("App connection history migration is forward-only; restore a reviewed backup")
