"""Full-stack two-app event-to-callback E2E with synthetic data only."""

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

BASE = os.getenv("APP_E2E_API_URL", "http://127.0.0.1:8000")
ORIGIN = os.getenv("APP_E2E_WEB_ORIGIN", "http://localhost:3000")
EVIDENCE = Path("artifacts/e2e/app-connections")
REPORT = EVIDENCE / "report.json"
CALLBACKS = EVIDENCE / "callbacks.jsonl"
SCENARIOS: list[str] = []


def check(ok: bool, label: str) -> None:
    if not ok:
        raise AssertionError(label)
    SCENARIOS.append(label)


async def mutate(
    client: httpx.AsyncClient,
    path: str,
    csrf: str,
    body: dict[str, Any] | None = None,
    *,
    method: str = "POST",
) -> httpx.Response:
    return await client.request(
        method,
        path,
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json=body,
    )


def event_bytes(event_id: str, source: str, item_id: str) -> bytes:
    return json.dumps(
        {
            "specversion": "1.0",
            "id": event_id,
            "source": source,
            "type": "com.example.item.created.v1",
            "datacontenttype": "application/json",
            "data": {"item_id": item_id},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


async def deliver(client: httpx.AsyncClient, url: str, body: bytes, secret: str) -> httpx.Response:
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256)
    return await client.post(
        url,
        content=body,
        headers={
            "Content-Type": "application/cloudevents+json",
            "X-Automiq-Timestamp": stamp,
            "X-Automiq-Signature": "sha256=" + signature.hexdigest(),
        },
    )


async def wait_run(client: httpx.AsyncClient, ws: str, run_id: str) -> dict[str, Any]:
    for _ in range(60):
        response = await client.get(f"/api/v1/workspaces/{ws}/runs/{run_id}")
        if response.status_code == 200 and response.json()["status"] in {"succeeded", "failed"}:
            return response.json()
        await asyncio.sleep(1)
    raise AssertionError("App event run did not reach a terminal state")


def observed_callbacks() -> list[dict[str, Any]]:
    if not CALLBACKS.exists():
        return []
    return [json.loads(line) for line in CALLBACKS.read_text(encoding="utf-8").splitlines()]


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"item_id": {"type": "string"}},
        "required": ["item_id"],
        "additionalProperties": False,
    }


async def create_workflow(client: httpx.AsyncClient, ws: str, csrf: str, connection_id: str) -> str:
    created = await mutate(
        client, f"/api/v1/workspaces/{ws}/workflows", csrf, {"name": "App event relay"}
    )
    check(created.status_code == 201, "workflow created")
    workflow_id = created.json()["id"]
    base = f"/api/v1/workspaces/{ws}/workflows/{workflow_id}"
    graph = {
        "schema_version": "1.0",
        "nodes": [
            {"id": "start", "type": "trigger.app_event", "config": {}},
            {
                "id": "callback",
                "type": "action.app_callback",
                "config": {
                    "connection_id": connection_id,
                    "contract_version": 1,
                    "event_type": "com.automiq.item.completed.v1",
                    "data_paths": {"item_id": "trigger.payload.item_id"},
                },
            },
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "callback"},
            {"source": "callback", "target": "end"},
        ],
    }
    saved = await mutate(
        client, f"{base}/draft", csrf, {"revision": 0, "graph": graph}, method="PUT"
    )
    check(saved.status_code == 200, "custom app graph saved")
    published = await mutate(client, f"{base}/publish", csrf, {"revision": 1})
    check(published.status_code == 200, "custom app graph published")
    binding = await mutate(
        client,
        f"{base}/triggers/app",
        csrf,
        {
            "connection_id": connection_id,
            "event_type": "com.example.item.created.v1",
        },
    )
    check(binding.status_code == 201, "custom app event bound to published version")
    return workflow_id


