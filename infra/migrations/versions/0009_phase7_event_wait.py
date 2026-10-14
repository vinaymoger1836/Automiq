"""Store bounded app event correlation data for durable waits.

Revision ID: 0009_phase7_event_wait
Revises: 0008_zoho_projects
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009_phase7_event_wait"
down_revision = "0008_zoho_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_event_receipts", sa.Column("event_type", sa.String(80)))
    op.add_column(
        "app_event_receipts",
        sa.Column("event_data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.alter_column("app_event_receipts", "event_data", server_default=None)
    op.create_index(
        "ix_app_event_receipts_match",
        "app_event_receipts",
        ["connection_id", "event_type", "received_at"],
    )
    op.create_index(
        "ix_app_event_receipts_data", "app_event_receipts", ["event_data"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    raise RuntimeError("Downgrade requires an explicit event data retention decision")
