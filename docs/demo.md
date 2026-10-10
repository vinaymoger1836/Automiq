# Local demo outline

The demo uses only local synthetic identities, a fake model, and fake GitHub/Slack adapters. Start Compose with the Phase 4 fake-provider overlay and an ignored, randomly generated integration encryption key as described in [Phase 5 setup](phase5.md). Apply Alembic migrations before opening `/studio`.

## Two-minute walkthrough

1. Sign in as the local owner, create a workspace, name a workflow, and choose **Manual → mock action → end** under **Starting point**. Publish it, start a run, and show the three completed steps and timeline. The starter is a working graph that can be edited before publishing.
2. Run `uv run --frozen python scripts/check_phase5_agent_approvals.py` against the fake-provider stack. It creates a synthetic issue workflow with bounded classification, a condition, approval, and notification; it also verifies failure and recovery cases. Select its generated workspace in the studio.
3. Show the published version, the execution timeline, the approval inbox, sanitized agent usage, and the completed action after approval. Explain that published runs remain pinned to a version and that external effects use provider-specific reconciliation, with documented duplicate risk.
4. Use the synthetic issue examples in `tests/fixtures/github-issue-critical.json` and `github-issue-normal.json` when narrating the critical and normal branches. The E2E script signs its own local payloads with an in-memory synthetic secret; do not paste a real webhook secret into a recording.
5. Show the ignored Phase 6 reports for recovery and measured local throughput. State the host, concurrency, fake-provider profile, and test limits next to any number. Avoid turning a local baseline into a production performance claim.

## Short recording outline

- **0:00–0:20:** Workspace, starter template, and visual editor.
- **0:20–0:45:** Publish, manual run, and inspect the timeline.
- **0:45–1:20:** Synthetic GitHub issue, bounded agent decision, and owner approval.
- **1:20–1:45:** Version pinning, retry/recovery evidence, and sanitized audit.
- **1:45–2:00:** Local benchmark settings and architectural boundaries.

The component diagram is in [architecture.md](../architecture.md); decision records are in [docs/adr](adr), and the [Phase 6 guide](phase6.md) has exact verification commands. All reports and browser screenshots belong under Git-ignored `artifacts/e2e/`.
