# Connect Zoho Projects

Automiq includes a Zoho Projects task-status action alongside its GitHub and Slack actions. A workspace owner connects one Zoho Projects portal and project. A workflow with a manual or signed custom-app event trigger can then update the status of a numeric `task_id` in that project. The current connector supports the US (`.com`), EU (`.eu`), and India (`.in`) Zoho data centers.

## Set up the integration

1. In Zoho, register an OAuth client and obtain an offline refresh token for the correct data center and account. Grant at least `ZohoProjects.tasks.UPDATE` for the account that may update the intended project. Zoho's [OAuth documentation](https://www.zoho.com/developer/oauth/web-server-apps/refresh-access-token.html) explains refresh tokens and data-center-specific Accounts URLs. Keep the client secret and refresh token in a secret manager until entering them in Automiq.
2. Find the numeric portal ID, project ID, and target task status ID in Zoho Projects. Task statuses may be customized for each portal or layout; use the ID of the exact status you intend to set. Zoho's [V3 API documentation](https://projects.zoho.com/api-docs) uses numeric portal, project, and task IDs and `PATCH /api/v3/portal/{portal_id}/projects/{project_id}/tasks/{task_id}`. A [Zoho task-closure example](https://help.zoho.com/portal/en/community/topic/automation-series-auto-update-task-closure-field-based-on-due-date-26-5-2026) shows the status body as `{"status":{"id":"..."}}`.
3. In **Studio → Integrations & triggers → Workspace integrations**, select **Zoho Projects**. Enter a display name, data center, portal ID, project ID, OAuth client ID, client secret, and refresh token. Automiq encrypts the credentials and never returns their values in the integration list. Rotation replaces the whole credential set; revocation blocks future actions.
4. Add **Zoho task status** to a workflow. Choose the integration and enter the target status ID. Connect it to a trigger and an end node, save, then publish. The trigger payload must contain `task_id` as a numeric string or integer, for example `{"task_id":"4000004"}`. The action updates only the integration's configured project. Its output contains the task ID, status ID, and delivery result.

Automiq refreshes a short-lived access token inside the worker and sends the V3 PATCH request to a fixed, data-center-specific Zoho Projects API host. It does not accept a destination URL from workflow input. Provider calls are bounded by timeout and response size. Mutations use Automiq's effect ledger; an uncertain response stops for review rather than assuming that a retry is safe. A task managed by a Zoho Blueprint may reject a direct status change; Zoho's API error is shown as a failed action.

## Receive Zoho Projects task events

Zoho Projects [task webhooks](https://help.zoho.com/portal/en/kb/projects/settings-in-zoho-projects/automation/task-automation/articles/webhooks-for-tasks) can notify a third-party URL on task automation events. They do not produce Automiq's signed CloudEvents contract by themselves. Configure a small app-side bridge to receive the Zoho webhook, check a secret header configured in Zoho, validate the task and project identifiers, assign a stable event ID, and forward a signed CloudEvent to an [Automiq custom app connection](connect-custom-app.md). Keep the bridge and its secret outside Automiq's workflow graph. Do not point Zoho's raw webhook directly at the signed Automiq endpoint.

For example, the bridge can send `com.example.zoho.task.updated.v1` with `{"task_id":"4000004","project_id":"2000002"}`. Bind that event to a published workflow. Filter the Zoho automation rule or bridge so an Automiq status update does not trigger the same workflow repeatedly. Zoho documents task webhook availability and delivery limits on its [webhook help page](https://help.zoho.com/portal/en/kb/projects/settings-in-zoho-projects/automation/task-automation/articles/webhooks-for-tasks).

The original “submitted GitHub PR → wait for merge → close task” flow still needs P7-06 durable PR-event correlation. Once that is available, this Zoho action can close a Zoho Projects task when the matching PR merges. A custom task app can instead receive Automiq's signed callback and close its own task.

## Reproduce the fake-provider E2E

Run from the repository root with Docker Desktop, Python, uv, Node.js, and Playwright Chromium. Use only synthetic local configuration. The preparation script writes ignored E2E secrets and TLS material; never use real Zoho credentials in this profile.

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uv --cache-dir .uv-cache run --frozen python scripts/prepare_app_e2e.py
docker compose --env-file .env --env-file .env.app-e2e.local -f infra/compose.yaml -f infra/compose.app-e2e.yaml -f infra/compose.phase4-e2e.yaml up -d --build
docker compose --env-file .env --env-file .env.app-e2e.local -f infra/compose.yaml -f infra/compose.app-e2e.yaml -f infra/compose.phase4-e2e.yaml exec -T api uv run --frozen --no-dev alembic -c alembic.ini upgrade head
docker compose --env-file .env --env-file .env.app-e2e.local -f infra/compose.yaml -f infra/compose.app-e2e.yaml -f infra/compose.phase4-e2e.yaml exec -T api uv run --frozen --no-dev alembic -c alembic.ini check
uv --cache-dir .uv-cache run --frozen python scripts/check_zoho_projects.py
Push-Location apps/web
.\node_modules\.bin\playwright.cmd test --config=playwright.zoho-projects.config.ts
Pop-Location
```

Observed on 2026-10-12: API/PostgreSQL/Temporal/fake-provider E2E passed 16 checks, covering configuration rejection, redacted credentials, publication, a task update with a rate-limit retry, invalid task ID, rotation, and revocation. Chromium passed one UI workflow scenario. Light and dark screenshots were reviewed at 375px and 1280px; no horizontal overflow was observed at 375, 768, 1280, and 1440px. Evidence is ignored under `artifacts/e2e/zoho-projects/` (`report.json`, `playwright.xml`, and four screenshots). Live Zoho OAuth and API behavior was not exercised.
