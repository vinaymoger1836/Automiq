"""Full-stack admission, metrics, tenant boundary, and retention E2E."""

import asyncio
import hashlib
import hmac
import json
import os
import platform
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import update
from sqlalchemy.engine import make_url

BASE = os.getenv("PHASE6_API_URL", "http://127.0.0.1:8000")
ORIGIN = "http://localhost:3000"
REPORT = Path("artifacts/e2e/phase6-operations/report.json")
SCENARIOS: list[str] = []


def check(value: bool, label: str) -> None:
    if not value:
        raise AssertionError(label)
    SCENARIOS.append(label)


async def request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    csrf: str,
    body: dict[str, Any],
    *,
    key: str | None = None,
) -> httpx.Response:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
    if key is not None:
        headers["Idempotency-Key"] = key
    return await client.request(method, path, json=body, headers=headers)


async def wait_status(client: httpx.AsyncClient, ws: str, run_id: str, expected: str) -> None:
    for _ in range(30):
        response = await client.get(f"/api/v1/workspaces/{ws}/runs/{run_id}")
        if response.status_code == 200 and response.json()["status"] == expected:
            return
        await asyncio.sleep(1)
    raise AssertionError(f"run failed to reach {expected}")


def local_database_url() -> str:
    # Read the ignored local config into memory only; never write credentials to evidence.
    value = next(
        line.removeprefix("DATABASE_URL=")
        for line in Path(".env").read_text(encoding="utf-8").splitlines()
        if line.startswith("DATABASE_URL=")
    )
    return make_url(value).set(host="127.0.0.1").render_as_string(hide_password=False)


