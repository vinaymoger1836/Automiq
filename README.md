# Automiq

Automiq is a versioned workflow automation engine. Phase 0 provides the local development foundation: a Next.js health dashboard, FastAPI health endpoints, PostgreSQL, Redis, Temporal, and a registered Temporal worker. Workflow authoring and execution features begin in later phases.

## Requirements

- Docker Desktop with Compose and a running daemon
- Node.js 22+ and npm 11+ for local web checks
- Python 3.12 and [uv](https://docs.astral.sh/uv/) for local Python checks

## Start locally

From the repository root, copy `.env.example` to `.env` and keep that file local. The supplied values are local development placeholders. Change them before exposing any service beyond your machine.

```powershell
Copy-Item .env.example .env
docker compose --env-file .env -f infra/compose.yaml up -d --build
docker compose --env-file .env -f infra/compose.yaml exec -T worker uv run --frozen --no-dev python /app/scripts/smoke.py
```

Open the [web dashboard](http://localhost:3000), [API readiness](http://localhost:8000/health/ready), or [Temporal UI](http://localhost:8080). The smoke command executes the `BootstrapCheck` Temporal workflow; its activity queries PostgreSQL. It should print `Temporal worker registered; PostgreSQL activity succeeded`.

Run the full PowerShell startup and smoke shortcut with `./scripts/smoke.ps1`. Use `docker compose --env-file .env -f infra/compose.yaml down` to stop services. Volumes persist; deleting them removes local data.

## Checks

```powershell
uv sync --frozen
uv run ruff check .
uv run mypy
npm.cmd --prefix apps/web ci
npm.cmd --prefix apps/web run lint
npm.cmd --prefix apps/web run typecheck
npm.cmd --prefix apps/web run build
npm.cmd --prefix apps/web run test:e2e
```

Playwright needs Chromium installed with `npm.cmd --prefix apps/web exec playwright install chromium`. The browser test in `apps/web/e2e` expects the Compose stack running and writes screenshots and a JUnit report under the ignored `artifacts/e2e/phase0/` directory. It checks desktop and mobile widths in light and dark themes.

## Layout and boundaries

- `apps/api`: control plane. Only infrastructure health endpoints exist in Phase 0.
- `services/orchestrator`: Temporal worker and bootstrap registration check. Workflow code contains no side effects; PostgreSQL access lives in an activity.
- `apps/web`: status dashboard and same-origin health proxy.
- `infra/compose.yaml`: local services and persistent volumes.
- `packages/`, `services/integrations/`, and `infra/migrations/`: reserved for later phases; no domain contracts or migrations are claimed yet.

See [architecture.md](architecture.md), [phase-planning.md](phase-planning.md), and [development runbook](docs/runbook.md). Do not use the local placeholders for production credentials.
