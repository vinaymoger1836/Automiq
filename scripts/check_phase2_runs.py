"""Phase 2 HTTP/Temporal/PostgreSQL run flow with synthetic local data."""

import asyncio
import json
import uuid
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
ORIGIN = "http://localhost:3000"


async def main() -> None:
    phase1 = json.loads(
        await asyncio.to_thread(
            Path("artifacts/e2e/phase1-workflows/report.json").read_text, encoding="utf-8"
        )
    )
    ws, workflow_id = phase1["workspace_id"], phase1["workflow_id"]
    scenarios: list[str] = []
    async with (
        httpx.AsyncClient(base_url=BASE, timeout=45) as owner,
        httpx.AsyncClient(base_url=BASE, timeout=15) as viewer,
    ):
        signed = await owner.post(
            "/auth/dev", headers={"Origin": ORIGIN}, json={"identity": "owner"}
        )
        assert signed.status_code == 200, signed.text
        me = (await owner.get("/api/v1/me")).json()
        headers = {
            "Origin": ORIGIN,
            "X-CSRF-Token": me["csrf_token"],
            "Idempotency-Key": f"phase2-{uuid.uuid4().hex}",
        }
        started = await owner.post(
            f"/api/v1/workspaces/{ws}/workflows/{workflow_id}/runs",
            headers=headers,
            json={"payload": {"flag": True}},
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["run_id"]
        assert started.json()["version_id"]
        scenarios.append("manual run transaction returns queued pinned run")
        duplicate = await owner.post(
            f"/api/v1/workspaces/{ws}/workflows/{workflow_id}/runs",
            headers=headers,
            json={"payload": {"flag": True}},
        )
        assert duplicate.status_code == 202 and duplicate.json()["run_id"] == run_id
        conflict = await owner.post(
            f"/api/v1/workspaces/{ws}/workflows/{workflow_id}/runs",
            headers=headers,
            json={"payload": {"flag": False}},
        )
        assert conflict.status_code == 409
        scenarios.append("idempotency key deduplicates and rejects changed input")
        path = f"/api/v1/workspaces/{ws}/runs/{run_id}"
        for _ in range(40):
            inspected = await owner.get(path)
            assert inspected.status_code == 200, inspected.text
            if inspected.json()["status"] in {"succeeded", "failed"}:
                break
            await asyncio.sleep(1)
        assert inspected.json()["status"] == "succeeded", inspected.text
        steps = {step["node_id"]: step for step in inspected.json()["steps"]}
        assert all(
            steps[node]["status"] == "succeeded" for node in ("start", "action", "route", "yes")
        )
        assert steps["no"]["status"] == "skipped"
        assert steps["action"]["output"]["ok"] is False
        scenarios.append("Temporal traverses pinned DAG and projects succeeded/skipped nodes")
        stream_path = f"{path}/events"
        stream = await owner.get(stream_path)
        assert stream.status_code == 200, stream.text
        ids = [int(line[4:]) for line in stream.text.splitlines() if line.startswith("id: ")]
        assert ids == sorted(ids) and len(ids) >= 10
        assert "event: run.succeeded" in stream.text
        resumed = await owner.get(stream_path, headers={"Last-Event-ID": str(ids[-2])})
        resumed_ids = [
            int(line[4:]) for line in resumed.text.splitlines() if line.startswith("id: ")
        ]
        assert resumed_ids == [ids[-1]], resumed.text
        scenarios.append("SSE events are monotonic and resume after Last-Event-ID")
        await viewer.post("/auth/dev", headers={"Origin": ORIGIN}, json={"identity": "viewer"})
        viewer_me = (await viewer.get("/api/v1/me")).json()
        readable = await viewer.get(path)
        assert readable.status_code == 200, readable.text
        denied = await viewer.post(
            f"/api/v1/workspaces/{ws}/workflows/{workflow_id}/runs",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": viewer_me["csrf_token"],
                "Idempotency-Key": f"viewer-{uuid.uuid4().hex}",
            },
            json={"payload": {}},
        )
        assert denied.status_code == 403, denied.text
        scenarios.append("viewer inspects run but cannot start one")
    report = {
        "phase": "P2",
        "status": "passed",
        "count": len(scenarios),
        "scenarios": scenarios,
        "run_id": run_id,
        "version_id": started.json()["version_id"],
        "environment": {
            "api": BASE,
            "temporal": "1.27.2",
            "postgres": "16.6",
            "adapter": "deterministic mock",
        },
    }
    target = Path("artifacts/e2e/phase2-runs/report.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        target.write_text, json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Phase 2: {len(scenarios)} scenarios passed; report: {target}")


if __name__ == "__main__":
    asyncio.run(main())
