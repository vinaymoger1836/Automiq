"""Full-stack synthetic task event, durable wait, PR merge, and auto-close flow."""

import asyncio
import hashlib
import hmac
import json
import os
import platform
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

BASE = os.getenv("APP_E2E_API_URL", "http://127.0.0.1:8000")
ORIGIN = os.getenv("APP_E2E_WEB_ORIGIN", "http://localhost:3000")
REPORT = Path("artifacts/e2e/phase7-waits/report.json")
CALLBACKS = Path("artifacts/e2e/app-connections/callbacks.jsonl")
CHECKS: list[str] = []


def check(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    CHECKS.append(label)


def schema(**fields: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: {"type": kind} for name, kind in fields.items()},
        "required": list(fields),
        "additionalProperties": False,
    }


async def mutate(client: httpx.AsyncClient, path: str, csrf: str, body: Any) -> httpx.Response:
    return await client.post(path, json=body, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})


async def deliver(
    client: httpx.AsyncClient, connection: dict[str, Any], kind: str, data: dict[str, Any],
    *, event_id: str | None = None,
) -> httpx.Response:
    raw = json.dumps(
        {
            "specversion": "1.0", "id": event_id or uuid.uuid4().hex,
            "source": "urn:synthetic:task-app", "type": kind,
            "datacontenttype": "application/json", "data": data,
        },
        sort_keys=True, separators=(",", ":"),
    ).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(
        connection["inbound_secret"].encode(), stamp.encode() + b"." + raw, hashlib.sha256
    ).hexdigest()
    return await client.post(
        connection["webhook_url"], content=raw,
        headers={
            "Content-Type": "application/cloudevents+json",
            "X-Automiq-Timestamp": stamp,
            "X-Automiq-Signature": "sha256=" + signature,
        },
    )


async def inspect(client: httpx.AsyncClient, ws: str, run_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/workspaces/{ws}/runs/{run_id}")
    if response.status_code != 200:
        raise AssertionError("Run is not workspace-visible")
    return response.json()


async def wait_state(
    client: httpx.AsyncClient, ws: str, run_id: str, states: set[str], *, limit: int = 60,
) -> dict[str, Any]:
    for _ in range(limit):
        result = await inspect(client, ws, run_id)
        if result["status"] in states:
            return result
        await asyncio.sleep(1)
    raise AssertionError("Run did not reach expected status")


async def workflow(
    client: httpx.AsyncClient, ws: str, csrf: str, name: str,
    connection: dict[str, Any], wait_node: dict[str, Any], trigger_type: str,
) -> str:
    created = await mutate(client, f"/api/v1/workspaces/{ws}/workflows", csrf, {"name": name})
    check(created.status_code == 201, f"{name} created")
    base = f"/api/v1/workspaces/{ws}/workflows/{created.json()['id']}"
    graph = {
        "schema_version": "1.0",
        "nodes": [
            {"id": "start", "type": "trigger.app_event", "config": {}},
            {"id": "wait", **wait_node},
            {"id": "close", "type": "action.app_callback", "config": {
                "connection_id": connection["id"], "contract_version": 1,
                "event_type": "com.automiq.task.closed.v1",
                "data_paths": {"task_id": "trigger.payload.task_id"},
            }},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "wait"},
            {"source": "wait", "target": "close"},
            {"source": "close", "target": "end"},
        ],
    }
    saved = await client.put(
        f"{base}/draft", json={"revision": 0, "graph": graph},
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
    )
    check(saved.status_code == 200, f"{name} graph saved")
    published = await mutate(client, f"{base}/publish", csrf, {"revision": 1})
    check(published.status_code == 200, f"{name} graph published")
    binding = await mutate(client, f"{base}/triggers/app", csrf, {
        "connection_id": connection["id"], "event_type": trigger_type,
    })
    check(binding.status_code == 201, f"{name} event bound")
    return created.json()["id"]


def restart_worker() -> None:
    command = [
        "docker", "compose", "--env-file", ".env", "--env-file", ".env.app-e2e.local",
        "-f", "infra/compose.yaml", "-f", "infra/compose.app-e2e.yaml",
        "restart", "worker",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=90)
    check(result.returncode == 0, "worker restarted while app wait was pending")


