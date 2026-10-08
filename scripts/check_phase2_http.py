"""Full API/PostgreSQL/Temporal/fake-TLS-provider E2E for P2-06."""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8000"
ORIGIN = "http://localhost:3000"


def graph(path: str, fields: list[str] | None = None, timeout_seconds: int = 10) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "nodes": [
            {"id": "start", "type": "trigger.manual", "config": {}},
            {
                "id": "action",
                "type": "action.http",
                "config": {
                    "operation": "https_get",
                    "path": path,
                    "response_fields": fields if fields is not None else ["ok"],
                    "timeout_seconds": timeout_seconds,
                },
            },
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "action"},
            {"source": "action", "target": "end"},
        ],
    }


async def terminal(client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    for _ in range(45):
        response = await client.get(path)
        assert response.status_code == 200, response.text
        if response.json()["status"] in {"succeeded", "failed"}:
            return response.json()
        await asyncio.sleep(1)
    raise AssertionError("HTTPS connector run did not finish")


async def main() -> None:
    scenarios: list[str] = []
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as client:
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
            json={"name": "HTTPS E2E", "slug": f"https-e2e-{suffix}"},
        )
        assert workspace.status_code == 201, workspace.text
        ws = workspace.json()["id"]
        created = await client.post(
            f"/api/v1/workspaces/{ws}/workflows",
            headers=write,
            json={"name": "Pinned HTTPS GET"},
        )
        assert created.status_code == 201, created.text
        workflow_id = created.json()["id"]
        base = f"/api/v1/workspaces/{ws}/workflows/{workflow_id}"

        unsafe = await client.put(
            f"{base}/draft",
            headers=write,
            json={
                "revision": created.json()["draft_revision"],
                "graph": graph("//169.254.169.254"),
            },
        )
        assert unsafe.status_code == 422, unsafe.text
        scenarios.append("graph rejects an absolute-like path before publication")

        async def publish_and_run(
            path: str, fields: list[str] | None = None, timeout_seconds: int = 10
        ) -> tuple[dict[str, Any], str]:
            current = (await client.get(base)).json()
            saved = await client.put(
                f"{base}/draft",
                headers=write,
                json={
                    "revision": current["draft_revision"],
                    "graph": graph(path, fields, timeout_seconds),
                },
            )
            assert saved.status_code == 200, saved.text
            published = await client.post(
                f"{base}/publish",
                headers=write,
                json={"revision": saved.json()["draft_revision"]},
            )
            assert published.status_code == 200, published.text
            started = await client.post(
                f"{base}/runs",
                headers={**write, "Idempotency-Key": f"https-{uuid.uuid4().hex}"},
                json={"payload": {"flag": True}},
            )
            assert started.status_code == 202, started.text
            run_path = f"/api/v1/workspaces/{ws}/runs/{started.json()['run_id']}"
            completed = await terminal(client, run_path)
            assert completed["version_id"] == published.json()["id"]
            return completed, run_path

        success, success_path = await publish_and_run("/status")
        assert success["status"] == "succeeded", success
        action = next(step for step in success["steps"] if step["node_id"] == "action")
        assert action["output"] == {"http_status": 200, "ok": True}
        events = await client.get(f"{success_path}/events")
        assert events.status_code == 200 and "must-not-persist" not in events.text
        scenarios.append("TLS hostname verification, pinned destination, and selected output")

        redirected, _ = await publish_and_run("/redirect")
        assert redirected["status"] == "failed"
        errors = [step["error"] for step in redirected["steps"] if step["node_id"] == "action"]
        assert errors == ["http_rejected"], errors
        scenarios.append("metadata redirect is not followed or retried")

        large, _ = await publish_and_run("/large")
        assert large["status"] == "failed"
        assert "response_too_large" in [
            step["error"] for step in large["steps"] if step["node_id"] == "action"
        ]
        scenarios.append("oversized provider response is rejected before persistence")

        badtype, _ = await publish_and_run("/badtype")
        assert badtype["status"] == "failed"
        assert "invalid_response" in [
            step["error"] for step in badtype["steps"] if step["node_id"] == "action"
        ]
        scenarios.append("non-scalar selected response is rejected")

        retried, _ = await publish_and_run("/retry", ["ok", "attempt"])
        assert retried["status"] == "succeeded", retried
        attempts = {
            step["attempt"]: step["status"]
            for step in retried["steps"]
            if step["node_id"] == "action"
        }
        assert attempts == {1: "failed", 2: "failed", 3: "succeeded"}, attempts
        scenarios.append("transient HTTPS failures retry with traceable attempts")

        timed_out, _ = await publish_and_run("/slow", timeout_seconds=2)
        assert timed_out["status"] == "failed", timed_out
        timed_out_attempts = {
            step["attempt"]: (step["status"], step["error"])
            for step in timed_out["steps"]
            if step["node_id"] == "action"
        }
        assert timed_out_attempts == {
            1: ("failed", "http_timeout"),
            2: ("failed", "http_timeout"),
            3: ("failed", "http_timeout"),
        }, timed_out_attempts
        scenarios.append("slow HTTPS provider reaches a bounded terminal failure")

    report = {
        "phase": "P2-06",
        "status": "passed",
        "count": len(scenarios),
        "scenarios": scenarios,
        "environment": {
            "provider": "synthetic TLS server in Compose",
            "origin": "https://fake-provider:8443",
            "app_env": "test",
            "public_egress": "disabled in E2E",
        },
    }
    target = Path("artifacts/e2e/phase2-http/report.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        target.write_text, json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(f"P2-06: {len(scenarios)} scenarios passed; report: {target}")


if __name__ == "__main__":
    asyncio.run(main())