async def backdate(run_id: str, days: float) -> None:
    from app.db import session_factory
    from app.models import WorkflowRun

    async with session_factory()() as db:
        await db.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == uuid.UUID(run_id))
            .values(completed_at=datetime.now(UTC) - timedelta(days=days))
        )
        await db.commit()


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as owner:
        signed = await owner.post(
            "/auth/dev", headers={"Origin": ORIGIN}, json={"identity": "owner"}
        )
        check(signed.status_code == 200, "owner signed in")
        csrf = (await owner.get("/api/v1/me")).json()["csrf_token"]
        nonce = uuid.uuid4().hex[:10]
        created = await request(
            owner,
            "POST",
            "/api/v1/workspaces",
            csrf,
            {"name": f"Phase 6 E2E {nonce}", "slug": f"phase6-e2e-{nonce}"},
        )
        check(created.status_code == 201, "synthetic workspace created")
        ws = created.json()["id"]
        workflow = await request(
            owner,
            "POST",
            f"/api/v1/workspaces/{ws}/workflows",
            csrf,
            {"name": "Bounded approval run"},
        )
        check(workflow.status_code == 201, "workflow created")
        workflow_id = workflow.json()["id"]
        path = f"/api/v1/workspaces/{ws}/workflows/{workflow_id}"
        graph = {
            "schema_version": "1.0",
            "nodes": [
                {"id": "start", "type": "trigger.manual", "config": {}},
                {
                    "id": "review",
                    "type": "approval",
                    "config": {"title": "Review", "timeout_seconds": 90},
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"source": "start", "target": "review"},
                {"source": "review", "target": "end"},
            ],
        }
        saved = await request(
            owner,
            "PUT",
            f"{path}/draft",
            csrf,
            {"revision": workflow.json()["draft_revision"], "graph": graph},
        )
        check(saved.status_code == 200, "graph saved")
        published = await request(
            owner,
            "POST",
            f"{path}/publish",
            csrf,
            {"revision": saved.json()["draft_revision"]},
        )
        check(published.status_code == 200, "graph published")
        run_path = f"{path}/runs"
        keys = [uuid.uuid4().hex for _ in range(4)]
        first = await request(owner, "POST", run_path, csrf, {"payload": {}}, key=keys[0])
        second = await request(owner, "POST", run_path, csrf, {"payload": {}}, key=keys[1])
        check(first.status_code == second.status_code == 202, "two active runs admitted")
        duplicate = await request(owner, "POST", run_path, csrf, {"payload": {}}, key=keys[0])
        check(
            duplicate.status_code == 202 and duplicate.json()["run_id"] == first.json()["run_id"],
            "idempotent replay bypasses new-run quota",
        )
        excess = await request(owner, "POST", run_path, csrf, {"payload": {}}, key=keys[2])
        check(excess.status_code == 429, "workspace active-run cap enforced")
        run_ids = [first.json()["run_id"], second.json()["run_id"]]
        for run_id in run_ids:
            for _ in range(30):
                inbox = (await owner.get(f"/api/v1/workspaces/{ws}/approvals")).json()
                approval = next((row for row in inbox if row["run_id"] == run_id), None)
                if approval is not None:
                    break
                await asyncio.sleep(1)
            else:
                raise AssertionError("approval did not open")
            decision = await request(
                owner,
                "POST",
                f"/api/v1/workspaces/{ws}/approvals/{approval['id']}/decision",
                csrf,
                {"decision": "approved"},
            )
            check(decision.status_code == 200, "pending run approved")
            await wait_status(owner, ws, run_id, "succeeded")
        third = await request(owner, "POST", run_path, csrf, {"payload": {}}, key=keys[2])
        check(third.status_code == 202, "capacity restored after completion")
        fourth = await request(owner, "POST", run_path, csrf, {"payload": {}}, key=keys[3])
        check(
            fourth.status_code == 429 and fourth.headers.get("retry-after") == "60",
            "Redis request rate limit enforced",
        )
        missing_token = await owner.get("/internal/metrics")
        check(missing_token.status_code == 404, "metrics require scrape token")
        metrics = await owner.get(
            "/internal/metrics", headers={"X-Metrics-Token": "local-only-phase6-metrics"}
        )
        check(
            metrics.status_code == 200 and "automiq_http_requests_total" in metrics.text,
            "metrics scrape exposes request counters",
        )
        async with httpx.AsyncClient(base_url=BASE, timeout=20) as outsider:
            await outsider.post(
                "/auth/dev", headers={"Origin": ORIGIN}, json={"identity": "viewer"}
            )
            hidden = await outsider.get(f"/api/v1/workspaces/{ws}/runs/{run_ids[0]}")
            check(hidden.status_code in {403, 404}, "other tenant cannot inspect run")

        secret = f"local-only-phase6-{uuid.uuid4().hex}"
        integration = await request(
            owner,
            "POST",
            f"/api/v1/workspaces/{ws}/integrations",
            csrf,
            {
                "provider": "github",
                "display_name": "Synthetic quota provider",
                "token": "local-only-fake-token",
                "webhook_secret": secret,
                "repository": "synthetic/repo",
            },
        )
        check(integration.status_code == 201, "synthetic webhook credential created")
        webhook_workflow = await request(
            owner,
            "POST",
            f"/api/v1/workspaces/{ws}/workflows",
            csrf,
            {"name": "Webhook quota"},
        )
        check(webhook_workflow.status_code == 201, "webhook workflow created")
        hook_base = f"/api/v1/workspaces/{ws}/workflows/{webhook_workflow.json()['id']}"
        hook_graph = {
            "schema_version": "1.0",
            "nodes": [
                {"id": "start", "type": "trigger.github_issue", "config": {}},
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [{"source": "start", "target": "end"}],
        }
        hook_saved = await request(
            owner,
            "PUT",
            f"{hook_base}/draft",
            csrf,
            {"revision": webhook_workflow.json()["draft_revision"], "graph": hook_graph},
        )
        check(hook_saved.status_code == 200, "webhook graph saved")
        hook_published = await request(
            owner,
            "POST",
            f"{hook_base}/publish",
            csrf,
            {"revision": hook_saved.json()["draft_revision"]},
        )
        check(hook_published.status_code == 200, "webhook graph published")
        bound = await request(
            owner,
            "POST",
            f"{hook_base}/triggers/github",
            csrf,
            {"integration_id": integration.json()["id"]},
        )
        check(bound.status_code == 201, "signed webhook bound")
        raw = json.dumps(
            {
                "action": "opened",
                "repository": {"full_name": "synthetic/repo"},
                "issue": {"number": 42, "state": "open", "title": "", "body": ""},
            },
            separators=(",", ":"),
        ).encode()
        signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        first_delivery = str(uuid.uuid4())

        async def deliver(delivery_id: str) -> httpx.Response:
            return await owner.post(
                bound.json()["webhook_path"],
                content=raw,
                headers={
                    "X-Hub-Signature-256": signature,
                    "X-GitHub-Delivery": delivery_id,
                    "X-GitHub-Event": "issues",
                    "Content-Type": "application/json",
                },
            )

        first_hook = await deliver(first_delivery)
        check(first_hook.status_code == 202, "first signed delivery admitted")
        await wait_status(owner, ws, first_hook.json()["run_id"], "succeeded")
        for _ in range(2):
            accepted_hook = await deliver(str(uuid.uuid4()))
            check(accepted_hook.status_code == 202, "signed delivery admitted below quota")
            await wait_status(owner, ws, accepted_hook.json()["run_id"], "succeeded")
        limited_hook = await deliver(str(uuid.uuid4()))
        check(limited_hook.status_code == 429, "signed webhook rate limit enforced")
        duplicate_hook = await deliver(first_delivery)
        check(
            duplicate_hook.status_code == 202
            and duplicate_hook.json()["duplicate"]
            and duplicate_hook.json()["run_id"] == first_hook.json()["run_id"],
            "duplicate webhook remains idempotent above quota",
        )

        os.environ["DATABASE_URL"] = local_database_url()
        os.environ["RUN_DETAIL_RETENTION_DAYS"] = "1"
        os.environ["RUN_SUMMARY_RETENTION_DAYS"] = "2"
        from app.config import get_settings
        from app.db import session_factory

        from scripts.cleanup_retention import cleanup

        get_settings.cache_clear()
        session_factory.cache_clear()
        await backdate(run_ids[0], 1.5)
        dry = await cleanup(apply=False, batch_size=500)
        check(not dry["applied"] and dry["details"] >= 1, "retention dry run counts old detail")
        applied = await cleanup(apply=True, batch_size=500)
        check(applied["details"] >= 1, "old detail scrubbed")
        retained = await owner.get(f"/api/v1/workspaces/{ws}/runs/{run_ids[0]}")
        check(
            retained.status_code == 200
            and retained.json()["input"] == {"retained": False}
            and all(step["output"] is None for step in retained.json()["steps"]),
            "old run remains inspectable without detail",
        )
        await backdate(run_ids[0], 2.5)
        removed = await cleanup(apply=True, batch_size=500)
        check(removed["summaries"] >= 1, "expired summary removed")
        missing = await owner.get(f"/api/v1/workspaces/{ws}/runs/{run_ids[0]}")
        check(missing.status_code == 404, "expired run no longer visible")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        REPORT.write_text,
        json.dumps(
            {
                "status": "passed",
                "count": len(SCENARIOS),
                "scenarios": SCENARIOS,
                "environment": {"python": platform.python_version(), "provider": "fake"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Phase 6 operations E2E passed: {len(SCENARIOS)} scenarios")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.with_name("failure.json").write_text(
            json.dumps(
                {"status": "failed", "error_type": type(exc).__name__, "completed": SCENARIOS},
                indent=2,
            ),
            encoding="utf-8",
        )
        raise
