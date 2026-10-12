"""Full-stack Zoho Projects status action with synthetic credentials and fake provider."""

import asyncio
import json
import os
import platform
import uuid
from pathlib import Path
from typing import Any

import httpx

BASE = os.getenv("ZOHO_E2E_API_URL", "http://127.0.0.1:8000")
ORIGIN = os.getenv("ZOHO_E2E_WEB_ORIGIN", "http://localhost:3000")
REPORT = Path("artifacts/e2e/zoho-projects/report.json")
CHECKS: list[str] = []


def check(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    CHECKS.append(label)


async def mutate(
    client: httpx.AsyncClient,
    path: str,
    csrf: str,
    body: dict[str, Any],
    method: str = "POST",
) -> httpx.Response:
    return await client.request(
        method, path, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}, json=body
    )


async def terminal(client: httpx.AsyncClient, ws: str, run_id: str) -> dict[str, Any]:
    for _ in range(60):
        result = await client.get(f"/api/v1/workspaces/{ws}/runs/{run_id}")
        if result.status_code == 200 and result.json()["status"] in {"succeeded", "failed"}:
            return result.json()
        await asyncio.sleep(1)
    raise AssertionError("Zoho task run did not finish")


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as owner:
        login = await owner.post(
            "/auth/dev", headers={"Origin": ORIGIN}, json={"identity": "owner"}
        )
        check(login.status_code == 200, "local owner signed in")
        csrf = (await owner.get("/api/v1/me")).json()["csrf_token"]
        nonce = uuid.uuid4().hex[:10]
        created = await mutate(
            owner,
            "/api/v1/workspaces",
            csrf,
            {"name": "Zoho Projects E2E", "slug": f"zoho-projects-e2e-{nonce}"},
        )
        check(created.status_code == 201, "workspace created")
        ws = created.json()["id"]
        integration_body = {
            "provider": "zoho_projects",
            "display_name": "Synthetic Zoho Projects",
            "zoho_region": "com",
            "zoho_portal_id": "1000001",
            "zoho_project_id": "2000002",
            "zoho_client_id": "fake-client-id-123",
            "zoho_client_secret": "fake-client-secret-123",
            "zoho_refresh_token": "fake-refresh-token-123",
        }
        invalid = await mutate(
            owner,
            f"/api/v1/workspaces/{ws}/integrations",
            csrf,
            {**integration_body, "zoho_project_id": "http://169.254.169.254"},
        )
        check(invalid.status_code == 422, "arbitrary project destination rejected")
        added = await mutate(
            owner, f"/api/v1/workspaces/{ws}/integrations", csrf, integration_body
        )
        check(
            added.status_code == 201
            and all(secret not in added.text for secret in (
                integration_body["zoho_client_id"],
                integration_body["zoho_client_secret"],
                integration_body["zoho_refresh_token"],
            )),
            "Zoho Projects credential stored without exposing secrets",
        )
        integration_id = added.json()["id"]
        listing = await owner.get(f"/api/v1/workspaces/{ws}/integrations")
        check(
            listing.status_code == 200
            and listing.json()[0]["provider"] == "zoho_projects"
            and "zoho_refresh_token" not in listing.text,
            "Zoho Projects listed with redacted metadata",
        )
        workflow = await mutate(
            owner, f"/api/v1/workspaces/{ws}/workflows", csrf, {"name": "Set task status"}
        )
        check(workflow.status_code == 201, "workflow created")
        base = f"/api/v1/workspaces/{ws}/workflows/{workflow.json()['id']}"
        graph = {
            "schema_version": "1.0",
            "nodes": [
                {"id": "start", "type": "trigger.manual", "config": {}},
                {
                    "id": "update",
                    "type": "action.zoho_task_status",
                    "config": {"integration_id": integration_id, "status_id": "3000003"},
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"source": "start", "target": "update"},
                {"source": "update", "target": "end"},
            ],
        }
        draft = await mutate(
            owner,
            f"{base}/draft",
            csrf,
            {"revision": workflow.json()["draft_revision"], "graph": graph},
            "PUT",
        )
        check(draft.status_code == 200, "Zoho task graph saved")
        published = await mutate(
            owner, f"{base}/publish", csrf, {"revision": draft.json()["draft_revision"]}
        )
        check(published.status_code == 200, "Zoho task graph published")

        async def run(payload: dict[str, Any]) -> dict[str, Any]:
            started = await owner.post(
                f"{base}/runs",
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": f"zoho-{uuid.uuid4().hex}",
                },
                json={"payload": payload},
            )
            check(started.status_code == 202, "task run accepted")
            return await terminal(owner, ws, started.json()["run_id"])

        success = await run({"task_id": "4000004"})
        action = next(
            step
            for step in success["steps"]
            if step["node_id"] == "update" and step["status"] == "succeeded"
        )
        check(
            success["status"] == "succeeded"
            and success["version_id"] == published.json()["id"]
            and action["output"]["provider"] == "zoho_projects"
            and action["output"]["task_id"] == "4000004"
            and action["output"]["status_id"] == "3000003",
            "version-pinned run updates the requested task through fake Zoho provider",
        )
        rejected = await run({"task_id": "../other-project"})
        check(
            rejected["status"] == "failed"
            and any(step["error"] == "invalid_task_id" for step in rejected["steps"]),
            "unsafe task ID fails before provider delivery",
        )
        rotated = await mutate(
            owner,
            f"/api/v1/workspaces/{ws}/integrations/{integration_id}",
            csrf,
            {**integration_body, "zoho_refresh_token": "fake-rotated-refresh-token-123"},
            "PUT",
        )
        check(
            rotated.status_code == 200 and rotated.json()["credential_version"] == 2,
            "Zoho refresh credential rotates",
        )
        revoked = await mutate(
            owner,
            f"/api/v1/workspaces/{ws}/integrations/{integration_id}/revoke",
            csrf,
            {},
        )
        check(revoked.status_code == 200 and revoked.json()["revoked_at"], "integration revoked")
        after_revoke = await run({"task_id": "4000005"})
        check(
            after_revoke["status"] == "failed"
            and any(step["error"] == "integration_unavailable" for step in after_revoke["steps"]),
            "revocation blocks later task updates",
        )


if __name__ == "__main__":
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(main())
    except Exception as exc:
        REPORT.write_text(
            json.dumps({"status": "failed", "checks": CHECKS, "error_type": type(exc).__name__}),
            encoding="utf-8",
        )
        raise
    REPORT.write_text(
        json.dumps(
            {
                "status": "passed",
                "count": len(CHECKS),
                "checks": CHECKS,
                "python": platform.python_version(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Zoho Projects E2E passed {len(CHECKS)} checks; report: {REPORT}")
