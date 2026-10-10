"""Local-only dependency failure and durable recovery matrix."""

import asyncio
import json
import os
import platform
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx

BASE = os.getenv("PHASE6_API_URL", "http://127.0.0.1:8000")
ORIGIN = "http://localhost:3000"
REPORT = Path("artifacts/e2e/phase6-failures/report.json")
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    ".env",
    "--env-file",
    ".env.phase4.local",
    "-f",
    "infra/compose.yaml",
    "-f",
    "infra/compose.phase4-e2e.yaml",
]
SCENARIOS: list[str] = []


def check(value: bool, label: str) -> None:
    if not value:
        raise AssertionError(label)
    SCENARIOS.append(label)


async def compose(*args: str) -> None:
    await asyncio.to_thread(
        subprocess.run,
        [*COMPOSE, *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=90,
    )


async def ready(expected: int, seconds: int = 75) -> bool:
    async with httpx.AsyncClient(base_url=BASE, timeout=5) as client:
        for _ in range(seconds):
            try:
                response = await client.get("/health/ready")
                if response.status_code == expected:
                    return True
            except httpx.RequestError:
                pass
            await asyncio.sleep(1)
    return False


async def mutate(
    client: httpx.AsyncClient,
    path: str,
    csrf: str,
    body: dict[str, Any],
    *,
    method: str = "POST",
    key: str | None = None,
) -> httpx.Response:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
    if key:
        headers["Idempotency-Key"] = key
    return await client.request(method, path, json=body, headers=headers)


async def wait_run(client: httpx.AsyncClient, ws: str, run_id: str, status: str) -> bool:
    for _ in range(75):
        try:
            response = await client.get(f"/api/v1/workspaces/{ws}/runs/{run_id}")
            if response.status_code == 200 and response.json()["status"] == status:
                return True
        except httpx.RequestError:
            pass
        await asyncio.sleep(1)
    return False


async def main() -> None:
    if os.getenv("PHASE6_ALLOW_FAULTS") != "1":
        raise RuntimeError("Set PHASE6_ALLOW_FAULTS=1 for the local Compose stack")
    check(await ready(200), "baseline dependencies healthy")
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as client:
        login = await client.post(
            "/auth/dev", headers={"Origin": ORIGIN}, json={"identity": "owner"}
        )
        check(login.status_code == 200, "synthetic owner signed in")
        csrf = (await client.get("/api/v1/me")).json()["csrf_token"]
        nonce = uuid.uuid4().hex[:10]
        workspace = await mutate(
            client,
            "/api/v1/workspaces",
            csrf,
            {"name": f"Phase 6 faults {nonce}", "slug": f"phase6-faults-{nonce}"},
        )
        check(workspace.status_code == 201, "synthetic workspace created")
        ws = workspace.json()["id"]
        workflow = await mutate(
            client, f"/api/v1/workspaces/{ws}/workflows", csrf, {"name": "Recovery"}
        )
        check(workflow.status_code == 201, "recovery workflow created")
        path = f"/api/v1/workspaces/{ws}/workflows/{workflow.json()['id']}"
        graph = {
            "schema_version": "1.0",
            "nodes": [
                {"id": "start", "type": "trigger.manual", "config": {}},
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [{"source": "start", "target": "end"}],
        }
        saved = await mutate(
            client,
            f"{path}/draft",
            csrf,
            {"revision": workflow.json()["draft_revision"], "graph": graph},
            method="PUT",
        )
        check(saved.status_code == 200, "recovery graph saved")
        published = await mutate(
            client, f"{path}/publish", csrf, {"revision": saved.json()["draft_revision"]}
        )
        check(published.status_code == 200, "recovery graph published")

        try:
            await compose("stop", "api")
            unreachable = False
            try:
                await client.get("/health/live")
            except httpx.RequestError:
                unreachable = True
            check(unreachable, "API stop makes local API unavailable")
        finally:
            await compose("start", "api")
        check(await ready(200), "API restart restores readiness")

        try:
            await compose("stop", "worker")
            queued = await mutate(
                client, f"{path}/runs", csrf, {"payload": {}}, key=uuid.uuid4().hex
            )
            check(queued.status_code == 202, "run accepted while worker stopped")
            run_id = queued.json()["run_id"]
            await asyncio.sleep(3)
            state = await client.get(f"/api/v1/workspaces/{ws}/runs/{run_id}")
            check(
                state.status_code == 200 and state.json()["status"] == "queued",
                "run durably queued without worker",
            )
        finally:
            await compose("start", "worker")
        check(await wait_run(client, ws, run_id, "succeeded"), "worker restart resumes queued run")

        try:
            await compose("stop", "temporal")
            check(await ready(503), "Temporal outage degrades readiness")
            queued = await mutate(
                client, f"{path}/runs", csrf, {"payload": {}}, key=uuid.uuid4().hex
            )
            check(queued.status_code == 202, "run accepted while Temporal unavailable")
            temporal_run = queued.json()["run_id"]
        finally:
            await compose("start", "temporal")
        check(await ready(200), "Temporal recovery restores readiness")
        check(
            await wait_run(client, ws, temporal_run, "succeeded"),
            "outbox reconciles after Temporal recovery",
        )

        try:
            await compose("stop", "postgres")
            check(await ready(503), "PostgreSQL outage degrades readiness")
        finally:
            await compose("start", "postgres")
        check(await ready(200), "PostgreSQL recovery restores readiness")
        final = await mutate(client, f"{path}/runs", csrf, {"payload": {}}, key=uuid.uuid4().hex)
        check(final.status_code == 202, "run accepted after database recovery")
        check(
            await wait_run(client, ws, final.json()["run_id"], "succeeded"),
            "run completes after database recovery",
        )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        REPORT.write_text,
        json.dumps(
            {
                "status": "passed",
                "count": len(SCENARIOS),
                "scenarios": SCENARIOS,
                "environment": {
                    "python": platform.python_version(),
                    "provider": "fake",
                    "compose": "local",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Phase 6 dependency matrix passed: {len(SCENARIOS)} scenarios")


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
