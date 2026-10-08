"""Full-stack E2E: a metadata-range DNS answer is rejected before connection."""

import asyncio
import json
import uuid
from pathlib import Path

import httpx

from check_phase2_http import BASE, ORIGIN, graph, terminal


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as client:
        signed = await client.post(
            "/auth/dev", headers={"Origin": ORIGIN}, json={"identity": "owner"}
        )
        assert signed.status_code == 200, signed.text
        me = (await client.get("/api/v1/me")).json()
        write = {"Origin": ORIGIN, "X-CSRF-Token": me["csrf_token"]}
        suffix = uuid.uuid4().hex[:10]
        workspace = await client.post(
            "/api/v1/workspaces",
            headers=write,
            json={"name": "Blocked HTTPS E2E", "slug": f"https-blocked-{suffix}"},
        )
        assert workspace.status_code == 201, workspace.text
        ws = workspace.json()["id"]
        created = await client.post(
            f"/api/v1/workspaces/{ws}/workflows",
            headers=write,
            json={"name": "Blocked destination"},
        )
        assert created.status_code == 201, created.text
        base = f"/api/v1/workspaces/{ws}/workflows/{created.json()['id']}"
        saved = await client.put(
            f"{base}/draft",
            headers=write,
            json={"revision": created.json()["draft_revision"], "graph": graph("/status")},
        )
        assert saved.status_code == 200, saved.text
        published = await client.post(
            f"{base}/publish", headers=write, json={"revision": saved.json()["draft_revision"]}
        )
        assert published.status_code == 200, published.text
        started = await client.post(
            f"{base}/runs",
            headers={**write, "Idempotency-Key": f"blocked-{uuid.uuid4().hex}"},
            json={"payload": {}},
        )
        assert started.status_code == 202, started.text
        run = await terminal(client, f"/api/v1/workspaces/{ws}/runs/{started.json()['run_id']}")
        assert run["status"] == "failed", run
        action_errors = [
            step["error"] for step in run["steps"] if step["node_id"] == "action"
        ]
        assert action_errors == ["unsafe_destination"], action_errors
    target = Path("artifacts/e2e/phase2-http/blocked.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        target.write_text,
        json.dumps({"phase": "P2-06", "status": "passed", "scenarios": 1}) + "\n",
        encoding="utf-8",
    )
    print(f"P2-06: metadata-range DNS rejection passed; report: {target}")


if __name__ == "__main__":
    asyncio.run(main())