async def run() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as owner:
        sign_in = await owner.post(
            "/auth/dev", headers={"Origin": ORIGIN}, json={"identity": "owner"}
        )
        check(sign_in.status_code == 200, "synthetic owner signed in")
        csrf = (await owner.get("/api/v1/me")).json()["csrf_token"]
        nonce = uuid.uuid4().hex[:10]
        created = await mutate(
            owner,
            "/api/v1/workspaces",
            csrf,
            {
                "name": f"App connection E2E {nonce}",
                "slug": f"app-e2e-{nonce}",
            },
        )
        check(created.status_code == 201, "workspace created")
        ws = created.json()["id"]
        connections: list[dict[str, Any]] = []
        run_ids: list[str] = []
        for index, callback_path in enumerate(("/app-one/events", "/app-two/retry"), start=1):
            body = {
                "display_name": f"Synthetic app {index}",
                "callback_url": f"https://fake-app:8443{callback_path}",
                "inbound_schemas": {"com.example.item.created.v1": schema()},
                "outbound_schemas": {"com.automiq.item.completed.v1": schema()},
            }
            response = await mutate(owner, f"/api/v1/workspaces/{ws}/app-connections", csrf, body)
            check(response.status_code == 201, f"app {index} connected through the same API")
            connection = response.json()
            connections.append(connection)
            listing = await owner.get(f"/api/v1/workspaces/{ws}/app-connections")
            check(
                listing.status_code == 200
                and "inbound_secret" not in listing.text
                and "outbound_secret" not in listing.text,
                f"app {index} listing redacts signing secrets",
            )
            await create_workflow(owner, ws, csrf, connection["id"])
            event_id = uuid.uuid4().hex
            raw = event_bytes(event_id, f"urn:synthetic:app:{index}", f"item-{nonce}-{index}")
            bad = await deliver(owner, connection["webhook_url"], raw, "wrong-local-key")
            check(bad.status_code == 401, f"app {index} forged signature rejected")
            accepted = await deliver(
                owner, connection["webhook_url"], raw, connection["inbound_secret"]
            )
            check(
                accepted.status_code == 202 and len(accepted.json()["run_ids"]) == 1,
                f"app {index} event queued one run",
            )
            run_id = accepted.json()["run_ids"][0]
            run_ids.append(run_id)
            duplicate = await deliver(
                owner, connection["webhook_url"], raw, connection["inbound_secret"]
            )
            check(
                duplicate.status_code == 202
                and duplicate.json()["duplicate"] is True
                and duplicate.json()["run_ids"] == [run_id],
                f"app {index} duplicate deduplicated",
            )
            conflict = await deliver(
                owner,
                connection["webhook_url"],
                event_bytes(event_id, f"urn:synthetic:app:{index}", "changed"),
                connection["inbound_secret"],
            )
            check(conflict.status_code == 409, f"app {index} conflicting event ID rejected")
            terminal = await wait_run(owner, ws, run_id)
            check(terminal["status"] == "succeeded", f"app {index} callback workflow completed")
            callbacks = [
                item
                for item in observed_callbacks()
                if item["data"].get("item_id") == f"item-{nonce}-{index}"
            ]
            check(len(callbacks) == 1, f"app {index} received one callback event")
            callback = callbacks[0]
            digest = hmac.new(
                connection["outbound_secret"].encode(),
                callback["timestamp"].encode() + b"." + callback["body"].encode(),
                hashlib.sha256,
            ).hexdigest()
            check(
                callback["signature"] == "sha256=" + digest,
                f"app {index} callback signature verified",
            )
            deliveries = await owner.get(
                f"/api/v1/workspaces/{ws}/app-connections/{connection['id']}/callbacks"
            )
            check(
                deliveries.status_code == 200
                and deliveries.json()[0]["status"] == "delivered"
                and deliveries.json()[0]["attempts"] >= (2 if index == 2 else 1),
                f"app {index} durable callback status and retry recorded",
            )
        async with httpx.AsyncClient(base_url=BASE, timeout=20) as outsider:
            await outsider.post(
                "/auth/dev", headers={"Origin": ORIGIN}, json={"identity": "viewer"}
            )
            masked = await outsider.get(f"/api/v1/workspaces/{ws}/app-connections")
            check(masked.status_code == 404, "foreign workspace app connections masked")
        rotated = await mutate(
            owner, f"/api/v1/workspaces/{ws}/app-connections/{connections[0]['id']}/rotate", csrf
        )
        check(
            rotated.status_code == 200 and rotated.json()["credential_version"] == 2,
            "connection signing secrets rotated",
        )
        rotate_body = event_bytes(uuid.uuid4().hex, "urn:synthetic:app:1", "after-rotation")
        old = await deliver(
            owner, connections[0]["webhook_url"], rotate_body, connections[0]["inbound_secret"]
        )
        check(old.status_code == 401, "old signing secret rejected after rotation")
        revoked = await mutate(
            owner, f"/api/v1/workspaces/{ws}/app-connections/{connections[1]['id']}/revoke", csrf
        )
        check(
            revoked.status_code == 200 and revoked.json()["revoked_at"] is not None,
            "connection revoked",
        )
        after_revoke = await deliver(
            owner,
            connections[1]["webhook_url"],
            event_bytes(uuid.uuid4().hex, "urn:synthetic:app:2", "revoked"),
            connections[1]["inbound_secret"],
        )
        check(after_revoke.status_code == 404, "revoked connection rejects new events")
        check(len(set(run_ids)) == 2, "two independent apps used one generic engine build")


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "environment": {
            "python": platform.python_version(),
            "api": BASE,
            "provider": "synthetic HTTPS callback receiver",
        },
        "scenarios": SCENARIOS,
        "status": "failed",
    }
    failed = False
    try:
        asyncio.run(run())
        result["status"] = "passed"
    except Exception as exc:
        result["failure"] = type(exc).__name__
        failed = True
    finally:
        result["count"] = len(SCENARIOS)
        REPORT.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"App connection E2E: {result['status']} ({result['count']} checks); {REPORT}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
