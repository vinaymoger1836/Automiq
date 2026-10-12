"""Signed, idempotent custom-app callbacks from a fixed connection endpoint."""

import asyncio
import hashlib
import hmac
import json
import ssl
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from app.app_contract import DataSchema, validate_data
from app.config import get_settings
from app.db import session_factory
from app.integration_crypto import decrypt_credentials
from app.models import AppCallbackDelivery, AppConnection, AuditLog, WorkflowRun
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from orchestrator.http_connector import HttpActionError, pinned_address


class CallbackError(Exception):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


async def post_callback(url: str, body: bytes, secret: str) -> None:
    settings = get_settings()
    parsed = urlsplit(url)
    host = parsed.hostname
    if host is None or parsed.scheme != "https":
        raise CallbackError("unsafe_destination")
    port = parsed.port or 443
    try:
        address = await pinned_address(host, port, settings)
        context = ssl.create_default_context(cafile=settings.app_callback_test_ca_file or None)
        stamp = str(int(time.time()))
        signature = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256)
        authority = f"[{address}]" if ":" in address else address
        host_header = host if port == 443 else f"{host}:{port}"
        path = parsed.path or "/"
        async with asyncio.timeout(12):
            async with httpx.AsyncClient(
                verify=context,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(10, connect=3),
            ) as client:
                async with client.stream(
                    "POST",
                    f"https://{authority}:{port}{path}",
                    content=body,
                    headers={
                        "Host": host_header,
                        "Content-Type": "application/cloudevents+json",
                        "X-Automiq-Timestamp": stamp,
                        "X-Automiq-Signature": "sha256=" + signature.hexdigest(),
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    },
                    extensions={"sni_hostname": host},
                ) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        raise CallbackError("callback_unavailable", retryable=True)
                    if not 200 <= response.status_code < 300:
                        raise CallbackError("callback_rejected")
    except HttpActionError as exc:
        raise CallbackError(exc.code, retryable=exc.retryable) from None
    except (TimeoutError, httpx.TransportError):
        raise CallbackError("callback_transport", retryable=True) from None
    except OSError:
        raise CallbackError("callback_tls_configuration") from None


@activity.defn(name="execute_app_callback")
async def execute_app_callback(command: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(command["run_id"])
    node_id = str(command["node_id"])
    config = command["config"]
    connection_id = uuid.UUID(config["connection_id"])
    event_type = str(config["event_type"])
    data = command["data"]
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"automiq:{run_id}:{node_id}"))
    async with session_factory()() as db:
        run = await db.get(WorkflowRun, run_id)
        if run is None:
            raise ApplicationError("Run missing", non_retryable=True)
        connection = await db.scalar(
            select(AppConnection).where(
                AppConnection.id == connection_id,
                AppConnection.workspace_id == run.workspace_id,
                AppConnection.revoked_at.is_(None),
            )
        )
        if (
            connection is None
            or connection.callback_url is None
            or connection.contract_version != config["contract_version"]
            or event_type not in connection.outbound_schemas
        ):
            raise ApplicationError("App connection unavailable", non_retryable=True)
        try:
            validate_data(data, DataSchema.model_validate(connection.outbound_schemas[event_type]))
        except ValueError:
            raise ApplicationError("Callback data invalid", non_retryable=True) from None
        credentials = decrypt_credentials(
            run.workspace_id,
            connection.id,
            "custom_app",
            connection.encrypted_credentials,
            connection.key_version,
        )
        url = connection.callback_url
        workspace_id = run.workspace_id
        secret = credentials["outbound_secret"]
        envelope = {
            "specversion": "1.0",
            "id": event_id,
            "source": f"urn:automiq:workspace:{workspace_id}",
            "type": event_type,
            "subject": f"runs/{run_id}",
            "datacontenttype": "application/json",
            "data": data,
        }
        body = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        if len(body) > 16_000:
            raise ApplicationError("Callback too large", non_retryable=True)
        async with db.begin_nested():
            delivery = await db.scalar(
                select(AppCallbackDelivery)
                .where(
                    AppCallbackDelivery.run_id == run_id,
                    AppCallbackDelivery.node_id == node_id,
                )
                .with_for_update()
            )
            if delivery is not None and delivery.status == "delivered":
                return {
                    "output": {"delivered": True, "event_id": event_id},
                    "attempts": delivery.attempts,
                }
            if delivery is None:
                delivery = AppCallbackDelivery(
                    workspace_id=workspace_id,
                    connection_id=connection_id,
                    run_id=run_id,
                    node_id=node_id,
                    event_id=event_id,
                    status="pending",
                    attempts=0,
                )
                db.add(delivery)
            delivery.attempts += 1
            attempts = delivery.attempts
        await db.commit()
    try:
        await post_callback(url, body, secret)
    except CallbackError as exc:
        async with session_factory()() as db:
            async with db.begin():
                delivery = await db.scalar(
                    select(AppCallbackDelivery)
                    .where(
                        AppCallbackDelivery.run_id == run_id, AppCallbackDelivery.node_id == node_id
                    )
                    .with_for_update()
                )
                if delivery is not None:
                    delivery.status = "pending" if exc.retryable and attempts < 3 else "failed"
                    delivery.last_error = exc.code
        raise ApplicationError(exc.code, non_retryable=not exc.retryable) from None
    async with session_factory()() as db:
        async with db.begin():
            delivery = await db.scalar(
                select(AppCallbackDelivery)
                .where(AppCallbackDelivery.run_id == run_id, AppCallbackDelivery.node_id == node_id)
                .with_for_update()
            )
            if delivery is None:
                raise ApplicationError("Callback delivery missing", non_retryable=True)
            delivery.status = "delivered"
            delivery.last_error = None
            delivery.delivered_at = datetime.now(UTC)
            db.add(
                AuditLog(
                    workspace_id=workspace_id,
                    actor_type="service",
                    actor_id=None,
                    action="app_callback.delivered",
                    resource_type="app_callback_deliveries",
                    resource_id=delivery.id,
                    metadata_json={"event_type": event_type, "run_id": str(run_id)},
                )
            )
    return {"output": {"delivered": True, "event_id": event_id}, "attempts": attempts}
