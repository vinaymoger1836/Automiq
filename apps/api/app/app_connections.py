"""Runtime-configured, workspace-scoped custom app event connections."""

import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.app_contract import (
    CloudEvent,
    DataSchema,
    validate_callback_url,
    validate_data,
    validate_event_types,
)
from app.auth import Db, UserDep, membership, require_csrf
from app.config import get_settings
from app.integration_crypto import active_key_version, decrypt_credentials, encrypt_credentials
from app.limits import admit_request, workspace_has_capacity
from app.models import (
    AppCallbackDelivery,
    AppConnection,
    AppEventReceipt,
    AuditLog,
    RunEvent,
    RunStartOutbox,
    Trigger,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)
from app.observability import current_traceparent

router = APIRouter(prefix="/api/v1", tags=["app-connections"])
MAX_EVENT_BYTES = 16_000


class ConnectionCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    callback_url: str | None = Field(default=None, max_length=500)
    inbound_schemas: dict[str, DataSchema] = Field(default_factory=dict, max_length=20)
    outbound_schemas: dict[str, DataSchema] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="after")
    def valid_connection(self) -> "ConnectionCreate":
        validate_event_types(self.inbound_schemas)
        validate_event_types(self.outbound_schemas)
        if not self.inbound_schemas and not self.outbound_schemas:
            raise ValueError("At least one event type is required")
        if self.outbound_schemas and not self.callback_url:
            raise ValueError("Outbound events need a callback URL")
        self.callback_url = validate_callback_url(
            self.callback_url, test_mode=get_settings().app_env == "test"
        )
        return self


class ConnectionOut(BaseModel):
    id: uuid.UUID
    display_name: str
    callback_url: str | None
    webhook_url: str
    inbound_schemas: dict[str, Any]
    outbound_schemas: dict[str, Any]
    contract_version: int
    credential_version: int
    revoked_at: datetime | None


class ConnectionCreated(ConnectionOut):
    inbound_secret: str
    outbound_secret: str


class AppTriggerCreate(BaseModel):
    connection_id: uuid.UUID
    event_type: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,79}$")


class AppTriggerOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    version_id: uuid.UUID
    connection_id: uuid.UUID
    event_type: str
    enabled: bool


class EventAccepted(BaseModel):
    event_id: str
    run_ids: list[uuid.UUID]
    status: str
    duplicate: bool


class CallbackOut(BaseModel):
    event_id: str
    run_id: uuid.UUID
    node_id: str
    status: str
    attempts: int
    last_error: str | None
    delivered_at: datetime | None


def connection_out(row: AppConnection) -> ConnectionOut:
    return ConnectionOut(
        id=row.id,
        display_name=row.display_name,
        callback_url=row.callback_url,
        webhook_url=f"{get_settings().api_public_url.rstrip('/')}/api/v1/app-connections/{row.public_id}/events",
        inbound_schemas=row.inbound_schemas,
        outbound_schemas=row.outbound_schemas,
        contract_version=row.contract_version,
        credential_version=row.credential_version,
        revoked_at=row.revoked_at,
    )


