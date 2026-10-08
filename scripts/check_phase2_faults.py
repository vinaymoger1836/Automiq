"""Fault-injection E2E for outbox recovery, activity retry, and worker restart."""

import asyncio
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from orchestrator.engine import WorkflowExecution
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

BASE = "http://127.0.0.1:8000"
ORIGIN = "http://localhost:3000"
COMPOSE = ["docker", "compose", "--env-file", ".env", "-f", "infra/compose.yaml"]


def compose(*args: str) -> None:
    result = subprocess.run([*COMPOSE, *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"Local Compose {args[0]} failed")


async def wait_ready(client: httpx.AsyncClient) -> None:
    for _ in range(40):
        try:
            if (await client.get("/health/live")).status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(1)
    raise AssertionError("API did not restart")


async def wait_status(
    client: httpx.AsyncClient, path: str, expected: str, timeout_seconds: int = 70
) -> dict[str, Any]:
    for _ in range(timeout_seconds):
        response = await client.get(path)
        assert response.status_code == 200, response.text
        data = response.json()
        if data["status"] == expected:
            return data
        if data["status"] in {"succeeded", "failed"}:
            raise AssertionError(f"Run ended in {data['status']}, expected {expected}")
        await asyncio.sleep(1)
    raise AssertionError(f"Run did not reach {expected}")


async def main() -> None:
    phase1 = json.loads(
        await asyncio.to_thread(
            Path("artifacts/e2e/phase1-workflows/report.json").read_text, encoding="utf-8"
        )
    )
    ws, workflow_id = phase1["workspace_id"], phase1["workflow_id"]
    config = dotenv_values(".env")
    raw_url = config.get("DATABASE_URL")
    assert raw_url
    engine = create_async_engine(make_url(raw_url).set(host="127.0.0.1"))
    scenarios: list[str] = []
    worker_stopped = False
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=20) as client:
            login = await client.post(
                "/auth/dev", headers={"Origin": ORIGIN}, json={"identity": "owner"}
            )
            assert login.status_code == 200, login.text
            me = (await client.get("/api/v1/me")).json()
            write = {"Origin": ORIGIN, "X-CSRF-Token": me["csrf_token"]}
            base = f"/api/v1/workspaces/{ws}/workflows/{workflow_id}"

            async def publish_config(**changes: Any) -> str:
                response = await client.get(base)
                assert response.status_code == 200, response.text
                workflow = response.json()
                graph = workflow["draft_graph"]
                action = next(node for node in graph["nodes"] if node["id"] == "action")
                action["config"] = {
                    "operation": "mock",
                    "mock_output": {"ok": True},
                    "failures_before_success": 0,
                    "permanent_failure": False,
                    "delay_seconds": 0,
                    **changes,
                }
                saved = await client.put(
                    f"{base}/draft",
                    headers=write,
                    json={"revision": workflow["draft_revision"], "graph": graph},
                )
                assert saved.status_code == 200, saved.text
                published = await client.post(
                    f"{base}/publish",
                    headers=write,
                    json={"revision": saved.json()["draft_revision"]},
                )
                assert published.status_code == 200, published.text
                return published.json()["id"]

            async def start(flag: bool = True) -> tuple[str, str]:
                key = f"fault-{uuid.uuid4().hex}"
                response = await client.post(
                    f"{base}/runs",
                    headers={**write, "Idempotency-Key": key},
                    json={"payload": {"flag": flag}},
                )
                assert response.status_code == 202, response.text
                return response.json()["run_id"], response.json()["version_id"]

            compose("stop", "worker")
            worker_stopped = True
            queued_id, pinned = await start()
            queued_path = f"/api/v1/workspaces/{ws}/runs/{queued_id}"
            await asyncio.sleep(2)
            queued = (await client.get(queued_path)).json()
            assert queued["status"] == "queued" and queued["version_id"] == pinned
            async with engine.connect() as connection:
                outbox = await connection.execute(
                    text("SELECT attempts, started_at FROM run_start_outbox WHERE run_id = :id"),
                    {"id": uuid.UUID(queued_id)},
                )
                assert outbox.one() == (0, None)
            compose("restart", "api")
            await wait_ready(client)
            compose("start", "worker")
            worker_stopped = False
            completed = await wait_status(client, queued_path, "succeeded")
            assert completed["version_id"] == pinned
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            "SELECT attempts, started_at FROM run_start_outbox WHERE run_id = :id"
                        ),
                        {"id": uuid.UUID(queued_id)},
                    )
                ).one()
                assert row[0] >= 1 and row[1] is not None
            scenarios.append("queued DB outbox survives API restart and worker absence")

            temporal = await Client.connect(
                "127.0.0.1:7233", namespace=str(config.get("TEMPORAL_NAMESPACE", "default"))
            )
            try:
                await temporal.start_workflow(
                    WorkflowExecution.run,
                    queued_id,
                    id=f"run:{queued_id}",
                    task_queue=str(config.get("TEMPORAL_TASK_QUEUE", "automiq-bootstrap")),
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                )
            except WorkflowAlreadyStartedError:
                pass
            else:
                raise AssertionError("Duplicate Temporal start was accepted")
            scenarios.append("Temporal rejects a duplicate start for the same run ID")

            await publish_config(failures_before_success=2)
            retry_id, _ = await start()
            retried = await wait_status(
                client, f"/api/v1/workspaces/{ws}/runs/{retry_id}", "succeeded"
            )
            attempts = {
                step["attempt"]: step["status"]
                for step in retried["steps"]
                if step["node_id"] == "action"
            }
            assert attempts == {1: "failed", 2: "failed", 3: "succeeded"}, attempts
            async with engine.connect() as connection:
                count = await connection.scalar(
                    text(
                        "SELECT invocations FROM action_effects "
                        "WHERE run_id = :id AND node_id = 'action'"
                    ),
                    {"id": uuid.UUID(retry_id)},
                )
                assert count == 3
            scenarios.append("transient mock action retries twice with traceable attempts")

            await publish_config(permanent_failure=True)
            failed_id, _ = await start()
            failed = await wait_status(
                client, f"/api/v1/workspaces/{ws}/runs/{failed_id}", "failed"
            )
            assert failed["error"] == "external_error"
            assert any(
                step["node_id"] == "action" and step["status"] == "failed"
                for step in failed["steps"]
            )
            scenarios.append("permanent action failure terminates run without unbounded retry")

            await publish_config(delay_seconds=6, timeout_seconds=1)
            timed_out_id, _ = await start()
            timed_out = await wait_status(
                client, f"/api/v1/workspaces/{ws}/runs/{timed_out_id}", "failed", 60
            )
            assert timed_out["error"] == "external_error"
            scenarios.append("action deadline terminates the run after bounded retries")

            await publish_config(delay_seconds=6, timeout_seconds=10)
            restarted_id, _ = await start()
            restarted_path = f"/api/v1/workspaces/{ws}/runs/{restarted_id}"
            for _ in range(30):
                running = (await client.get(restarted_path)).json()
                if any(
                    step["node_id"] == "action" and step["status"] == "running"
                    for step in running["steps"]
                ):
                    break
                await asyncio.sleep(1)
            else:
                raise AssertionError("Action did not start before worker restart")
            compose("stop", "-t", "0", "worker")
            worker_stopped = True
            await asyncio.sleep(1)
            compose("start", "worker")
            worker_stopped = False
            resumed = await wait_status(client, restarted_path, "succeeded", 90)
            assert any(
                step["node_id"] == "action" and step["status"] == "succeeded"
                for step in resumed["steps"]
            )
            scenarios.append("Temporal resumes an in-flight run after abrupt worker stop")
    finally:
        if worker_stopped:
            compose("start", "worker")
        await engine.dispose()
    report = {
        "phase": "P2",
        "status": "passed",
        "count": len(scenarios),
        "scenarios": scenarios,
        "environment": {"temporal": "1.27.2", "postgres": "16.6", "adapter": "deterministic mock"},
    }
    target = Path("artifacts/e2e/phase2-faults/report.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        target.write_text, json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Phase 2 faults: {len(scenarios)} scenarios passed; report: {target}")


if __name__ == "__main__":
    asyncio.run(main())