async def run() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as client:
        login = await client.post(
            "/auth/dev", json={"identity": "owner"}, headers={"Origin": ORIGIN}
        )
        check(login.status_code == 200, "synthetic owner signed in")
        csrf = (await client.get("/api/v1/me")).json()["csrf_token"]
        nonce = uuid.uuid4().hex[:10]
        created = await mutate(client, "/api/v1/workspaces", csrf, {
            "name": "Phase 7 wait flow", "slug": f"phase7-wait-{nonce}",
        })
        check(created.status_code == 201, "wait-flow workspace created")
        ws = created.json()["id"]
        connected = await mutate(client, f"/api/v1/workspaces/{ws}/app-connections", csrf, {
            "display_name": "Synthetic task app",
            "callback_url": "https://fake-app:8443/app-one/events",
            "inbound_schemas": {
                "com.example.task.created.v1": schema(task_id="string"),
                "com.example.task.merged.v1": schema(task_id="string"),
                "com.example.task.ready.v1": schema(task_id="string", pr_number="integer"),
            },
            "outbound_schemas": {
                "com.automiq.task.closed.v1": schema(task_id="string"),
            },
        })
        check(connected.status_code == 201, "task app registered with bounded events")
        connection = connected.json()
        github = await mutate(client, f"/api/v1/workspaces/{ws}/integrations", csrf, {
            "provider": "github", "display_name": "Synthetic GitHub",
            "token": "synthetic-gh-token-123", "webhook_secret": "synthetic-webhook-secret-123",
            "repository": "example/tasks",
        })
        check(github.status_code == 201, "scoped GitHub integration created")
        await workflow(client, ws, csrf, "Wait for task event", connection, {
            "type": "wait.app_event", "config": {
                "connection_id": connection["id"], "contract_version": 1,
                "event_type": "com.example.task.merged.v1",
                "correlation_field": "task_id", "trigger_path": "trigger.payload.task_id",
                "timeout_seconds": 40, "poll_seconds": 5,
            },
        }, "com.example.task.created.v1")
        await workflow(client, ws, csrf, "Wait for GitHub PR", connection, {
            "type": "wait.github_pr_merge", "config": {
                "integration_id": github.json()["id"],
                "pr_number_path": "trigger.payload.pr_number",
                "timeout_seconds": 30, "poll_seconds": 5,
            },
        }, "com.example.task.ready.v1")

        task_id = f"task-{nonce}-event"
        started = await deliver(
            client, connection, "com.example.task.created.v1", {"task_id": task_id}
        )
        check(started.status_code == 202 and len(started.json()["run_ids"]) == 1,
              "task submission started one pinned run")
        run_id = started.json()["run_ids"][0]
        await wait_state(client, ws, run_id, {"running"})
        restart_worker()
        wrong = await deliver(
            client, connection, "com.example.task.merged.v1", {"task_id": "other-task"}
        )
        check(wrong.status_code == 202 and not wrong.json()["run_ids"],
              "unrelated event stored without starting another run")
        await asyncio.sleep(6)
        pending = await inspect(client, ws, run_id)
        check(pending["status"] == "running", "wrong correlation did not release wait")
        matched_id = uuid.uuid4().hex
        matched = await deliver(client, connection, "com.example.task.merged.v1",
                                {"task_id": task_id}, event_id=matched_id)
        check(matched.status_code == 202, "matching signed event accepted after restart")
        duplicate = await deliver(client, connection, "com.example.task.merged.v1",
                                  {"task_id": task_id}, event_id=matched_id)
        check(duplicate.status_code == 202 and duplicate.json()["duplicate"],
              "matching event deduplicated")
        done = await wait_state(client, ws, run_id, {"succeeded", "failed"})
        check(done["status"] == "succeeded", "durable app wait resumed and auto-close delivered")
        wait_step = next(step for step in done["steps"] if step["node_id"] == "wait")
        check(wait_step["output"]["event_id"] == matched_id,
              "wait output identifies the matched event")

        pr_task = f"task-{nonce}-pr"
        pr = await deliver(client, connection, "com.example.task.ready.v1",
                           {"task_id": pr_task, "pr_number": 123})
        check(pr.status_code == 202 and len(pr.json()["run_ids"]) == 1,
              "PR task started the reusable merge adapter")
        pr_done = await wait_state(client, ws, pr.json()["run_ids"][0], {"succeeded", "failed"})
        check(pr_done["status"] == "succeeded", "fake PR opened then merged before task auto-close")
        pr_wait = next(step for step in pr_done["steps"] if step["node_id"] == "wait")
        check(pr_wait["output"]["state"] == "merged" and
              pr_wait["output"]["repository"] == "example/tasks",
              "merge result pinned to configured repository")

        timeout_task = f"task-{nonce}-timeout"
        timed = await deliver(client, connection, "com.example.task.created.v1",
                              {"task_id": timeout_task})
        check(timed.status_code == 202, "unmatched task queued")
        failed = await wait_state(client, ws, timed.json()["run_ids"][0],
                                  {"succeeded", "failed"})
        check(failed["status"] == "failed" and failed["error"] == "wait_timeout",
              "wait timeout failed closed")
        callback_rows = await client.get(
            f"/api/v1/workspaces/{ws}/app-connections/{connection['id']}/callbacks"
        )
        check(callback_rows.status_code == 200 and
              len([row for row in callback_rows.json() if row["status"] == "delivered"]) == 2,
              "only matched and merged tasks received auto-close callbacks")
        callback_text = await asyncio.to_thread(CALLBACKS.read_text, encoding="utf-8")
        receipts = [
            json.loads(line) for line in callback_text.splitlines() if line.strip()
        ]
        our_receipts = [
            row for row in receipts
            if row.get("event_type") == "com.automiq.task.closed.v1"
            and row.get("data", {}).get("task_id") in {task_id, pr_task, timeout_task}
        ]
        check(
            {row["data"]["task_id"] for row in our_receipts} == {task_id, pr_task}
            and len(our_receipts) == 2,
            "task app received exactly the two authorized auto-close events",
        )
        check(
            all(
                row["signature"] == "sha256=" + hmac.new(
                    connection["outbound_secret"].encode(),
                    row["timestamp"].encode() + b"." + row["body"].encode(),
                    hashlib.sha256,
                ).hexdigest()
                for row in our_receipts
            ),
            "task auto-close callbacks carry valid signatures",
        )


def main() -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "environment": {"python": platform.python_version(), "api": BASE,
                        "provider": "fake GitHub and synthetic HTTPS app"},
        "scenarios": CHECKS, "status": "failed",
    }
    try:
        asyncio.run(run())
        result["status"] = "passed"
    except Exception as exc:
        result["failure"] = type(exc).__name__
        if isinstance(exc, AssertionError):
            result["failed_check"] = str(exc)
    result["count"] = len(CHECKS)
    REPORT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Phase 7 wait E2E: {result['status']} ({result['count']} checks); {REPORT}")
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
