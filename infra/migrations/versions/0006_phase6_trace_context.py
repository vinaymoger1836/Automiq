"""Persist W3C trace context for asynchronous run execution.

Revision ID: 0006_phase6_trace_context
Revises: 0005_phase5_approvals
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_phase6_trace_context"
down_revision = "0005_phase5_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("traceparent", sa.String(55), nullable=True))


def downgrade() -> None:
    op.drop_column("workflow_runs", "traceparent")
