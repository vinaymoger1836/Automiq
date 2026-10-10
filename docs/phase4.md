# Phase 4: webhooks, schedules, and integrations

## Local operation

Create `.env` from `.env.example` if it does not already exist. Its values are local placeholders. Generate a fresh 32-byte base64 integration key without printing it. The ignored `.env.phase4.local` file keeps the same local test key available to both Compose services across commands. The Phase 4 overlay enables deterministic fake providers.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
$env:UV_CACHE_DIR = (Join-Path (Get-Location) '.uv-cache')
uv sync --frozen
npm.cmd --prefix apps/web ci
$keyBytes = New-Object byte[] 32
$keyGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$keyGenerator.GetBytes($keyBytes)
$keyGenerator.Dispose()
$keyText = [Convert]::ToBase64String($keyBytes)
[System.IO.File]::WriteAllText((Join-Path (Get-Location) '.env.phase4.local'), "INTEGRATION_ENCRYPTION_KEY=$keyText`n", [System.Text.UTF8Encoding]::new($false))
docker compose --env-file .env --env-file .env.phase4.local -f infra/compose.yaml -f infra/compose.phase4-e2e.yaml up -d --build
docker compose --env-file .env --env-file .env.phase4.local -f infra/compose.yaml -f infra/compose.phase4-e2e.yaml exec -T api uv run --frozen --no-dev alembic upgrade head
docker compose --env-file .env --env-file .env.phase4.local -f infra/compose.yaml -f infra/compose.phase4-e2e.yaml exec -T api uv run --frozen --no-dev alembic check
uv run --frozen python scripts/check_phase4_integrations.py
Push-Location apps/web
npx.cmd playwright install chromium
npx.cmd playwright test --config=playwright.phase4.config.ts
Pop-Location
```

The API check writes a sanitized JSON report to ignored `artifacts/e2e/phase4-integrations/report.json`. The browser check writes JUnit and light/dark screenshots at 375 and 1280 pixels to ignored `artifacts/e2e/phase4-browser/`. Both checks use synthetic local identities and fake provider tokens. The API check waits for a one-minute UTC schedule to fire, so it can take about two minutes. Run the browser check from `apps/web`; the Playwright config resolves the evidence directory from there. Use a fresh local test database or clean up old synthetic workspaces after failed runs, as an interrupted schedule check may leave its schedule enabled.

Set `INTEGRATION_PROVIDER_MODE=real` only when using approved live GitHub and Slack credentials. The worker uses fixed HTTPS origins (`api.github.com`, `slack.com`), DNS address validation, TLS verification for the named host, no proxy, no redirects, response limits, and bounded timeouts. The existing generic HTTPS connector remains separately disabled by default. Production also needs a network egress policy.

## Trigger and credential behavior

- A GitHub issue trigger binds to one immutable published version. Configure a GitHub integration first, publish a graph with `trigger.github_issue`, then create its trigger binding. The returned webhook path accepts signed `issues` events for that integration's exact repository. Phase 5 keeps only action, repository, issue number, and state in the Temporal run input; a bounded title and body are encrypted in a separate workspace-scoped row for the agent activity.
- The webhook verifies `X-Hub-Signature-256` against raw bytes before parsing, caps the request at 64 KB, and deduplicates by trigger and `X-GitHub-Delivery`. A repeated ID with changed bytes is rejected. Run, delivery, outbox, and queued event commit atomically.
- Credentials use a random per-integration data key encrypted by a configured master key. AES-GCM binds both encrypted layers to workspace, integration ID, and provider. `key_version` identifies the master key; `credential_version` increments on credential replacement. API responses expose metadata only. Revocation blocks later webhook intake and worker credential resolution.
- Owners create, revoke, and assign integrations. An editor can replace credentials only for an integration explicitly assigned to that editor in the same workspace. Viewers see metadata without secrets and cannot configure integrations.
- For master-key rotation, set `INTEGRATION_KEYRING` to a secret JSON map of version numbers to base64 keys, set `INTEGRATION_ACTIVE_KEY_VERSION` to the new version, and call the owner-only `POST /api/v1/workspaces/{ws}/integrations/{id}/rewrap` route for each integration. Keep the previous key available until every row reports the new `key_version`. Keep the keyring in a secret manager or ignored environment file, never in source control.
- A Temporal Schedule is registered asynchronously after the trigger transaction. Each schedule fire starts a small Temporal workflow which creates one business run and outbox row, pinned to the trigger's published version. It uses the scheduled workflow ID as the dedupe key. Disable stops future starts; a fire already accepted may still finish. The schedule uses the supplied IANA timezone and a five-field cron expression. Use UTC when wall-clock behavior across daylight-saving transitions is undesirable. Temporal owns calendar interpretation and a ten-minute catch-up window.
- GitHub comments and Slack messages use a durable effect ledger. A documented 429 response permits a capped retry. A lost or uncertain mutation response stops automatic replay and leaves an audit record for manual reconciliation. Remote duplicate side effects remain possible if the provider applies a mutation before the worker can record success; the system does not claim exactly-once external delivery.

## Verification completed 2026-10-10

The Phase 4 Compose stack built and started after Docker Desktop became available. The migration upgraded to `0004_phase4_integrations (head)` and `alembic check` reported no new operations. The API E2E command above passed **42 scenarios**: signed and invalid GitHub deliveries, payload limits, trigger-scoped deduplication, fake GitHub/Slack actions with first-attempt rate limits, audit records, workspace permissions, credential rotation/revocation, and a UTC Temporal Schedule firing a version-pinned run. The fake read-only GitHub search tool was also exercised in the worker against a synthetic run and wrote its audit record. A completed fake GitHub effect was replayed through the worker after credential revocation and returned its recorded result without a new provider call. The browser command above passed **1 Chromium scenario**: owner configures integrations and a workflow, publishes, sends a signed webhook, and sees the completed run. It checked no horizontal overflow at 375, 768, 1280, and 1440 pixels in both themes. Four light/dark mobile/desktop screenshots were reviewed.

Evidence: ignored `artifacts/e2e/phase4-integrations/report.json`, `artifacts/e2e/phase4-browser/playwright.xml`, and four PNGs in the browser evidence directory. The prior Phase 2 metadata-range DNS rejection report is `artifacts/e2e/phase2-http/blocked.json`; Phase 4 also rejects arbitrary repository URLs and connector destination fields through the full API path. Provider actions use fixed HTTPS hosts and the same pinned-address guard. No live GitHub or Slack credential exchange, production network egress policy, or real-world DST transition was tested.

Environment: Python 3.12.10 for the host E2E client, Node 24.15.0, npm 11.12.1, Playwright 1.64.0 with Chromium, uv 0.12.23, Docker Engine 29.6.2, Compose 5.3.1, PostgreSQL 16.6, Redis 7.4.2, Temporal 1.27.2, fake providers enabled, and UTC for the schedule scenario. Python Ruff and mypy, frontend lint and production build, OpenAPI route presence, and graph JSON schema parity also passed. A previous failed E2E run left one synthetic schedule enabled; it was disabled through the authenticated API after the successful run.
