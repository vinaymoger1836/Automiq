# Automiq

Automiq is a versioned workflow automation engine. The current local slice lets a signed-in user create a workspace, draw and publish a workflow, run it through Temporal, and inspect its progress. PostgreSQL holds workspace and run state; Redis and Temporal are local Compose services. The HTTP action uses a deterministic mock adapter. Real HTTP egress is not enabled.

## Requirements

- Docker Desktop with Compose
- Node.js 22+ and npm 11+
- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Playwright Chromium for browser checks

## Start locally

From the repository root in PowerShell:

```powershell
Copy-Item .env.example .env
docker compose --env-file .env -f infra/compose.yaml up -d --build
docker compose --env-file .env -f infra/compose.yaml exec -T api uv run --frozen --no-dev alembic -c alembic.ini upgrade head
docker compose --env-file .env -f infra/compose.yaml exec -T worker uv run --frozen --no-dev python /app/scripts/smoke.py
```

The supplied `.env.example` contains local placeholders only. Keep `.env` local and replace those placeholders before exposing services. Open [the studio](http://localhost:3000/studio), choose a synthetic local identity, then create a workspace. The synthetic identities exist only while `APP_ENV` is `development` or `test`. For external sign-in, configure `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `API_PUBLIC_URL`, and a unique `SESSION_SECRET` for your HTTPS provider. A live provider exchange has not been verified in this local E2E suite.

## Verify the local slice

Install local dependencies once with `uv sync --frozen`, `npm.cmd --prefix apps/web ci`, and `npm.cmd --prefix apps/web exec playwright install chromium`. With Compose running and migrations applied:

```powershell
$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache')
$env:PYTHONPATH='apps/api;services/orchestrator'
uv run --frozen python scripts/check_phase1_workflows.py
uv run --frozen python scripts/check_phase2_runs.py
uv run --frozen python scripts/check_phase2_faults.py
npm.cmd --prefix apps/web run test:e2e
uv run --frozen ruff check .
uv run --frozen mypy
npm.cmd --prefix apps/web run lint
npm.cmd --prefix apps/web run typecheck
npm.cmd --prefix apps/web run build
docker compose --env-file .env -f infra/compose.yaml exec -T api uv run --frozen --no-dev alembic -c alembic.ini check
```

Set `UV_CACHE_DIR` to any writable local directory if the default cache is inaccessible. The Phase 2 fault script intentionally stops and starts the local API and worker; do not run it against a shared environment. Reports and theme/viewport screenshots are saved under the Git-ignored `artifacts/e2e/`. See [the runbook](docs/runbook.md) for recovery and evidence details.

## Current boundaries

- The [API](apps/api/app/main.py) handles identity, workspace authorization, workflow definitions, and read APIs. Browser mutations use a signed session and CSRF token.
- The [worker](services/orchestrator/orchestrator/worker.py) starts queued runs from a database outbox. Temporal workflow code is deterministic; database writes and mock actions execute in activities.
- The [studio](apps/web/src/app/studio/page.tsx) supports light and dark themes, keyboard-accessible forms, and mobile layouts. The server remains the authority for graph validation and workspace permissions.
- Runs pin an immutable published version. Mock action effects use a stable key so retries can be reconciled. External exactly-once effects are not claimed.

See [architecture.md](architecture.md), [phase-planning.md](phase-planning.md), and [migration notes](infra/migrations/README.md). No commit or push is performed by the engineering agent.
