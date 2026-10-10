# Deployment guide and API examples

This is an operator guide for adapting the local Compose topology; the repository has no verified production deployment. The [architecture diagram](../architecture.md#2-high-level-architecture), [ADRs](adr), [threat review](threat-model.md), and [Phase 6 measurements](phase6.md) describe the boundaries and observed behavior.

## Local setup

Use the root [README](../README.md#start-locally) to install Node 22+, Python 3.12, uv, and Docker Compose; copy the sanitized `.env.example` to ignored `.env`; start Compose; and apply Alembic migrations. The [Phase 4 guide](phase4.md) describes the ignored fake-provider configuration. Use that profile for the demo and E2E suite. Never use the synthetic identity endpoint in a public deployment.

## Deployment decisions

1. Provide PostgreSQL, Redis, Temporal, API, worker, and web services on private networks. Allow the public ingress to reach only the web app and verified API/webhook routes. Limit worker outbound traffic to approved provider hosts and the configured model endpoint.
2. Configure external OIDC, HTTPS, unique session and encryption keys, webhook secrets, and the metrics scrape token through a secret manager. Disable development identities. Set the application origin and public API URL to the deployed HTTPS names. Use distinct database and Temporal namespaces for each environment.
3. Apply Alembic migrations before starting traffic. Keep at least one worker active. Start new runs through the database outbox and retain its reconciler; monitor queued age and failed starts. The worker's current one-row outbox polling is a throughput limit in the local benchmark.
4. Restrict `/internal/metrics` to the protected scraper token and the worker's port 9465 to an internal network. Point `OTEL_EXPORTER_OTLP_ENDPOINT` to a trusted collector if tracing is enabled. Configure collector access and retention. Run trace context is persisted in PostgreSQL and reconnected at outbox start and activity spans.
5. Schedule `scripts/cleanup_retention.py --apply` only after reviewing its dry run and deciding the actual retention policy. Back up PostgreSQL and Temporal separately; encrypt backups, isolate restore drills, and define an offsite recovery objective. The local restore evidence covers PostgreSQL only.
6. Run the full synthetic E2E and security checks described in [Phase 6](phase6.md) after deployment configuration changes. Perform a privacy assessment, production network review, live OIDC and provider checks with approved credentials, and a capacity test before a production claim.

## API examples

The browser sends a signed session cookie and CSRF token. The local E2E scripts are executable examples of the complete API sequence and use only synthetic identities. The calls below show the manual-run contract with placeholders; `{workspace_id}` and `{workflow_id}` are returned by prior authorized create/list calls. A workflow must already have a published version containing a manual trigger.

```http
POST /api/v1/workspaces/{workspace_id}/workflows/{workflow_id}/runs HTTP/1.1
Content-Type: application/json
Origin: https://your-web-origin.example
Cookie: automiq_session=<session-cookie>
X-CSRF-Token: <session-csrf-token>
Idempotency-Key: example-run-001

{"payload":{"flag":true}}
```

Success returns HTTP 202 with `run_id`, `status`, and `version_id`. Repeating the key with the same input returns the existing run; changing the input returns 409. New-run quota or active-run capacity returns 429 with `Retry-After` where applicable. Read `GET /api/v1/workspaces/{workspace_id}/runs/{run_id}` and resume its SSE stream at `/events` using `Last-Event-ID`. The API checks workspace membership for both endpoints. The full route and error envelope contract is in [architecture.md](../architecture.md#8-api-surface-first-cut).

```http
GET /api/v1/workspaces/{workspace_id}/runs/{run_id} HTTP/1.1
Cookie: automiq_session=<session-cookie>
```

Webhook consumers send raw JSON plus a provider HMAC signature and delivery ID. The verified local signing example is in `scripts/check_phase6_operations.py`; do not use a browser session or real secret in a demo recording.
