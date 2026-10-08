# Development runbook

## Start and inspect

Copy `.env.example` to `.env` and run `docker compose --env-file .env -f infra/compose.yaml up -d --build`. Apply migrations with `docker compose --env-file .env -f infra/compose.yaml exec -T api uv run --frozen --no-dev alembic -c alembic.ini upgrade head`. Inspect service health with `docker compose --env-file .env -f infra/compose.yaml ps` and local logs with `docker compose --env-file .env -f infra/compose.yaml logs --tail=100 api worker temporal`. Avoid sharing unsanitized logs.

Run `docker compose --env-file .env -f infra/compose.yaml exec -T worker uv run --frozen --no-dev python /app/scripts/smoke.py` to prove Temporal polling and PostgreSQL access. `/health/ready` checks PostgreSQL, Redis, and Temporal; `/health/live` checks only the API process. Browser entry point: `http://localhost:3000/studio`.

## Workflow and run recovery

An accepted manual run and its start-outbox row are committed together. The worker reconciles pending rows to Temporal using `run:<uuid>` as the workflow ID. If an API request succeeds but a run remains queued, inspect worker health and the outbox; restarting the worker retries the start. If a worker stops while an activity is in flight, Temporal retries the activity according to its policy. Mock effects are recorded by stable run/node key, and run projections have unique event keys. Real external actions would need provider-specific deduplication or reconciliation.

`docker compose --env-file .env -f infra/compose.yaml down` leaves named volumes intact. Do not remove volumes to recover a stalled run. The fault-injection E2E script in `scripts/check_phase2_faults.py` exercises API restart, worker restart, retry, timeout, permanent failure, and duplicate Temporal start on the local stack only.

## Evidence and current limits

Use the exact setup and check commands in [README](../README.md). Synthetic E2E reports are saved as `artifacts/e2e/phase1-workflows/report.json`, `artifacts/e2e/phase2-runs/report.json`, `artifacts/e2e/phase2-faults/report.json`, and `artifacts/e2e/phase3-studio/playwright.xml`. Browser screenshots under `artifacts/e2e/phase3-studio/` cover light and dark themes at mobile and desktop sizes. These paths are Git-ignored. The tests use synthetic identities and mock actions; no paid provider calls are made.

The OIDC login path is implemented but needs a configured live provider exchange before a production claim. The restricted HTTPS GET connector is opt-in and limited to one operator-configured origin. It uses application-level DNS/IP restrictions, TLS hostname verification, a response size cap, scalar output projection, and no redirects. Production egress also needs network-level restrictions. See [migration notes](../infra/migrations/README.md) before any rollback; migrations are forward-only and data-preserving.

## P2-06 fake HTTPS provider E2E

Run from the repository root after the normal stack and migrations are ready. The synthetic certificate and private key are generated under ignored `artifacts/e2e/phase2-http/tls/`; neither belongs in Git. The overlay sets `APP_ENV=test`, enables the connector only for the `fake-provider` hostname, and trusts only its generated certificate.

```powershell
$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache')
uv run --frozen python scripts/prepare_phase2_http_e2e.py
docker compose --env-file .env -f infra/compose.yaml -f infra/compose.http-e2e.yaml up -d --build
uv run --frozen python scripts/check_phase2_http.py
$env:P2_HTTP_ORIGIN='https://metadata-e2e'
$env:P2_HTTP_TEST_PRIVATE_HOST='metadata-e2e'
docker compose --env-file .env -f infra/compose.yaml -f infra/compose.http-e2e.yaml up -d api worker
uv run --frozen python scripts/check_phase2_http_blocked.py
Remove-Item Env:\P2_HTTP_ORIGIN,Env:\P2_HTTP_TEST_PRIVATE_HOST
docker compose --env-file .env -f infra/compose.yaml up -d --build api worker web
docker compose --env-file .env -f infra/compose.yaml -f infra/compose.http-e2e.yaml stop fake-provider
docker compose --env-file .env -f infra/compose.yaml -f infra/compose.http-e2e.yaml rm -f fake-provider
uv run --frozen python scripts/check_phase2_http_disabled.py
```

The three machine-readable results are `artifacts/e2e/phase2-http/report.json`, `blocked.json`, and `disabled.json`. The successful run checks TLS to a synthetic provider, pinned workflow version, selected output only, redirect refusal, size/type rejection, and bounded retries. The blocked run maps a configured test hostname to a metadata-range IP and verifies `unsafe_destination` before any connection. The final run confirms the normal local stack rejects publication of a real HTTP action. The test-only private-network allowance applies only when `APP_ENV=test`, and only to the configured synthetic hostname within RFC1918 ranges. No public or paid service is called.
