# Migrations

Run `docker compose --env-file .env -f infra/compose.yaml exec -T api uv run --frozen --no-dev alembic -c alembic.ini upgrade head` from the repository root after the local stack is healthy. The first revision creates identity, workspace, workflow, version, membership, and audit tables. It does not change Temporal's own databases.

Revisions `0002_immutable_versions` and `0003_phase2_runs` add an immutable-version database trigger plus run, step, event, outbox, and mock-effect tables. They do not alter existing Phase 1 columns. All revisions are forward-only to avoid silently dropping user, workflow, or run data. For rollback, stop application writes, take and verify a PostgreSQL backup, then restore the prior database state. Do not run `alembic downgrade` against live data; revisions raise an explicit error.
