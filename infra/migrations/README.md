# Migrations

Run `docker compose --env-file .env -f infra/compose.yaml exec -T api uv run --frozen --no-dev alembic -c alembic.ini upgrade head` from the repository root after the local stack is healthy. The first revision creates identity, workspace, workflow, version, membership, and audit tables. It does not change Temporal's own databases.

P1-01 is forward-only to avoid silently dropping user or workflow data. For rollback, first stop application writes, take and verify a PostgreSQL backup, then restore the prior database state. Do not run `alembic downgrade` against live data; the revision raises an explicit error. Later schema changes require their own compatibility and rollback notes.
