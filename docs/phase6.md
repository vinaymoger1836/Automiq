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
docker compose --env-file .env --env-file .env.phase4.local -f infra/compose.yaml -f infra/compose.phase4-e2e.yaml -f infra/compose.phase6-e2e.yaml exec -T api uv run --frozen --no-dev alembic check
uv run --frozen python scripts/check_phase6_operations.py
docker compose --env-file .env --env-file .env.phase4.local -f infra/compose.yaml -f infra/compose.phase4-e2e.yaml -f infra/compose.phase6-e2e.yaml exec -T api uv run --frozen --no-dev python /app/scripts/cleanup_retention.py
```

The overlay uses only synthetic provider credentials and a local metrics token. The E2E creates a unique local workspace and checks run admission, retry headers, metrics access, tenant isolation, detail scrubbing, and summary deletion. It backdates only one synthetic run. Reports and failure evidence are saved under ignored `artifacts/e2e/phase6-operations/`. The direct cleanup command at the end is a dry run; add `--apply` only on a database whose retention policy you have selected.

## Deployment notes and open gate items

Keep the metrics token in a secret manager. Rotate it independently of session and integration keys. Scrape the worker only from a trusted internal network. An OTLP collector and Prometheus/Grafana deployment are optional and require operator configuration. A production deployment still needs network egress policy, external OIDC validation, backup restore drills, threat review, load measurements, and a broader failure matrix before any production claim. Existing provider side effects retain their documented at-least-once caveat.
