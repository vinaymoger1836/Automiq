"""Protect published graphs from accidental updates or deletion.

Revision ID: 0002_immutable_versions
Revises: 0001_phase1_core
"""

from alembic import op

revision = "0002_immutable_versions"
down_revision = "0001_phase1_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION reject_workflow_version_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'published workflow versions are immutable';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER workflow_versions_immutable
        BEFORE UPDATE OR DELETE ON workflow_versions
        FOR EACH ROW EXECUTE FUNCTION reject_workflow_version_mutation();
        """
    )


def downgrade() -> None:
    raise RuntimeError("Immutable version protection is forward-only; restore a reviewed backup")
