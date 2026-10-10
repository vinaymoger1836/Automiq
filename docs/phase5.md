# Phase 5: bounded agents and approvals

## What changed

The versioned graph now accepts `agent` and `approval` nodes. An agent has a server-validated provider profile, instructions, read-only tool allowlist, input/output contracts, tool-call and duration limits, token limits, and a cost budget. Phase 5 fixes the output contract to `severity` (`critical` or `normal`) plus `reason`; generated reason text is replaced with a generic summary before projection. The only agent tool is workspace-scoped `github.search_issues`. Unknown tools and calls beyond the configured quota fail the run. A graph with an agent-influenced GitHub comment or Slack message must place an approval checkpoint after the agent and before the external action on that path.

LangGraph runs inside a bounded Temporal activity. The fake model is deterministic in development/test; the configurable OpenAI Responses adapter uses a fixed HTTPS endpoint, structured output, function calling, `store: false`, and operator-provided model, key, and USD-per-million-token rates. Real calls were not made during E2E. The worker does not put the model key, raw webhook issue text, assembled model requests, or tool results into Temporal history or run events. Published node instructions remain part of the versioned graph. Signed GitHub issue title and body are capped and encrypted in a separate PostgreSQL row. Only the agent activity decrypts them. Manual trigger input with secret-like field names is rejected before Temporal starts. Run output contains severity, a generic reason, token counts, cost estimate, and a tool name/result-count summary. Input fields with secret-like names are removed before model use; each string is capped at 1,000 characters. The fake E2E includes a body that asks for an unauthorized tool and credential disclosure.

Approval requests persist with a unique run/node key, deadline, status, actor, and signal-delivery marker. The owner-only decision endpoint commits a decision and audit event before signaling Temporal. The worker retries undelivered signals; the workflow reads the database status after a signal and fails closed on rejection or timeout. Repeating the same decision returns the recorded result; an opposite decision conflicts. Editors and viewers can read the inbox but cannot decide. The current UI exposes the node settings and inbox with pending, approved, rejected, and expired states.

## Local fake-provider verification

From the repository root on Windows PowerShell, with Docker Desktop available:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
$env:UV_CACHE_DIR = (Join-Path (Get-Location) '.uv-cache')
uv sync --frozen
npm.cmd ci --prefix apps/web --no-audit --no-fund
$keyBytes = New-Object byte[] 32
$keyGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$keyGenerator.GetBytes($keyBytes)
$keyGenerator.Dispose()
$keyText = [Convert]::ToBase64String($keyBytes)
[System.IO.File]::WriteAllText((Join-Path (Get-Location) '.env.phase4.local'), "INTEGRATION_ENCRYPTION_KEY=$keyText`n", [System.Text.UTF8Encoding]::new($false))
docker compose --env-file .env --env-file .env.phase4.local -f infra/compose.yaml -f infra/compose.phase4-e2e.yaml up -d --build
docker compose --env-file .env --env-file .env.phase4.local -f infra/compose.yaml -f infra/compose.phase4-e2e.yaml exec -T api uv run --frozen --no-dev alembic upgrade head
docker compose --env-file .env --env-file .env.phase4.local -f infra/compose.yaml -f infra/compose.phase4-e2e.yaml exec -T api uv run --frozen --no-dev alembic check
$env:PHASE5_RESTART_WORKER = '1'
uv run --frozen python scripts/check_phase5_agent_approvals.py
Push-Location apps/web
node_modules\.bin\playwright.cmd test --config=playwright.phase5.config.ts
Pop-Location
```

The ignored `.env.phase4.local` uses a fresh synthetic key so the API and worker can share encrypted fake integration and issue content. The E2E script creates its own synthetic workspace/integrations/workflows and restarts the local worker while an approval is pending. Never use a live provider key in this profile. The script writes sanitized pass/fail JSON to ignored `artifacts/e2e/phase5-agent-approvals/`; Playwright writes JUnit, traces on first retry, and light/dark mobile/desktop screenshots to ignored `artifacts/e2e/phase5-browser/`.

## Verification completed 2026-10-10

- Compose rebuilt the API, worker, and web images with PostgreSQL 16.6, Redis 7.4.2, and Temporal 1.27.2. Alembic reached `0005_phase5_approvals`; `alembic check` found no new operations.
- The full-stack API/PostgreSQL/Temporal fake-provider script passed **47 scenarios**: signed issue intake, authorized read-only GitHub search, severity classification, encrypted issue input absent from run projection, prompt-injection text withheld, secret-like manual input rejection, approval gating, editor/viewer denial, worker restart during pending approval, owner decision and audit, duplicate decision idempotency, conflicting decision rejection, Slack effect after approval, normal issue path without approval or Slack action, unknown tool denial, tool quota, malformed model output, and approval timeout.
- Chromium passed **1 browser scenario**: the owner added and configured agent/approval nodes, published and ran the workflow, then approved from the inbox. Light and dark themes had no horizontal page overflow at 375, 768, 1280, and 1440 pixels. Screenshots at 375 and 1280 pixels in both themes were inspected.
- Ruff, mypy, TypeScript, ESLint, and the web production build passed. Graph schema export matches the Pydantic model.

Evidence: `artifacts/e2e/phase5-agent-approvals/report.json`, `artifacts/e2e/phase5-browser/playwright.xml`, and four ignored browser PNGs. Host tools were Python 3.12.10, uv 0.12.23, Node 24.15.0, npm 11.12.1, Playwright 1.64.0, and Compose 5.3.1; the Docker engine was 29.6.2 in the preceding Phase 4 gate. No live OpenAI response, paid usage, or real GitHub/Slack action was exercised. The real adapter's model pricing must be configured by the operator and kept current; the preflight cost bound assumes those rates are correct, while actual usage is checked after every response. The system does not claim exactly-once external effects.
