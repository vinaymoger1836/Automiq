# Phase 7: standard custom app connections

## Scope and current status

The first generic slice is implemented in code: workspace owners register a custom app through the UI/API, define bounded event schemas, receive one-time inbound/outbound signing secrets, rotate or revoke those secrets, bind an inbound event type to a published workflow, and add a signed callback action. One generic endpoint accepts CloudEvents 1.0 JSON for any configured app. The event receipt, run, queued event, and Temporal start outbox commit together. Callback activities use a stable event ID, persist attempts/status, verify a fixed HTTPS destination through pinned DNS/TLS, and require the receiving app to deduplicate retries. Two different apps can be configured without editing Automiq code.

The first slice did not include event waiting or the PR adapter. Their P7-06 implementation is described below and awaits full-stack verification. An external correlation/status query for app identities is still absent. A connection definition is immutable at version 1; adding event types currently means creating another connection. Event `data` supports a bounded, flat JSON Schema object subset of `string`, `integer`, `number`, `boolean`, or `null` fields.

## P7-06 implementation pending full-stack verification (2026-10-14)

The graph now has `wait.app_event` and `wait.github_pr_merge` nodes. Temporal holds each wait with bounded polling timers and database/provider checks in activities, so a worker restart can replay the wait. App events now persist their validated type and bounded data for correlation. The app wait matches one required string or integer field against the starting run's payload, within the same workspace and connection, using only events received after the run was created. The PR wait reads the fixed repository from a workspace GitHub integration and checks the configured PR number through [GitHub's read-only pull request endpoint](https://docs.github.com/en/rest/pulls/pulls#get-a-pull-request), with a 64 KiB response limit. A merged PR continues; a closed unmerged PR or timeout fails the run. In local/test fake-provider mode, the PR is open for one check and then merged. Neither wait mutates GitHub.

The original task auto-close composition is: signed task-created event → wait for a correlated completion event or GitHub PR merge → signed custom-app callback (or the existing Zoho task status action) → end. The browser editor exposes both waits and their bounds. Migration `0009_phase7_event_wait` is additive; historical receipts have empty correlation data and cannot satisfy waits. Connection definitions remain version 1. Event data is stored as bounded JSONB, so send only non-sensitive identifiers and statuses. The retention job scrubs correlation data after the longer of eight days or the run-detail retention period while keeping receipt identity for deduplication. Polling intervals of 5–300 seconds, a maximum seven-day wait, and no more than 2500 checks bound Temporal history growth; operators should keep intervals appropriate to their workflow volume.

Verification performed: `uv run --frozen ruff check .`, `uv run --frozen mypy`, web typecheck/lint, Python compile, and Temporal workflow sandbox preparation passed. The full-stack script `uv run --frozen python scripts/check_phase7_waits.py` produced ignored `artifacts/e2e/phase7-waits/report.json` with `ConnectError` and zero scenarios because the Docker daemon/API was unavailable. P7-06 remains unchecked. No live GitHub API or task app was used.

When Docker is available, run from the repository root with synthetic `.env` values:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
npm.cmd --prefix apps/web ci
$env:UV_CACHE_DIR=Join-Path (Get-Location) '.uv-cache'
uv run --frozen python scripts/prepare_app_e2e.py
docker compose --env-file .env --env-file .env.app-e2e.local -f infra/compose.yaml -f infra/compose.app-e2e.yaml up -d --build
docker compose --env-file .env --env-file .env.app-e2e.local -f infra/compose.yaml -f infra/compose.app-e2e.yaml exec -T api uv run --frozen --no-dev alembic -c alembic.ini upgrade head
docker compose --env-file .env --env-file .env.app-e2e.local -f infra/compose.yaml -f infra/compose.app-e2e.yaml exec -T api uv run --frozen --no-dev alembic -c alembic.ini check
uv run --frozen python scripts/check_phase7_waits.py
Push-Location apps/web
.\node_modules\.bin\playwright.cmd install chromium
.\node_modules\.bin\playwright.cmd test --config=playwright.app-connections.config.ts
Pop-Location
```

The new API E2E checks a wrong correlation, duplicate matching event, worker restart while waiting, signed task auto-close, fake PR merge, and timeout. The browser spec adds wait-node inspector screenshots in light/dark themes at 375 and 1280 pixels and overflow checks at 375, 768, 1280, and 1440 pixels. Reports/screenshots remain ignored under `artifacts/e2e/phase7-waits/` and `artifacts/e2e/app-connections/`. The real GitHub response path and live app bridge still need optional credential-backed smoke verification.

## App contract

Owners create `POST /api/v1/workspaces/{ws}/app-connections` with a name, optional fixed HTTPS callback URL, `inbound_schemas`, and `outbound_schemas`. The create/rotate response displays two secrets once. Later list responses show metadata only. Each registered schema uses `type: object`, declared scalar `properties`, `required`, and `additionalProperties: false`. A connection may be inbound-only. An app that needs workflow results must also receive callbacks.

The app sends one CloudEvent to the connection's returned `webhook_url` with `Content-Type: application/cloudevents+json`, `X-Automiq-Timestamp` as Unix seconds, and `X-Automiq-Signature: sha256=<HMAC-SHA-256>`. The HMAC input is the decimal timestamp, a period byte, and the exact UTF-8 request body. Automiq accepts timestamps within five minutes and deduplicates by connection, `source`, and `id`. Schema property names resembling secrets are rejected; do not send credentials in event data. The `202` response includes the run IDs; it acknowledges durable intake, not workflow completion. Reusing an event ID with changed body returns `409`.

Example synthetic event (replace IDs and values for a real app):

```json
{"specversion":"1.0","id":"event-001","source":"urn:example:task-app","type":"com.example.task.submitted.v1","datacontenttype":"application/json","data":{"task_id":"task-123"}}
```

For callbacks, Automiq sends a CloudEvent to the fixed URL and signs it with the distinct outbound secret and the same timestamp/body scheme. The callback event ID is stable for a run and node. The app should record that ID before returning a 2xx response; duplicate deliveries must not repeat its business mutation. The workspace-scoped callback status endpoint is `GET /api/v1/workspaces/{ws}/app-connections/{id}/callbacks`.

## Reproduce the synthetic E2E

Run from the repository root with a fresh local test database, Docker Desktop, Node.js, Python, uv, and Playwright Chromium. `.env` comes from the sanitized `.env.example`; the preparation script writes a short-lived encryption key to the ignored `.env.app-e2e.local` and a synthetic TLS certificate to ignored `artifacts/e2e/app-connections/tls/`. Do not reuse this E2E key for a real workspace.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
npm.cmd --prefix apps/web ci
$env:UV_CACHE_DIR=Join-Path (Get-Location) '.uv-cache'
uv run --frozen python scripts/prepare_app_e2e.py
docker compose --env-file .env --env-file .env.app-e2e.local -f infra/compose.yaml -f infra/compose.app-e2e.yaml up -d --build
docker compose --env-file .env --env-file .env.app-e2e.local -f infra/compose.yaml -f infra/compose.app-e2e.yaml exec -T api uv run --frozen --no-dev alembic -c alembic.ini upgrade head
uv run --frozen python scripts/check_app_connections.py
Push-Location apps/web
.\node_modules\.bin\playwright.cmd install chromium
.\node_modules\.bin\playwright.cmd test --config=playwright.app-connections.config.ts
Pop-Location
```

The API E2E creates two synthetic apps through the same UI/API-independent contract, sends signed and forged events, checks deduplication, runs both workflows through Temporal, verifies callback HMACs and retry status, checks cross-workspace isolation, and rotates/revokes credentials. The browser E2E checks connection setup plus light/dark screenshots and no horizontal overflow at 375, 768, 1280, and 1440 pixels. Evidence paths are ignored: `artifacts/e2e/app-connections/report.json`, `callbacks.jsonl`, `playwright.xml`, `browser/`, and browser traces.

## Verification observed on 2026-10-12

- `uv run --frozen ruff check .`: passed after implementation changes.
- `uv run --frozen mypy`: passed for configured API/worker sources.
- `npm.cmd --prefix apps/web run typecheck`, `lint`, and `build`: passed.
- `docker compose ... config --quiet`: passed with the synthetic E2E overlay.
- `uv run --frozen alembic -c alembic.ini upgrade head --sql`: generated offline SQL under ignored evidence.
- `docker compose --env-file .env --env-file .env.app-e2e.local -f infra/compose.yaml -f infra/compose.app-e2e.yaml up -d --build`: passed after Docker Desktop started. The first E2E run exposed a worker import error, a parent/child flush-order issue, and a missing synthetic provider mount; all three were fixed before the passing run. Sanitized failure history: ignored `artifacts/e2e/app-connections/failure-notes.md`.
- `docker compose --env-file .env --env-file .env.app-e2e.local -f infra/compose.yaml -f infra/compose.app-e2e.yaml exec -T api uv run --frozen --no-dev alembic -c alembic.ini upgrade head`: passed. The same command with `check` instead of `upgrade head` reported no new upgrade operations.
- `uv run --frozen python scripts/check_app_connections.py`: passed 39 checks across two independent apps, including signed intake, invalid schema and forged signature rejection, duplicate/conflict handling, workflow completion, signed callback, retry ledger, isolation, credential rotation, and revocation. Machine-readable report: ignored `artifacts/e2e/app-connections/report.json`; synthetic callback receipts: `callbacks.jsonl`.
- `Push-Location apps/web; .\node_modules\.bin\playwright.cmd test --config=playwright.app-connections.config.ts; Pop-Location`: passed one Chromium scenario. Ignored JUnit: `artifacts/e2e/app-connections/playwright.xml`; light/dark screenshots at 375px and 1280px: `browser/`. The scenario also checked no horizontal overflow at 768px and 1440px.
- `docker compose --env-file .env --env-file .env.app-e2e.local -f infra/compose.yaml -f infra/compose.app-e2e.yaml exec -T worker uv run --frozen --no-dev python scripts/smoke.py`: passed Temporal activity and PostgreSQL connection.

The passing run used Windows, Python 3.12.10, Node.js 24.15.0, uv 0.12.23, Playwright 1.64.0 with Chromium, Docker Engine 29.6.2, PostgreSQL 16.6, Redis 7.4.2, Temporal 1.27.2, Next.js 16.4.0, and a synthetic HTTPS callback receiver. No real external API was called by the E2E profile.

## Local sign-in origin fix (2026-10-12)

Local owner sign-in previously returned `403 Origin check failed` when the studio was opened at `http://127.0.0.1:3000` while `WEB_ORIGIN=http://localhost:3000`. Development/test mode now accepts these two equivalent HTTP loopback origins on the configured port for sign-in, CSRF-protected writes, and CORS. Production still accepts only the configured origin. The initial `401` from `/api/v1/me` before sign-in is expected.

After rebuilding the API with `docker compose --env-file .env --env-file .env.app-e2e.local -f infra/compose.yaml -f infra/compose.app-e2e.yaml up -d --build api`, `Push-Location apps/web; .\node_modules\.bin\playwright.cmd test --config=playwright.app-connections.config.ts; Pop-Location` passed two Chromium scenarios: owner sign-in and workspace creation from both loopback URLs, plus the existing custom-app UI flow. The ignored JUnit report is `artifacts/e2e/app-connections/playwright.xml`. Direct synthetic requests returned `200` from each loopback origin and `403` with a foreign or missing origin.
