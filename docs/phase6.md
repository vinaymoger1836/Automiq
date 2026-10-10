# Phase 6 operations and verification

## Operational slice

The API records fixed-field JSON request events and low-cardinality Prometheus counters/histograms. The worker records run projection events, terminal run and step durations, action retries, agent usage, and approval wait time. Set `OTEL_EXPORTER_OTLP_ENDPOINT` to an operator-managed OTLP/gRPC collector to export spans; an empty value disables export. The API protects `/internal/metrics` with `X-Metrics-Token`; the worker exposes port 9465 only on the internal Compose network. Do not put payloads, issue text, credentials, or unbounded identifiers in metric labels.

Redis enforces a fixed 60-second request window for new manual runs per workspace and signed GitHub deliveries per trigger. A workspace row lock serializes the admission check for queued/running runs across manual, webhook, and schedule paths. Replaying an existing idempotency key or delivery ID does not create another run. A schedule fire at capacity is skipped and logged by trigger ID. Redis failure rejects new requests with 503; existing runs continue. The limits are configured in `.env.example`.

`scripts/cleanup_retention.py` defaults to a read-only dry run. `--apply` scrubs detail for terminal runs older than `RUN_DETAIL_RETENTION_DAYS` (default 30): run input, step outputs, event outputs, and encrypted issue text. It then removes terminal run summaries and dependent rows older than `RUN_SUMMARY_RETENTION_DAYS` (default 90), plus old audit rows. Active runs are never selected. Process at most 500 runs per invocation by default and repeat until `details` is zero. Run this from a trusted scheduler; no periodic cleanup is enabled by Compose. A delivery whose run is removed also loses its dedupe record, so a provider replay after the retention window can create a new run. Temporal history retention is managed separately by the Temporal deployment.

## Local fake-provider check

From the repository root in PowerShell, with Docker Desktop and the ignored local `.env` available:

```powershell
$env:UV_CACHE_DIR=(Join-Path (Get-Location) '.uv-cache')
$env:PYTHONPATH='.;apps/api;services/orchestrator'
uv sync --frozen
docker compose --env-file .env --env-file .env.phase4.local -f infra/compose.yaml -f infra/compose.phase4-e2e.yaml -f infra/compose.phase6-e2e.yaml up -d --build
docker compose --env-file .env --env-file .env.phase4.local -f infra/compose.yaml -f infra/compose.phase4-e2e.yaml -f infra/compose.phase6-e2e.yaml exec -T api uv run --frozen --no-dev alembic upgrade head
docker compose --env-file .env --env-file .env.phase4.local -f infra/compose.yaml -f infra/compose.phase4-e2e.yaml -f infra/compose.phase6-e2e.yaml exec -T api uv run --frozen --no-dev alembic check
uv run --frozen python scripts/check_phase6_operations.py
docker compose --env-file .env --env-file .env.phase4.local -f infra/compose.yaml -f infra/compose.phase4-e2e.yaml -f infra/compose.phase6-e2e.yaml exec -T api uv run --frozen --no-dev python /app/scripts/cleanup_retention.py
```

The overlay uses only synthetic provider credentials and a local metrics token. The E2E creates a unique local workspace and checks run admission, retry headers, metrics access, tenant isolation, detail scrubbing, and summary deletion. It backdates only one synthetic run. Reports and failure evidence are saved under ignored `artifacts/e2e/phase6-operations/`. The direct cleanup command at the end is a dry run; add `--apply` only on a database whose retention policy you have selected.

To verify optional OTLP export locally, add `-f infra/compose.otel-e2e.yaml` to the Compose command above, restart `api` and `worker`, run the operations script, then run `uv run --frozen python scripts/check_phase6_otel.py`. The collector uses a detailed debug exporter only in this E2E overlay. The check saves service/span counts and shared-trace counts without saving trace contents under ignored `artifacts/e2e/phase6-otel/`. It verifies a common trace ID across API admission, outbox start, run load, and projection. Running the Phase 4 integration suite first also verifies a schedule-fire trace through the same boundary; running Phase 5 adds agent and provider action spans.

## Recovery, load, browser, and backup checks

Use the normal fake-provider overlay for these checks; the operations overlay intentionally lowers admission limits and would skew the load run. The failure script stops and restarts services in this local Compose project only. It restores each service in a `finally` block, but do not run it against shared infrastructure.

