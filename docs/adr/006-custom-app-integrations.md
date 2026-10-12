# ADR-006: Custom application integration contract

Status: Proposed. This document does not authorize implementation or change the current architecture.

## Context

Automiq currently has provider-specific GitHub issue webhooks, GitHub and Slack actions, and a restricted HTTPS GET action. A custom task app needs to submit a task-to-PR link, receive the current PR status, and be notified after the PR merges. Requiring a new hard-coded connector for every custom application would prevent users from connecting their own apps.

## Proposed decision

Add a versioned, workspace-scoped **app connection** contract. A standards-compliant custom app must be connectable through Automiq's UI/API configuration, with **zero Automiq source-code changes, deployments, or per-app adapters**. This is an acceptance requirement, not merely an implementation preference. The contract has two independent capabilities:

1. **Inbound events:** A connected app sends signed CloudEvents 1.0 JSON over HTTPS to one Automiq endpoint for that connection. The envelope provides `id`, `source`, `type`, `subject`, and `data`; a schema registered through the connection UI/API validates `data`. Automiq authenticates the sender, enforces body and rate limits, rejects replay, deduplicates by connection plus `source` plus `id`, and atomically records the event and run-start outbox item before returning `202`. A repeated event returns the existing result.
2. **Outbound events and actions:** A workspace owner configures one approved HTTPS callback endpoint and credentials. Automiq sends signed CloudEvents with configured event types and schema-validated data to that endpoint. Notifications are one-way; an action request can include an operation name and correlation ID, and the connected app can send a standard result event back. The app implements its own business behavior. Workflow authors select a registered event/action type and map typed fields; they cannot supply an arbitrary destination URL, header, or secret in a graph. Worker activities deliver with bounded time, payload, and retries, and store sanitized results and audit records.

The control plane manages connection definitions, grants, subscriptions, validation, and status reads. The execution plane performs outbound requests in Temporal activities. PostgreSQL stores connection metadata, encrypted credential references, event receipts, delivery attempts, and correlation records. Existing GitHub and Slack adapters can later implement the same operation interface; their current behavior remains supported during migration.

An app that only sends standard events can trigger workflows. An app that must be updated by a workflow, as in the task-close example, must also receive standard signed callback events at its configured endpoint. Neither case requires app-specific Automiq code.

Use a small, versioned connection definition stored in PostgreSQL and managed through the UI/API to declare event/action types, JSON schemas, permissions, and the fixed callback endpoint. Adding an app or a new event type is configuration, not a source-code edit or deployment. A published workflow pins the definition version. A connection is activated only after origin validation and the configured deployment policy authorizes that origin. No user-supplied executable connector code is accepted. Private, loopback, link-local, metadata, and redirect destinations remain blocked; production deployments also need network egress policy.

Add generic event routing and a durable **wait for event** step. A workflow can subscribe to a declared event type using validated correlation fields from its input. Subscriptions and received events are persisted and matched idempotently, including events arriving while a status check or worker restart is in progress. These mechanisms cannot contain task-app-specific field names or branches.

Events and callbacks are **at least once**. The receiving app must deduplicate callback IDs and make task updates idempotent. For writes with an uncertain remote outcome, Automiq reconciles through a status read when available or records an operator-visible uncertain result; it does not claim exactly-once external effects.

## Proposed wire contract

- `POST /api/v1/app-connections/{public_id}/events` accepts one signed CloudEvent and returns `202` with `event_id`, `correlation_id`, and `status: accepted`. This acknowledges durable intake, not completion of a GitHub lookup or workflow.
- `GET /api/v1/workspaces/{ws}/app-correlations/{correlation_id}` returns the current normalized status and callback-delivery status to an authorized app identity or workspace member. The first status can be `pending` while the worker checks GitHub.
- Automiq delivers signed CloudEvents to a connection's declared callback path. The receiver returns 2xx only after recording the event; non-2xx responses are retried within a bounded policy. HMAC-SHA-256 signs the raw body and timestamp. A distinct secret is used for each direction, with a key identifier in the signature headers.
- The connection definition fixes event/action types, JSON schemas, the callback endpoint, and capability grants. Inputs are mapped from typed workflow fields, and action-result events are schema-checked before becoming run data. Its version is validated at publication.

