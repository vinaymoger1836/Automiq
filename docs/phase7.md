# Phase 7: standard custom app connections

## Scope and current status

The first generic slice is implemented in code: workspace owners register a custom app through the UI/API, define bounded event schemas, receive one-time inbound/outbound signing secrets, rotate or revoke those secrets, bind an inbound event type to a published workflow, and add a signed callback action. One generic endpoint accepts CloudEvents 1.0 JSON for any configured app. The event receipt, run, queued event, and Temporal start outbox commit together. Callback activities use a stable event ID, persist attempts/status, verify a fixed HTTPS destination through pinned DNS/TLS, and require the receiving app to deduplicate retries. Two different apps can be configured without editing Automiq code.

This is a first slice. The generic durable wait-for-event node, external correlation/status query for app identities, GitHub PR status/merge adapter, and automatic task-closure workflow from the original example are **not implemented**. A connection definition is immutable at version 1; adding event types currently means creating another connection. Event `data` supports a bounded, flat JSON Schema object subset of `string`, `integer`, `number`, `boolean`, or `null` fields.

## App contract

Owners create `POST /api/v1/workspaces/{ws}/app-connections` with a name, optional fixed HTTPS callback URL, `inbound_schemas`, and `outbound_schemas`. The create/rotate response displays two secrets once. Later list responses show metadata only. Each registered schema uses `type: object`, declared scalar `properties`, `required`, and `additionalProperties: false`. A connection may be inbound-only. An app that needs workflow results must also receive callbacks.

The app sends one CloudEvent to the connection's returned `webhook_url` with `Content-Type: application/cloudevents+json`, `X-Automiq-Timestamp` as Unix seconds, and `X-Automiq-Signature: sha256=<HMAC-SHA-256>`. The HMAC input is the decimal timestamp, a period byte, and the exact UTF-8 request body. Automiq accepts timestamps within five minutes and deduplicates by connection, `source`, and `id`. The `202` response includes the run IDs; it acknowledges durable intake, not workflow completion. Reusing an event ID with changed body returns `409`.

Example synthetic event (replace IDs and values for a real app):

```json
{"specversion":"1.0","id":"event-001","source":"urn:example:task-app","type":"com.example.task.submitted.v1","datacontenttype":"application/json","data":{"task_id":"task-123"}}
```

For callbacks, Automiq sends a CloudEvent to the fixed URL and signs it with the distinct outbound secret and the same timestamp/body scheme. The callback event ID is stable for a run and node. The app should record that ID before returning a 2xx response; duplicate deliveries must not repeat its business mutation. The workspace-scoped callback status endpoint is `GET /api/v1/workspaces/{ws}/app-connections/{id}/callbacks`.

## Reproduce the synthetic E2E

Run from the repository root with a fresh local test database, Docker Desktop, Node.js, Python, uv, and Playwright Chromium. `.env` comes from the sanitized `.env.example`; the preparation script writes a short-lived encryption key to the ignored `.env.app-e2e.local` and a synthetic TLS certificate to ignored `artifacts/e2e/app-connections/tls/`. Do not reuse this E2E key for a real workspace.

```powershell
Copy-Item .env.example .env  # only if .env does not exist
$env:UV_CACHE_DIR=Join-Path (Get-Location) '.uv-cache'
uv run --frozen python scripts/prepare_app_e2e.py
docker compose --env-file .env --env-file .env.app-e2e.local -f infra/compose.yaml -f infra/compose.app-e2e.yaml up -d --build
docker compose --env-file .env --env-file .env.app-e2e.local -f infra/compose.yaml -f infra/compose.app-e2e.yaml exec -T api uv run --frozen --no-dev alembic -c alembic.ini upgrade head
uv run --frozen python scripts/check_app_connections.py
npm.cmd --prefix apps/web exec playwright test --config=playwright.app-connections.config.ts
```

The API E2E creates two synthetic apps through the same UI/API-independent contract, sends signed and forged events, checks deduplication, runs both workflows through Temporal, verifies callback HMACs and retry status, checks cross-workspace isolation, and rotates/revokes credentials. The browser E2E checks connection setup plus light/dark screenshots and no horizontal overflow at 375, 768, 1280, and 1440 pixels. Evidence paths are ignored: `artifacts/e2e/app-connections/report.json`, `callbacks.jsonl`, `playwright.xml`, `browser/`, and browser traces.

## Verification observed on 2026-10-12

- `uv run --frozen ruff check .`: passed after implementation changes.
- `uv run --frozen mypy`: passed for configured API/worker sources.
- `npm.cmd --prefix apps/web run typecheck`, `lint`, and `build`: passed.
- `docker compose ... config --quiet`: passed with the synthetic E2E overlay.
- `uv run --frozen alembic -c alembic.ini upgrade head --sql`: generated offline SQL under ignored evidence.
- `docker compose ... up -d --build`: failed before service startup because the local Docker engine pipe was absent. The API E2E then reported `ConnectError` with zero scenarios in its ignored JSON report. No full-stack behavior, migration application/parity, callback delivery, or browser screenshots have been verified in this environment.

The test profile uses PostgreSQL 16.6, Redis 7.4.2, Temporal 1.27.2, Next.js 16.4.0, and a synthetic HTTPS callback receiver when Docker is available. No real external API is called by the E2E profile.
