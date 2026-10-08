# ADR-004: PostgreSQL projection and resumable SSE

Status: Planned by `architecture.md`; implementation deferred to Phase 2.

Temporal will own orchestration state; PostgreSQL will serve queryable run events. SSE will resume from persisted event IDs.