For the task app, the inbound event contains `task_id`, `repository`, and `pr_number`. Automiq sends an initial `task.pr_status.v1` callback after checking GitHub, then another callback if the PR reaches `merged` or `closed_unmerged`. The task app can also read the correlation status if it needs to refresh its UI. A merge callback includes the task ID and an event ID so the close operation can be idempotent.

## Task app example

1. The task app submits `task_id`, `repository`, and `pr_number` as a signed `task.pr_evidence_submitted.v1` event. A PR URL can be parsed into the latter two fields. The task app records the evidence locally.
2. A configured workflow correlates the task event with the exact GitHub repository/PR, checks current PR state so an already-merged PR is handled, and waits for a matching PR outcome if it is still open. A one-time GitHub PR adapter verifies GitHub's webhook and normalizes its closed/merged outcome for all workflows. The GitHub webhook is configured once per repository or installation, not once per task. No task-app connector code is added to Automiq.
3. On a verified merge, Automiq sends a signed `task.pr_status.v1` callback with `status: merged`, task ID, repository, PR number, and a stable event ID. The task app closes the task automatically. A PR closed without merging sends `status: closed_unmerged` and leaves the task open.
4. The task app acknowledges the callback with a 2xx response and applies it idempotently. It can query correlation and delivery status if a callback is delayed. If Automiq misses a GitHub delivery, an authorized reconciliation read checks the PR state and emits the same terminal outcome once.

Any merged PR linked to the task is sufficient for automatic closure. Docs, files, and other URL evidence continue through creator review in the task app.

## Boundaries and rollout

- **First slice:** signed custom-app event intake, registered outbound event/callback, connection UI/API, fake app provider, and full-stack E2E from event to callback, including duplicate and retry behavior.
- **Second slice:** generic durable event correlation/wait, GitHub PR status/merge adapter, already-merged submission handling, and recovery after missed webhooks or worker restart. The task app flow must be built entirely through connection/workflow configuration after these platform capabilities exist.
- **Versioning:** published workflow versions pin event and action contract versions. A changed connection definition creates a new version; old runs remain interpretable.
- **Security:** workspace-scoped authorization, least-privilege grants, secret rotation/revocation, payload redaction, signature verification, replay windows, bounded schemas, and destination validation are required before activation.
- **Compatibility:** add new APIs and node types alongside the existing provider-specific contracts. Do not reinterpret existing published graphs or migrate them implicitly.

## Tradeoffs and open decisions

- A fixed event protocol makes standards-compliant custom apps self-service without running third-party code inside Automiq. Existing providers with their own protocols, such as GitHub, need one reusable provider adapter; connecting another custom app does not.
- Owner-approved origins improve self-service but increase outbound risk. The deployment policy must decide whether an operator must also approve each origin in production.
- CloudEvents standardizes event identity and metadata; it does not itself provide authentication, delivery, or business schemas, which Automiq must define.

## Acceptance evidence required before adoption

Full-stack E2E must connect two different synthetic custom apps through the public UI/API with no Automiq code change or service rebuild between them. A synthetic task app and fake GitHub provider must cover: signed and forged event intake; duplicate and conflicting event IDs; cross-workspace isolation; approved and blocked origins; already-merged PR; open-to-merged PR; closed-without-merge PR; callback retry and deduplication; credential rotation/revocation; API/worker restart; and sanitized audit/delivery status. Browser checks must include light/dark and mobile/desktop views for connection setup and status.

## References

- CloudEvents 1.0.2 specification: https://github.com/cloudevents/spec/tree/v1.0.2/cloudevents
- Existing Automiq integration rules: `architecture.md` section 9
