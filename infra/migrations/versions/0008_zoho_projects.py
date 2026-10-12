"""Allow Zoho Projects workspace integrations.

Revision ID: 0008_zoho_projects
Revises: 0007_app_connections
"""

from alembic import op

revision = "0008_zoho_projects"
down_revision = "0007_app_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_integrations_provider_valid", "integrations", type_="check")
    op.create_check_constraint(
        "ck_integrations_provider_valid",
        "integrations",
        "provider IN ('github','slack','zoho_projects')",
    )


def downgrade() -> None:
    if op.get_bind().exec_driver_sql(
        "SELECT 1 FROM integrations WHERE provider = 'zoho_projects' LIMIT 1"
    ).scalar():
        raise RuntimeError("Remove Zoho Projects integrations explicitly before downgrading")
    op.drop_constraint("ck_integrations_provider_valid", "integrations", type_="check")
    op.create_check_constraint(
        "ck_integrations_provider_valid", "integrations", "provider IN ('github','slack')"
    )
