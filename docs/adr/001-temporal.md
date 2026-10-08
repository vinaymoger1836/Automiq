# ADR-001: Temporal as the single durable orchestrator

Status: Accepted from `architecture.md`; Phase 0 records the decision.

Temporal owns future run timing, retries, and signals. Redis is auxiliary only. Phase 0 registers a tiny workflow and verifies its worker with a PostgreSQL activity. No workflow domain behavior is implemented yet.
