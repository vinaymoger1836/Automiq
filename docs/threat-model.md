# MVP threat review (Phase 6)

## Assets and trust boundaries

The protected assets are workspace membership, versioned workflow definitions, execution history, encrypted integration credentials, encrypted issue content, approvals, and external effects. The browser crosses into the FastAPI control plane using a signed session and CSRF token. A GitHub webhook crosses a public boundary with an opaque trigger ID and raw-body HMAC. FastAPI persists business state in PostgreSQL and queues Temporal starts. The worker crosses a second boundary when it resolves credentials, calls an LLM or approved provider, and projects sanitized results back to PostgreSQL. The Next.js UI is not an authorization boundary.

## Reviewed threats and controls

| Threat | Current control | Verification and residual risk |
| --- | --- | --- |
| Cross-workspace read or mutation | API membership check and workspace-scoped queries; worker integration resolution uses the run's workspace | Phase 1/4/5 E2E plus Phase 6 foreign-run read denial. A broader route-by-route production review remains useful. |
| Forged or replayed webhook | Raw-body SHA-256 HMAC, 64 KB cap, trigger/delivery uniqueness and payload hash check | Phase 4 fake-provider E2E; Phase 6 limits accepted deliveries. Invalid signatures still consume API work before admission limiting; production edge rate limits remain necessary. |
| Secret or untrusted issue text disclosure | Encrypted credential and issue rows, worker-only decryption, redacted response models, bounded agent input, no raw prompt logs | Phase 4/5 E2E. The operator must protect the encryption key and rotate it when compromised. |
| SSRF and unsafe external action | Fixed provider origins, pinned DNS/IP validation, TLS host verification, no redirects, typed relative-path HTTPS connector | Phase 2/4 fake-provider checks. Production network egress policy remains required. |
| Agent tool escalation and prompt injection | Published tool allowlist, read-only search tool, bounded calls/tokens/cost, schema validation, approval before agent-influenced external actions | Phase 5 full-stack fake-model E2E passed. Real model behavior and live provider exchange have not been exercised. |
| Duplicate or uncertain side effect | Outbox, stable run/effect keys, idempotent projections, connector reconciliation or fail-closed ambiguous result | Phase 2/4/5 E2E. External providers can still duplicate effects after an unknown response; no exactly-once claim. |
| Resource exhaustion | Graph/body bounds, Redis ingress windows, workspace active-run cap, Temporal activity deadlines and bounded action retries | Phase 6 operations E2E and load report. Workspace creation and invalid-signature floods need deployment edge controls. |
| Operational disclosure | Protected API metrics token, no payload metric labels, fixed-field JSON events, local ignored evidence | Worker metrics port is internal to Compose but has no application-layer authentication; restrict its network in deployment. Traces require a trusted collector. |
| Data accumulation and backups | Dry-run-first detail/summary cleanup; backup/restore drill | Temporal history has separate retention. Restored databases must remain isolated and encrypted at rest. |

## Deployment decisions still required

Use HTTPS OIDC and a unique session secret, restrict production egress and metrics scrape networks, define collector access and retention, schedule cleanup, encrypt and test offsite backups, and verify live provider behavior with approved credentials. The local fake-provider evidence does not establish production readiness or a completed privacy assessment.