async def scoped_connection(db: Db, ws: uuid.UUID, connection_id: uuid.UUID) -> AppConnection:
    row = await db.scalar(
        select(AppConnection).where(
            AppConnection.id == connection_id, AppConnection.workspace_id == ws
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="App connection not found")
    return row


@router.post("/workspaces/{ws}/app-connections", response_model=ConnectionCreated, status_code=201)
async def create_connection(
    ws: uuid.UUID, body: ConnectionCreate, request: Request, db: Db, user: UserDep
) -> ConnectionCreated:
    require_csrf(request)
    await membership(db, ws, user, "manage")
    inbound_secret = secrets.token_urlsafe(32)
    outbound_secret = secrets.token_urlsafe(32)
    connection_id = uuid.uuid4()
    key_version = active_key_version()
    row = AppConnection(
        id=connection_id,
        public_id=secrets.token_urlsafe(24),
        workspace_id=ws,
        display_name=body.display_name.strip(),
        callback_url=body.callback_url,
        inbound_schemas={
            key: schema.model_dump(mode="json", by_alias=True)
            for key, schema in body.inbound_schemas.items()
        },
        outbound_schemas={
            key: schema.model_dump(mode="json", by_alias=True)
            for key, schema in body.outbound_schemas.items()
        },
        key_version=key_version,
        encrypted_credentials=encrypt_credentials(
            ws,
            connection_id,
            "custom_app",
            {"inbound_secret": inbound_secret, "outbound_secret": outbound_secret},
            key_version,
        ),
    )
    db.add(row)
    db.add(
        AuditLog(
            workspace_id=ws,
            actor_type="user",
            actor_id=user.id,
            action="app_connection.create",
            resource_type="app_connections",
            resource_id=row.id,
            metadata_json={},
        )
    )
    await db.commit()
    return ConnectionCreated(
        **connection_out(row).model_dump(),
        inbound_secret=inbound_secret,
        outbound_secret=outbound_secret,
    )


@router.get("/workspaces/{ws}/app-connections", response_model=list[ConnectionOut])
async def list_connections(ws: uuid.UUID, db: Db, user: UserDep) -> list[ConnectionOut]:
    await membership(db, ws, user, "read")
    rows = await db.scalars(
        select(AppConnection)
        .where(AppConnection.workspace_id == ws)
        .order_by(AppConnection.created_at)
        .limit(100)
    )
    return [connection_out(row) for row in rows]


@router.post(
    "/workspaces/{ws}/app-connections/{connection_id}/rotate", response_model=ConnectionCreated
)
async def rotate_connection(
    ws: uuid.UUID, connection_id: uuid.UUID, request: Request, db: Db, user: UserDep
) -> ConnectionCreated:
    require_csrf(request)
    await membership(db, ws, user, "manage")
    row = await db.scalar(
        select(AppConnection)
        .where(AppConnection.id == connection_id, AppConnection.workspace_id == ws)
        .with_for_update()
    )
    if row is None or row.revoked_at is not None:
        raise HTTPException(status_code=404, detail="App connection not found")
    inbound_secret = secrets.token_urlsafe(32)
    outbound_secret = secrets.token_urlsafe(32)
    row.key_version = active_key_version()
    row.credential_version += 1
    row.encrypted_credentials = encrypt_credentials(
        ws,
        row.id,
        "custom_app",
        {"inbound_secret": inbound_secret, "outbound_secret": outbound_secret},
        row.key_version,
    )
    db.add(
        AuditLog(
            workspace_id=ws,
            actor_type="user",
            actor_id=user.id,
            action="app_connection.rotate",
            resource_type="app_connections",
            resource_id=row.id,
            metadata_json={"credential_version": row.credential_version},
        )
    )
    await db.commit()
    return ConnectionCreated(
        **connection_out(row).model_dump(),
        inbound_secret=inbound_secret,
        outbound_secret=outbound_secret,
    )


@router.post(
    "/workspaces/{ws}/app-connections/{connection_id}/revoke", response_model=ConnectionOut
)
async def revoke_connection(
    ws: uuid.UUID, connection_id: uuid.UUID, request: Request, db: Db, user: UserDep
) -> ConnectionOut:
    require_csrf(request)
    await membership(db, ws, user, "manage")
    row = await scoped_connection(db, ws, connection_id)
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        db.add(
            AuditLog(
                workspace_id=ws,
                actor_type="user",
                actor_id=user.id,
                action="app_connection.revoke",
                resource_type="app_connections",
                resource_id=row.id,
                metadata_json={},
            )
        )
        await db.commit()
    return connection_out(row)


@router.post(
    "/workspaces/{ws}/workflows/{workflow_id}/triggers/app",
    response_model=AppTriggerOut,
    status_code=201,
)
async def bind_app_trigger(
    ws: uuid.UUID,
    workflow_id: uuid.UUID,
    body: AppTriggerCreate,
    request: Request,
    db: Db,
    user: UserDep,
) -> AppTriggerOut:
    require_csrf(request)
    await membership(db, ws, user, "write")
    connection = await scoped_connection(db, ws, body.connection_id)
    workflow = await db.scalar(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.workspace_id == ws)
    )
    if connection.revoked_at is not None or body.event_type not in connection.inbound_schemas:
        raise HTTPException(status_code=422, detail="Connection does not accept this event type")
    if workflow is None or workflow.status != "active" or workflow.published_version_id is None:
        raise HTTPException(status_code=409, detail="Publish an active workflow first")
    version = await db.get(WorkflowVersion, workflow.published_version_id)
    if version is None or not any(
        node["type"] == "trigger.app_event" for node in version.graph_json["nodes"]
    ):
        raise HTTPException(status_code=409, detail="Published graph needs an app event trigger")
    row = Trigger(
        public_id=secrets.token_urlsafe(24),
        workspace_id=ws,
        workflow_id=workflow_id,
        version_id=version.id,
        type="app.event",
        config_json={
            "connection_id": str(connection.id),
            "event_type": body.event_type,
            "contract_version": connection.contract_version,
        },
    )
    db.add(row)
    await db.commit()
    return AppTriggerOut(
        id=row.id,
        workflow_id=workflow_id,
        version_id=version.id,
        connection_id=connection.id,
        event_type=body.event_type,
        enabled=row.enabled,
    )


