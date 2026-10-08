"""Verify real egress is refused by the default local configuration."""

import asyncio
import json
import uuid
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
ORIGIN = "http://localhost:3000"


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
            json={"name": "Disabled HTTPS E2E", "slug": f"https-disabled-{suffix}"},
        )
        assert workspace.status_code == 201, workspace.text
        ws = workspace.json()["id"]
        created = await client.post(
            f"/api/v1/workspaces/{ws}/workflows",
            headers=write,
            json={"name": "Disabled HTTPS GET"},
        )
        assert created.status_code == 201, created.text
        workflow_id = created.json()["id"]
        base = f"/api/v1/workspaces/{ws}/workflows/{workflow_id}"
        graph = {
            "schema_version": "1.0",
            "nodes": [
                {"id": "start", "type": "trigger.manual", "config": {}},
                {
                    "id": "action",
                    "type": "action.http",
                    "config": {
                        "operation": "https_get",
                        "path": "/status",
                        "response_fields": ["ok"],
                    },
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"source": "start", "target": "action"},
                {"source": "action", "target": "end"},
            ],
        }
        validation = await client.post(f"{base}/validate", headers=write, json=graph)
        assert validation.status_code == 200, validation.text
        assert validation.json()["valid"] is False
        assert any(
            item["code"] == "http_connector_disabled"
            for item in validation.json()["diagnostics"]
        )
        saved = await client.put(
            f"{base}/draft",
            headers=write,
            json={"revision": created.json()["draft_revision"], "graph": graph},
        )
        assert saved.status_code == 200, saved.text
        published = await client.post(
            f"{base}/publish",
            headers=write,
            json={"revision": saved.json()["draft_revision"]},
        )
        assert published.status_code == 422, published.text
        assert any(
            item["code"] == "http_connector_disabled"
            for item in published.json()["error"]["diagnostics"]
        )
    target = Path("artifacts/e2e/phase2-http/disabled.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        target.write_text,
        json.dumps({"phase": "P2-06", "status": "passed", "scenarios": 1}) + "\n",
        encoding="utf-8",
    )
    print(f"P2-06: disabled-by-default scenario passed; report: {target}")


if __name__ == "__main__":
    asyncio.run(main())
