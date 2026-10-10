# Measured project highlights

Use these only with their test setting attached; the results are from a local synthetic Compose run on 2026-10-10, not a production deployment.

- Built a versioned Next.js/FastAPI/Temporal workflow engine with signed webhooks, bounded AI classification, durable approvals, and workspace-scoped authorization; verified the agent/approval path with 47 full-stack fake-provider scenarios, including a worker restart.
- Added Redis ingress limits and serialized workspace run admission; verified quota, signed-webhook replay, metrics access, tenant isolation, and retention with 30 full-stack operations scenarios.
- Measured 100 authenticated reads and 20 concurrent manual runs plus 20 signed webhooks with zero failures on a 12-logical-CPU Windows host. Observed p95 latency was 441.5 ms for reads, 310.5 ms for manual run acceptance, and 613.4 ms for webhook acceptance; completed throughput was 0.50 and 0.49 runs/s respectively. The worker's sequential outbox polling was the limiting behavior in this run.
- Demonstrated recovery across API, worker, Temporal, and PostgreSQL interruptions in 19 local failure scenarios, and verified a PostgreSQL 16.6 dump/restore in an isolated temporary database.

Source reports: ignored `artifacts/e2e/phase5-agent-approvals/report.json`, `phase6-operations/report.json`, `phase6-load/report.json`, `phase6-failures/report.json`, and `phase6-backup/report.json`. Re-run commands and environment details are in [the Phase 6 guide](phase6.md).
