# ADR-005: Credentials, tenancy, and idempotency

Status: Planned by `architecture.md`; implementation deferred to Phases 1–4.

Future APIs will scope every resource by workspace. Connector credentials will be encrypted references, and external effects will use idempotency or documented reconciliation. Phase 0 stores no connector credentials or user data.
