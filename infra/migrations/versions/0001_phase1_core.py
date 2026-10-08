"""Create Phase 1 identity, workspace, workflow, and audit tables.

Revision ID: 0001_phase1_core
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001_phase1_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("oidc_issuer", sa.String(255), nullable=False),
        sa.Column("oidc_subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("oidc_issuer", "oidc_subject", name="uq_users_oidc_identity"),
    )
    op.create_index("ix_users_email_lower", "users", [sa.text("lower(email)")])

    op.create_table(
        "workspaces",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspaces"),
        sa.UniqueConstraint("slug", name="uq_workspaces_slug"),
        sa.CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="ck_workspaces_slug_format"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_workspaces_name_nonempty"),
    )

    op.create_table(
        "memberships",
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", name="pk_memberships"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_memberships_workspace_id_workspaces"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_memberships_user_id_users"),
        sa.CheckConstraint(
            "role IN ('owner', 'editor', 'viewer')", name="ck_memberships_role_valid"
        ),
    )
    op.create_index("ix_memberships_user_workspace", "memberships", ["user_id", "workspace_id"])

    op.create_table(
        "workflows",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'active'"), nullable=False),
        sa.Column("draft_graph", JSONB(), nullable=False),
        sa.Column("draft_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("published_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflows"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_workflows_workspace_id_workspaces"
        ),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_workflows_status_valid"),
        sa.CheckConstraint("draft_revision >= 0", name="ck_workflows_draft_revision_nonnegative"),
    )
    op.create_index("ix_workflows_workspace_updated", "workflows", ["workspace_id", "updated_at"])

    op.create_table(
        "workflow_versions",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("workflow_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("graph_json", JSONB(), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column(
            "published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_versions"),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], name="fk_workflow_versions_workflow_id_workflows"
        ),
        sa.UniqueConstraint("workflow_id", "version", name="uq_workflow_versions_number"),
        sa.UniqueConstraint("workflow_id", "id", name="uq_workflow_versions_workflow_id"),
        sa.CheckConstraint("version > 0", name="ck_workflow_versions_version_positive"),
    )
    op.create_foreign_key(
        "fk_workflows_published_version_own",
        "workflows",
        "workflow_versions",
        ["id", "published_version_id"],
        ["workflow_id", "id"],
    )

    op.create_table(
        "audit_logs",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("resource_id", UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name="fk_audit_logs_workspace_id_workspaces"
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'service', 'system')", name="ck_audit_logs_actor_type_valid"
        ),
    )
    op.create_index("ix_audit_logs_workspace_created", "audit_logs", ["workspace_id", "created_at"])


def downgrade() -> None:
    raise RuntimeError(
        "P1-01 is forward-only: restore a verified backup before replacing this schema"
    )