```powershell
docker compose --env-file .env --env-file .env.phase4.local -f infra/compose.yaml -f infra/compose.phase4-e2e.yaml up -d --build
$env:PHASE5_RESTART_WORKER='1'
uv run --frozen python scripts/check_phase5_agent_approvals.py
Remove-Item Env:\PHASE5_RESTART_WORKER
uv run --frozen python scripts/bench_phase6.py
$env:PHASE6_ALLOW_FAULTS='1'
uv run --frozen python scripts/check_phase6_failures.py
Remove-Item Env:\PHASE6_ALLOW_FAULTS
$env:PHASE6_ALLOW_BACKUP='1'
uv run --frozen python scripts/check_phase6_backup.py
Remove-Item Env:\PHASE6_ALLOW_BACKUP
Push-Location apps/web
node_modules\.bin\playwright.cmd test --config=playwright.phase6.config.ts
Pop-Location
```

The bounded benchmark uses 100 authenticated workflow-list reads, 20 simultaneous manual run requests, and 20 simultaneous signed GitHub deliveries. It waits for all accepted runs to finish and records observed p95 request latency and completed throughput. It is a local synthetic baseline, not a production capacity claim. The failure matrix covers API, worker, Temporal, and PostgreSQL interruption and recovery; the Phase 4 fake-provider suite covers external 429 and permanent errors. The backup drill creates an in-container dump, restores to a temporary database, compares a run fingerprint, and removes both temporary artifacts. Evidence is under ignored `artifacts/e2e/phase6-load/`, `phase6-failures/`, `phase6-backup/`, and `phase6-browser/`.

The [threat review](threat-model.md) records trust boundaries, controls, and remaining deployment decisions. For dependency review, run `npm.cmd audit --prefix apps/web --json` and `uv export --frozen --no-dev --format requirements-txt` followed by `pip-audit`; keep machine-readable reports under ignored `artifacts/e2e/phase6-security/`.

## Observed local results (2026-10-10)

- Operations: 30 synthetic full-stack scenarios passed, including signed-webhook quota and duplicate replay above quota.
- Telemetry: the local OpenTelemetry Collector 0.162.0 received API and worker resources. The sanitized report records 19 shared API-to-worker execution traces and one shared schedule-to-worker execution trace, plus webhook, agent, provider action, and projection spans. It also counts 133 fixed-field JSON events with trace IDs, including 40 with run IDs. These are local synthetic observations; no distributed tracing backend or dashboard was deployed.
- Browser: one Chromium starter-template publish/run scenario passed. Light and dark screenshots at 375px and 1280px, plus overflow checks at 375px, 768px, 1280px, and 1440px, are under ignored `artifacts/e2e/phase6-browser/`.
- Phase 5 regression: 47 full-stack fake-provider scenarios passed, including worker restart.
- Phase 4 integration regression: 42 fake-provider scenarios passed, including a timed schedule and provider failure paths, after the trace-context migration.
- Failure matrix: 19 scenarios passed after enabling SQLAlchemy `pool_pre_ping`. The first run exposed a stale pooled connection after PostgreSQL restart; its failure report is retained.
- Backup/restore: three scenarios passed against PostgreSQL 16.6.
- Load: 100 reads, 20 manual runs, and 20 signed webhooks completed with zero failures at concurrency 20 on a Windows host with 12 logical CPUs. Read p95 was 441.5 ms; manual acceptance p95 310.5 ms with 0.50 completed runs/s; signed webhook acceptance p95 613.4 ms with 0.49 completed runs/s. The worker's current sequential outbox polling limits observed throughput. The report records settings and environment.
- Advisory scans: npm reported zero vulnerabilities; pip-audit reported none across 74 locked Python packages. A manual search of changed source/docs found no credential-shaped strings. These scans are point-in-time and do not replace deployment review.

## Deployment notes

Keep the metrics token in a secret manager. Rotate it independently of session and integration keys. Scrape the worker only from a trusted internal network. An OTLP collector and Prometheus/Grafana deployment are optional and require operator configuration. The local Phase 6 gate is complete with synthetic evidence; a production deployment still needs network egress policy, external OIDC validation, an offsite backup restore drill, and live-provider verification before any production claim. Existing provider side effects retain their documented at-least-once caveat.
