"""Synthetic, bounded API/webhook/workflow benchmark for the local fake stack."""

import asyncio
import hashlib
import hmac
import json
import os
import platform
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

BASE = os.getenv("PHASE6_API_URL", "http://127.0.0.1:8000")
REPORT = Path("artifacts/e2e/phase6-load/report.json")
ORIGIN = "http://localhost:3000"
READS = 100
CONCURRENT = 20


def p95(values: list[float]) -> float:
    return sorted(values)[max(0, (95 * len(values) + 99) // 100 - 1)]


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
    return await client.request(method, path, headers=headers, json=body)


async def publish(
    client: httpx.AsyncClient,
    ws: str,
    csrf: str,
    name: str,
    trigger_type: str,
) -> str:
    created = await mutate(client, f"/api/v1/workspaces/{ws}/workflows", csrf, {"name": name})
    created.raise_for_status()
    path = f"/api/v1/workspaces/{ws}/workflows/{created.json()['id']}"
    saved = await mutate(
        client,
        f"{path}/draft",
        csrf,
        {
            "revision": created.json()["draft_revision"],
            "graph": {
                "schema_version": "1.0",
                "nodes": [
                    {"id": "start", "type": trigger_type, "config": {}},
                    {"id": "end", "type": "end", "config": {}},
                ],
                "edges": [{"source": "start", "target": "end"}],
            },
        },
        method="PUT",
    )
    saved.raise_for_status()
    published = await mutate(
        client,
        f"{path}/publish",
        csrf,
        {"revision": saved.json()["draft_revision"]},
    )
    published.raise_for_status()
    return path


async def wait_all(client: httpx.AsyncClient, ws: str, ids: list[str]) -> None:
    pending = set(ids)
    for _ in range(60):
        responses = await asyncio.gather(
            *(client.get(f"/api/v1/workspaces/{ws}/runs/{run_id}") for run_id in pending)
        )
        for run_id, response in zip(list(pending), responses, strict=True):
            if response.status_code == 200 and response.json()["status"] == "succeeded":
                pending.discard(run_id)
        if not pending:
            return
        await asyncio.sleep(1)
    raise AssertionError(f"{len(pending)} synthetic runs did not complete")


async def main() -> None:
    limits = httpx.Limits(max_connections=50, max_keepalive_connections=50)
    async with httpx.AsyncClient(base_url=BASE, timeout=30, limits=limits) as client:
        login = await client.post(
            "/auth/dev", headers={"Origin": ORIGIN}, json={"identity": "owner"}
        )
        login.raise_for_status()
        csrf = (await client.get("/api/v1/me")).json()["csrf_token"]
        nonce = uuid.uuid4().hex[:10]
        workspace = await mutate(
            client,
            "/api/v1/workspaces",
            csrf,
            {"name": f"Phase 6 load {nonce}", "slug": f"phase6-load-{nonce}"},
        )
        workspace.raise_for_status()
        ws = workspace.json()["id"]
        manual = await publish(client, ws, csrf, "Manual load", "trigger.manual")
        github = await publish(client, ws, csrf, "Webhook load", "trigger.github_issue")
        secret = f"local-only-load-{uuid.uuid4().hex}"
        integration = await mutate(
            client,
            f"/api/v1/workspaces/{ws}/integrations",
            csrf,
            {
                "provider": "github",
                "display_name": "Synthetic load",
                "token": "local-only-load-token",
                "webhook_secret": secret,
                "repository": "synthetic/repo",
            },
        )
        integration.raise_for_status()
        binding = await mutate(
            client,
            f"{github}/triggers/github",
            csrf,
            {"integration_id": integration.json()["id"]},
        )
        binding.raise_for_status()
        webhook_path = binding.json()["webhook_path"]

        async def timed_read() -> tuple[int, float]:
            started = time.perf_counter()
            response = await client.get(f"/api/v1/workspaces/{ws}/workflows")
            return response.status_code, time.perf_counter() - started

        reads = await asyncio.gather(*(timed_read() for _ in range(READS)))
        read_times = [elapsed for _, elapsed in reads]
        read_failures = sum(status != 200 for status, _ in reads)

        async def timed_run() -> tuple[int, float, str | None]:
            started = time.perf_counter()
            response = await mutate(
                client, f"{manual}/runs", csrf, {"payload": {}}, key=uuid.uuid4().hex
            )
            return (
                response.status_code,
                time.perf_counter() - started,
                response.json().get("run_id") if response.status_code == 202 else None,
            )

        run_started = time.perf_counter()
        runs = await asyncio.gather(*(timed_run() for _ in range(CONCURRENT)))
        run_ids = [run_id for _, _, run_id in runs if run_id]
        if run_ids:
            await wait_all(client, ws, run_ids)
        run_wall = time.perf_counter() - run_started

        raw = json.dumps(
            {
                "action": "opened",
                "repository": {"full_name": "synthetic/repo"},
                "issue": {"number": 42, "state": "open", "title": "Synthetic load", "body": ""},
            },
            separators=(",", ":"),
        ).encode()
        signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

        async def timed_webhook() -> tuple[int, float, str | None]:
            started = time.perf_counter()
            response = await client.post(
                webhook_path,
                content=raw,
                headers={
                    "X-Hub-Signature-256": signature,
                    "X-GitHub-Delivery": str(uuid.uuid4()),
                    "X-GitHub-Event": "issues",
                    "Content-Type": "application/json",
                },
            )
            return (
                response.status_code,
                time.perf_counter() - started,
                response.json().get("run_id") if response.status_code == 202 else None,
            )

        webhook_started = time.perf_counter()
        hooks = await asyncio.gather(*(timed_webhook() for _ in range(CONCURRENT)))
        hook_ids = [run_id for _, _, run_id in hooks if run_id]
        if hook_ids:
            await wait_all(client, ws, hook_ids)
        webhook_wall = time.perf_counter() - webhook_started

    report = {
        "status": "passed"
        if read_failures == 0 and len(run_ids) == CONCURRENT and len(hook_ids) == CONCURRENT
        else "failed",
        "environment": {
            "python": platform.python_version(),
            "os": platform.system(),
            "cpu_count": os.cpu_count(),
            "provider": "fake",
            "concurrency": CONCURRENT,
        },
        "reads": {
            "requests": READS,
            "failures": read_failures,
            "p95_ms": round(p95(read_times) * 1000, 1),
        },
        "manual_runs": {
            "requests": CONCURRENT,
            "completed": len(run_ids),
            "failures": CONCURRENT - len(run_ids),
            "accept_p95_ms": round(p95([elapsed for _, elapsed, _ in runs]) * 1000, 1),
            "throughput_per_second": round(len(run_ids) / run_wall, 2),
        },
        "signed_webhooks": {
            "requests": CONCURRENT,
            "completed": len(hook_ids),
            "failures": CONCURRENT - len(hook_ids),
            "accept_p95_ms": round(p95([elapsed for _, elapsed, _ in hooks]) * 1000, 1),
            "throughput_per_second": round(len(hook_ids) / webhook_wall, 2),
        },
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(REPORT.write_text, json.dumps(report, indent=2), encoding="utf-8")
    print(f"Phase 6 local benchmark: {report['status']}; report: {REPORT}")
    if report["status"] != "passed":
        raise AssertionError("synthetic benchmark had failed requests")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.with_name("failure.json").write_text(
            json.dumps({"status": "failed", "error_type": type(exc).__name__}, indent=2),
            encoding="utf-8",
        )
        raise