def verify_signed_request(request: Request, raw: bytes, secret: str) -> None:
    stamp = request.headers.get("x-automiq-timestamp", "")
    signature = request.headers.get("x-automiq-signature", "")
    if not stamp.isdecimal() or len(stamp) > 12 or abs(time.time() - int(stamp)) > 300:
        raise HTTPException(status_code=401, detail="Invalid event timestamp")
    expected = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, "sha256=" + expected):
        raise HTTPException(status_code=401, detail="Invalid event signature")


@router.post("/app-connections/{public_id}/events", response_model=EventAccepted, status_code=202)
async def receive_event(public_id: str, request: Request, db: Db) -> EventAccepted:
    if len(public_id) > 64 or not public_id:
        raise HTTPException(status_code=404, detail="App connection not found")
    connection = await db.scalar(
        select(AppConnection).where(
            AppConnection.public_id == public_id, AppConnection.revoked_at.is_(None)
        )
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="App connection not found")
    length = request.headers.get("content-length")
    if length and (not length.isdecimal() or int(length) > MAX_EVENT_BYTES):
        raise HTTPException(status_code=413, detail="Event too large")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_EVENT_BYTES:
            raise HTTPException(status_code=413, detail="Event too large")
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/cloudevents+json":
        raise HTTPException(status_code=415, detail="CloudEvents JSON required")
    credentials = decrypt_credentials(
        connection.workspace_id,
        connection.id,
        "custom_app",
        connection.encrypted_credentials,
        connection.key_version,
    )
    verified_version = connection.credential_version
    verify_signed_request(request, bytes(raw), credentials["inbound_secret"])
    try:
        event = CloudEvent.model_validate_json(raw)
        schema = DataSchema.model_validate(connection.inbound_schemas[event.type])
        validate_data(event.data, schema)
    except (ValidationError, ValueError, KeyError):
        raise HTTPException(
            status_code=422, detail="Event does not match connection contract"
        ) from None
    digest = hashlib.sha256(raw).hexdigest()
    locked = await db.scalar(
        select(AppConnection)
        .where(AppConnection.id == connection.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        locked is None
        or locked.revoked_at is not None
        or locked.credential_version != verified_version
    ):
        raise HTTPException(status_code=409, detail="Connection changed during event intake")
    existing = await db.scalar(
        select(AppEventReceipt).where(
            AppEventReceipt.connection_id == connection.id,
            AppEventReceipt.source == event.source,
            AppEventReceipt.event_id == event.id,
        )
    )
    if existing is not None:
        if existing.payload_hash != digest:
            raise HTTPException(status_code=409, detail="Event ID reused with different payload")
        return EventAccepted(
            event_id=event.id,
            run_ids=[uuid.UUID(item) for item in existing.run_ids_json],
            status="accepted",
            duplicate=True,
        )
    await admit_request("app_event", connection.id, get_settings().app_events_per_minute)
    if not await workspace_has_capacity(db, connection.workspace_id):
        raise HTTPException(status_code=429, detail="Workspace active run limit exceeded")
    candidates = await db.scalars(
        select(Trigger).where(
            Trigger.workspace_id == connection.workspace_id,
            Trigger.type == "app.event",
            Trigger.enabled.is_(True),
        )
    )
    bindings = [
        row
        for row in candidates
        if row.config_json.get("connection_id") == str(connection.id)
        and row.config_json.get("event_type") == event.type
        and row.config_json.get("contract_version") == connection.contract_version
    ]
    if len(bindings) > 25:
        raise HTTPException(status_code=429, detail="Too many event subscriptions")
    receipt = AppEventReceipt(
        connection_id=connection.id,
        source=event.source,
        event_id=event.id,
        payload_hash=digest,
        run_ids_json=[],
    )
    db.add(receipt)
    for binding in bindings:
        workflow = await db.get(Workflow, binding.workflow_id)
        if workflow is None or workflow.status != "active":
            continue
        run_id = uuid.uuid4()
        payload = {"payload": event.data}
        db.add(
            WorkflowRun(
                id=run_id,
                workspace_id=connection.workspace_id,
                workflow_id=binding.workflow_id,
                version_id=binding.version_id,
                temporal_workflow_id=f"run:{run_id}",
                idempotency_key=f"app:{binding.id}:{receipt.id}",
                request_hash=hashlib.sha256(
                    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                traceparent=current_traceparent(),
                input_json=payload,
                status="queued",
            )
        )
        db.add(RunStartOutbox(run_id=run_id))
        db.add(
            RunEvent(
                workspace_id=connection.workspace_id,
                run_id=run_id,
                event_key="queued",
                type="run.queued",
                payload_json={"status": "queued"},
            )
        )
        receipt.run_ids_json = [*receipt.run_ids_json, str(run_id)]
    db.add(
        AuditLog(
            workspace_id=connection.workspace_id,
            actor_type="service",
            actor_id=None,
            action="app_event.accept",
            resource_type="app_event_receipts",
            resource_id=receipt.id,
            metadata_json={"type": event.type, "run_count": len(receipt.run_ids_json)},
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Event intake conflict") from None
    return EventAccepted(
        event_id=event.id,
        run_ids=[uuid.UUID(item) for item in receipt.run_ids_json],
        status="accepted",
        duplicate=False,
    )


@router.get(
    "/workspaces/{ws}/app-connections/{connection_id}/callbacks",
    response_model=list[CallbackOut],
)
async def list_callbacks(
    ws: uuid.UUID, connection_id: uuid.UUID, db: Db, user: UserDep
) -> list[CallbackOut]:
    await membership(db, ws, user, "read")
    await scoped_connection(db, ws, connection_id)
    rows = await db.scalars(
        select(AppCallbackDelivery)
        .where(
            AppCallbackDelivery.workspace_id == ws,
            AppCallbackDelivery.connection_id == connection_id,
        )
        .order_by(AppCallbackDelivery.created_at.desc())
        .limit(100)
    )
    return [
        CallbackOut(
            event_id=row.event_id,
            run_id=row.run_id,
            node_id=row.node_id,
            status=row.status,
            attempts=row.attempts,
            last_error=row.last_error,
            delivered_at=row.delivered_at,
        )
        for row in rows
    ]
