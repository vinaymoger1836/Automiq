# Development runbook

## Start and inspect

Copy `.env.example` to `.env`, then run `docker compose --env-file .env -f infra/compose.yaml up -d --build`. Inspect services with `docker compose --env-file .env -f infra/compose.yaml ps` and logs with `docker compose --env-file .env -f infra/compose.yaml logs --tail=100 api worker temporal`. The API readiness endpoint checks PostgreSQL, Redis, and Temporal. The liveness endpoint checks only the API process.

Run `docker compose --env-file .env -f infra/compose.yaml exec -T worker uv run --frozen --no-dev python /app/scripts/smoke.py` to prove a worker is polling and its PostgreSQL activity succeeds. A failing workflow points first to worker or Temporal logs; a degraded readiness response identifies the failed dependency without revealing connection strings.

## Failure recovery

Compose restarts the worker if it exits. Re-run the smoke workflow after recovery. `docker compose ... down` leaves named volumes intact. Do not use `down -v` unless you intend to delete local state. Never paste `.env` contents or unsanitized logs into issues.

## Phase 0 limits

Health checks are infrastructure checks, not proof of identity, tenant isolation, or workflow run behavior. The bootstrap workflow is a development smoke operation and carries no user data. At the Phase 0 handoff, no business migrations or real integrations were present.

## Phase 1 schema

After Compose is healthy, run `docker compose --env-file .env -f infra/compose.yaml exec -T api uv run --frozen --no-dev alembic -c alembic.ini upgrade head`. Run `alembic -c alembic.ini check` in the API container to compare the ORM metadata with the database, then run `python /app/scripts/check_phase1_schema.py` there to verify constraints using rolled-back synthetic rows. The migration is forward-only; the [migration notes](../infra/migrations/README.md) explain backup-based rollback. No Phase 1 authentication or workspace API has been enabled yet.
