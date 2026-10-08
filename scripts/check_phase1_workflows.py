"""Full HTTP/PostgreSQL Phase 1 check with synthetic local identities."""

import asyncio
import json
import uuid
from pathlib import Path

import httpx
from app.graph import Graph
from app.models import WorkflowVersion
from dotenv import dotenv_values
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

BASE = "http://127.0.0.1:8000"
ORIGIN = "http://localhost:3000"


async def sign_in(client: httpx.AsyncClient, identity: str) -> dict[str, object]:
    response = await client.post(
        "/auth/dev", headers={"Origin": ORIGIN}, json={"identity": identity}
    )
    assert response.status_code == 200, response.text
    response = await client.get("/api/v1/me")
    assert response.status_code == 200, response.text
    return response.json()


async def main() -> None:
    result: dict[str, object] = {"phase": "P1", "scenarios": []}
    scenarios: list[str] = result["scenarios"]  # type: ignore[assignment]
    config = dotenv_values(".env")
    raw_url = config.get("DATABASE_URL")
    assert raw_url, "Set DATABASE_URL in local .env"
    engine = create_async_engine(make_url(raw_url).set(host="127.0.0.1"))
    suffix = uuid.uuid4().hex[:12]
    async with (
        httpx.AsyncClient(base_url=BASE, timeout=15) as owner,
        httpx.AsyncClient(base_url=BASE, timeout=15) as viewer,
        httpx.AsyncClient(base_url=BASE, timeout=15) as editor,
    ):
        owner_me = await sign_in(owner, "owner")
        token = owner_me["csrf_token"]
        write = {"Origin": ORIGIN, "X-CSRF-Token": str(token)}
        denied = await owner.post(
            "/api/v1/workspaces", json={"name": "Denied", "slug": f"denied-{suffix}"}
        )
        assert denied.status_code == 403
        scenarios.append("cookie writes require CSRF token and allowed origin")

        created = await owner.post(
            "/api/v1/workspaces",
            headers=write,
            json={"name": "Synthetic Workspace", "slug": f"e2e-{suffix}"},
        )
        assert created.status_code == 201, created.text
        ws = created.json()["id"]
        workflow = await owner.post(
            f"/api/v1/workspaces/{ws}/workflows", headers=write, json={"name": "Phase 1 Graph"}
        )
        assert workflow.status_code == 201, workflow.text
        workflow_id = workflow.json()["id"]
        revision = workflow.json()["draft_revision"]
        scenarios.append("owner creates workspace and draft through authenticated API")

        invalid = {
            "schema_version": "1.0",
            "nodes": [
                {"id": "start", "type": "trigger.manual", "config": {}},
                {
                    "id": "route",
                    "type": "condition",
                    "config": {
                        "expression": {
                            "path": "trigger.payload.flag",
                            "operator": "eq",
                            "value": True,
                        }
                    },
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"source": "start", "target": "route"},
                {"source": "route", "source_handle": "true", "target": "end"},
            ],
        }
        saved = await owner.put(
            f"/api/v1/workspaces/{ws}/workflows/{workflow_id}/draft",
            headers=write,
            json={"revision": revision, "graph": invalid},
        )
        assert saved.status_code == 200, saved.text
        revision = saved.json()["draft_revision"]
        rejected = await owner.post(
            f"/api/v1/workspaces/{ws}/workflows/{workflow_id}/publish",
            headers=write,
            json={"revision": revision},
        )
        assert rejected.status_code == 422, rejected.text
        assert any(
            item["code"] == "condition_branches" for item in rejected.json()["error"]["diagnostics"]
        )
        scenarios.append("missing condition branch is rejected with diagnostics")

        cycle = {
            "schema_version": "1.0",
            "nodes": [
                {"id": "start", "type": "trigger.manual", "config": {}},
                {"id": "a", "type": "action.http", "config": {}},
                {"id": "b", "type": "action.http", "config": {}},
            ],
            "edges": [
                {"source": "start", "target": "a"},
                {"source": "a", "target": "b"},
                {"source": "b", "target": "a"},
            ],
        }
        saved = await owner.put(
            f"/api/v1/workspaces/{ws}/workflows/{workflow_id}/draft",
            headers=write,
            json={"revision": revision, "graph": cycle},
        )
        assert saved.status_code == 200, saved.text
        revision = saved.json()["draft_revision"]
        rejected = await owner.post(
            f"/api/v1/workspaces/{ws}/workflows/{workflow_id}/publish",
            headers=write,
            json={"revision": revision},
        )
        assert rejected.status_code == 422, rejected.text
        assert any(item["code"] == "cycle" for item in rejected.json()["error"]["diagnostics"])
        scenarios.append("cycle is rejected at publish")

        graph = {
            "schema_version": "1.0",
            "nodes": [
                {"id": "start", "type": "trigger.manual", "config": {}},
                {"id": "action", "type": "action.http", "config": {"mock_output": {"ok": True}}},
                {
                    "id": "route",
                    "type": "condition",
                    "config": {
                        "expression": {
                            "path": "trigger.payload.flag",
                            "operator": "eq",
                            "value": True,
                        }
                    },
                },
                {"id": "yes", "type": "end", "config": {}},
                {"id": "no", "type": "end", "config": {}},
            ],
            "edges": [
                {"source": "start", "target": "action"},
                {"source": "action", "target": "route"},
                {"source": "route", "source_handle": "true", "target": "yes"},
                {"source": "route", "source_handle": "false", "target": "no"},
            ],
        }
        saved = await owner.put(
            f"/api/v1/workspaces/{ws}/workflows/{workflow_id}/draft",
            headers=write,
            json={"revision": revision, "graph": graph},
        )
        assert saved.status_code == 200, saved.text
        conflict = await owner.put(
            f"/api/v1/workspaces/{ws}/workflows/{workflow_id}/draft",
            headers=write,
            json={"revision": revision, "graph": graph},
        )
        assert conflict.status_code == 409
        revision = saved.json()["draft_revision"]
        first = await owner.post(
            f"/api/v1/workspaces/{ws}/workflows/{workflow_id}/publish",
            headers=write,
            json={"revision": revision},
        )
        assert first.status_code == 200, first.text
        assert first.json()["version"] == 1
        scenarios.append("revision conflict and validated version 1 publish")

        graph["nodes"][1]["config"]["mock_output"] = {"ok": False}  # type: ignore[index]
        saved = await owner.put(
            f"/api/v1/workspaces/{ws}/workflows/{workflow_id}/draft",
            headers=write,
            json={"revision": revision, "graph": graph},
        )
        assert saved.status_code == 200, saved.text
        revision = saved.json()["draft_revision"]
        second = await owner.post(
            f"/api/v1/workspaces/{ws}/workflows/{workflow_id}/publish",
            headers=write,
            json={"revision": revision},
        )
        assert second.status_code == 200, second.text
        versions = await owner.get(f"/api/v1/workspaces/{ws}/workflows/{workflow_id}/versions")
        assert versions.status_code == 200
        assert [v["version"] for v in versions.json()] == [2, 1]
        assert versions.json()[1]["checksum"] == first.json()["checksum"]
        assert versions.json()[1]["graph"] == first.json()["graph"]
        scenarios.append("new draft and version 2 do not change version 1")

        viewer_me = await sign_in(viewer, "viewer")
        await sign_in(editor, "editor")
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO memberships (workspace_id,user_id,role) "
                    "VALUES (:ws,:user,'viewer') ON CONFLICT DO NOTHING"
                ),
                {"ws": uuid.UUID(ws), "user": uuid.UUID(str(viewer_me["id"]))},
            )
        readable = await viewer.get(f"/api/v1/workspaces/{ws}/workflows/{workflow_id}")
        assert readable.status_code == 200, readable.text
        denied = await viewer.post(
            f"/api/v1/workspaces/{ws}/workflows",
            headers={"Origin": ORIGIN, "X-CSRF-Token": str(viewer_me["csrf_token"])},
            json={"name": "Denied"},
        )
        assert denied.status_code == 403, denied.text
        foreign = await editor.get(f"/api/v1/workspaces/{ws}/workflows/{workflow_id}")
        assert foreign.status_code == 404, foreign.text
        scenarios.append("viewer can read but cannot mutate; foreign workspace is masked")

        async with engine.connect() as connection:
            version_id = uuid.UUID(first.json()["id"])
            current = await connection.scalar(
                select(WorkflowVersion.graph_json).where(WorkflowVersion.id == version_id)
            )
            assert current == first.json()["graph"]
            try:
                await connection.execute(
                    text("UPDATE workflow_versions SET checksum = checksum WHERE id = :id"),
                    {"id": version_id},
                )
                raise AssertionError("Published version UPDATE should fail")
            except Exception as exc:
                assert "immutable" in str(exc).lower()
        scenarios.append("database trigger rejects published version mutation")

        spec = (await owner.get("/openapi.json")).json()
        assert "/api/v1/workspaces/{ws}/workflows/{workflow_id}/publish" in spec["paths"]
        assert "Graph" in spec["components"]["schemas"]
        artifact = json.loads(
            await asyncio.to_thread(
                Path("packages/workflow-schema/graph-v1.schema.json").read_text, encoding="utf-8"
            )
        )
        assert artifact == Graph.model_json_schema()
        scenarios.append("OpenAPI publishes versioned graph contract")
        result.update(
            {
                "status": "passed",
                "count": len(scenarios),
                "workspace_id": ws,
                "workflow_id": workflow_id,
                "environment": {"api": BASE, "postgres": "16.6", "identity": "local synthetic"},
            }
        )
    await engine.dispose()
    target = Path("artifacts/e2e/phase1-workflows/report.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        target.write_text, json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Phase 1: {len(scenarios)} scenarios passed; report: {target}")


if __name__ == "__main__":
    asyncio.run(main())
