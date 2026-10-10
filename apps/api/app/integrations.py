"""Workspace integrations and authenticated GitHub issue deliveries."""

import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import available_timezones

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import Db, UserDep, membership, require_csrf
from app.config import get_settings
from app.integration_crypto import active_key_version, decrypt_credentials, encrypt_credentials
from app.models import (
    AuditLog,
    Integration,
    IntegrationAssignment,
    IssueContent,
    Membership,
    RunEvent,
    RunStartOutbox,
    Trigger,
    WebhookDelivery,
    Workflow,
    WorkflowRun,
    WorkflowVersion,
)
from app.runs import redact

router = APIRouter(prefix="/api/v1", tags=["integrations"])


class IntegrationCreate(BaseModel):
    provider: Literal["github", "slack"]
    display_name: str = Field(min_length=1, max_length=120)
    token: SecretStr = Field(min_length=8, max_length=4096)
    webhook_secret: SecretStr | None = Field(default=None, min_length=16, max_length=4096)
    repository: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", max_length=200
    )

    @model_validator(mode="after")
    def provider_fields(self) -> "IntegrationCreate":
        if self.provider == "github" and (not self.webhook_secret or not self.repository):
            raise ValueError("GitHub needs a webhook secret and repository")
        if self.provider == "slack" and (self.webhook_secret or self.repository):
            raise ValueError("Slack only needs a token")
        return self


class IntegrationOut(BaseModel):
    id: uuid.UUID
    provider: str
    display_name: str
    key_version: int
    credential_version: int
    revoked_at: datetime | None
    rotated_at: datetime | None
    created_at: datetime
    can_configure: bool


class IntegrationAssign(BaseModel):
    user_id: uuid.UUID


class TriggerCreate(BaseModel):
    integration_id: uuid.UUID


class ScheduleCreate(BaseModel):
    cron: str = Field(min_length=9, max_length=100)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)

    @model_validator(mode="after")
    def valid_schedule(self) -> "ScheduleCreate":
        fields = self.cron.split()
        limits = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
        if len(fields) != 5 or any(
            not valid_cron_field(field, *bounds)
            for field, bounds in zip(fields, limits, strict=True)
        ):
            raise ValueError("Use a five-field numeric cron expression")
        if not re.fullmatch(r"[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)*", self.timezone):
            raise ValueError("Invalid IANA timezone name")
        known = available_timezones()
        if known and self.timezone not in known and self.timezone != "UTC":
            raise ValueError("Unknown IANA timezone")
        return self


def valid_cron_field(value: str, lower: int, upper: int) -> bool:
    for piece in value.split(","):
        base, separator, step = piece.partition("/")
        if separator and (not step.isdecimal() or not 1 <= int(step) <= upper - lower + 1):
            return False
        if base == "*":
            continue
        start, dash, end = base.partition("-")
        if not start.isdecimal() or not lower <= int(start) <= upper:
            return False
        if dash:
            if not end.isdecimal() or not int(start) <= int(end) <= upper:
                return False
        elif separator:
            return False
    return bool(value)


class TriggerOut(BaseModel):
    id: uuid.UUID
    public_id: str
    workflow_id: uuid.UUID
    version_id: uuid.UUID
    integration_id: uuid.UUID | None
    type: str
    enabled: bool
    webhook_path: str | None
    webhook_url: str | None
    config: dict[str, str]


class GithubRepo(BaseModel):
    full_name: str = Field(min_length=3, max_length=200)


class GithubIssue(BaseModel):
    number: int = Field(gt=0)
    state: Literal["open", "closed"]
    title: str | None = None
    body: str | None = None


class GithubIssueEvent(BaseModel):
    action: Literal["opened", "edited", "reopened", "closed"]
    repository: GithubRepo
    issue: GithubIssue


class DeliveryOut(BaseModel):
    run_id: uuid.UUID | None
    status: Literal["queued", "ignored"]
    duplicate: bool


class AuditOut(BaseModel):
    action: str
    resource_type: str
    resource_id: uuid.UUID | None
    metadata: dict[str, object]
    created_at: datetime


def integration_out(row: Integration, *, can_configure: bool = True) -> IntegrationOut:
    return IntegrationOut(
        id=row.id,
        provider=row.provider,
        display_name=row.display_name,
        key_version=row.key_version,
        credential_version=row.credential_version,
        revoked_at=row.revoked_at,
        rotated_at=row.rotated_at,
        created_at=row.created_at,
        can_configure=can_configure,
    )


def trigger_out(row: Trigger) -> TriggerOut:
    path = f"/api/v1/webhooks/github/{row.public_id}" if row.type == "github.issue" else None
    return TriggerOut(
        id=row.id,
        public_id=row.public_id,
        workflow_id=row.workflow_id,
        version_id=row.version_id,
        integration_id=row.integration_id,
        type=row.type,
        enabled=row.enabled,
        webhook_path=path,
        webhook_url=f"{get_settings().api_public_url.rstrip('/')}{path}" if path else None,
        config=row.config_json,
    )


def credentials(body: IntegrationCreate) -> dict[str, str]:
    values = {"token": body.token.get_secret_value()}
    if body.provider == "github":
        assert body.webhook_secret and body.repository
        values["webhook_secret"] = body.webhook_secret.get_secret_value()
        values["repository"] = body.repository
    return values


def audit(
    ws: uuid.UUID, actor: uuid.UUID | None, action: str, resource: str, rid: uuid.UUID
) -> AuditLog:
    return AuditLog(
        workspace_id=ws,
        actor_type="user" if actor else "service",
        actor_id=actor,
        action=action,
        resource_type=resource,
        resource_id=rid,
        metadata_json={},
    )


@router.post("/workspaces/{ws}/integrations", response_model=IntegrationOut, status_code=201)
async def create_integration(
    ws: uuid.UUID, body: IntegrationCreate, request: Request, db: Db, user: UserDep
) -> IntegrationOut:
    require_csrf(request)
    await membership(db, ws, user, "manage")
    row = Integration(
        id=uuid.uuid4(),
        workspace_id=ws,
        provider=body.provider,
        display_name=body.display_name.strip(),
        encrypted_credentials=b"",
        key_version=active_key_version(),
    )
    if not row.display_name:
        raise HTTPException(status_code=422, detail="Name is required")
    row.encrypted_credentials = encrypt_credentials(
        ws, row.id, row.provider, credentials(body), row.key_version
    )
    db.add(row)
    db.add(audit(ws, user.id, "integration.create", "integrations", row.id))
    await db.commit()
    await db.refresh(row)
    return integration_out(row)


@router.get("/workspaces/{ws}/integrations", response_model=list[IntegrationOut])
async def list_integrations(ws: uuid.UUID, db: Db, user: UserDep) -> list[IntegrationOut]:
    access = await membership(db, ws, user, "read")
    rows = await db.scalars(
        select(Integration).where(Integration.workspace_id == ws).order_by(Integration.created_at)
    )
    assigned = (
        set(
            await db.scalars(
                select(IntegrationAssignment.integration_id).where(
                    IntegrationAssignment.workspace_id == ws,
                    IntegrationAssignment.user_id == user.id,
                )
            )
        )
        if access.role == "editor"
        else set()
    )
    return [
        integration_out(row, can_configure=access.role == "owner" or row.id in assigned)
        for row in rows
    ]


@router.post("/workspaces/{ws}/integrations/{integration_id}/assign", response_model=IntegrationOut)
async def assign_integration(
    ws: uuid.UUID,
    integration_id: uuid.UUID,
    body: IntegrationAssign,
    request: Request,
    db: Db,
    user: UserDep,
) -> IntegrationOut:
    require_csrf(request)
    await membership(db, ws, user, "manage")
    integration = await db.scalar(
        select(Integration).where(
            Integration.id == integration_id,
            Integration.workspace_id == ws,
            Integration.revoked_at.is_(None),
        )
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="Integration not found")
    member = await db.get(Membership, (ws, body.user_id))
    if member is None or member.role != "editor":
        raise HTTPException(status_code=422, detail="Assign an editor in this workspace")
    existing = await db.get(IntegrationAssignment, (integration_id, body.user_id))
    if existing is None:
        db.add(
            IntegrationAssignment(
                integration_id=integration_id,
                workspace_id=ws,
                user_id=body.user_id,
            )
        )
        db.add(audit(ws, user.id, "integration.assign", "integrations", integration_id))
        await db.commit()
    return integration_out(integration)


@router.get("/workspaces/{ws}/audit-logs", response_model=list[AuditOut])
async def list_audit_logs(ws: uuid.UUID, db: Db, user: UserDep) -> list[AuditOut]:
    await membership(db, ws, user, "manage")
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.workspace_id == ws)
        .order_by(AuditLog.created_at.desc())
        .limit(100)
    )
    return [
        AuditOut(
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            metadata=redact(row.metadata_json),
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.put("/workspaces/{ws}/integrations/{integration_id}", response_model=IntegrationOut)
async def rotate_integration(
    ws: uuid.UUID,
    integration_id: uuid.UUID,
    body: IntegrationCreate,
    request: Request,
    db: Db,
    user: UserDep,
) -> IntegrationOut:
    require_csrf(request)
    access = await membership(db, ws, user, "read")
    if access.role != "owner":
        assigned = await db.get(IntegrationAssignment, (integration_id, user.id))
        if access.role != "editor" or assigned is None or assigned.workspace_id != ws:
            raise HTTPException(status_code=403, detail="Integration permission denied")
    row = await db.scalar(
        select(Integration)
        .where(Integration.id == integration_id, Integration.workspace_id == ws)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Integration not found")
    if row.provider != body.provider or row.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Integration cannot be rotated")
    row.key_version = active_key_version()
    row.encrypted_credentials = encrypt_credentials(
        ws, row.id, row.provider, credentials(body), row.key_version
    )
    row.display_name = body.display_name.strip()
    row.credential_version += 1
    row.rotated_at = datetime.now(UTC)
    db.add(audit(ws, user.id, "integration.rotate", "integrations", row.id))
    await db.commit()
    return integration_out(row)


@router.post("/workspaces/{ws}/integrations/{integration_id}/rewrap", response_model=IntegrationOut)
async def rewrap_integration(
    ws: uuid.UUID,
    integration_id: uuid.UUID,
    request: Request,
    db: Db,
    user: UserDep,
) -> IntegrationOut:
    require_csrf(request)
    await membership(db, ws, user, "manage")
    row = await db.scalar(
        select(Integration)
        .where(
            Integration.id == integration_id,
            Integration.workspace_id == ws,
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Integration not found")
    if row.key_version != active_key_version():
        values = decrypt_credentials(
            ws, row.id, row.provider, row.encrypted_credentials, row.key_version
        )
        row.key_version = active_key_version()
        row.encrypted_credentials = encrypt_credentials(
            ws, row.id, row.provider, values, row.key_version
        )
        db.add(audit(ws, user.id, "integration.rewrap", "integrations", row.id))
        await db.commit()
    return integration_out(row)


@router.post("/workspaces/{ws}/integrations/{integration_id}/revoke", response_model=IntegrationOut)
async def revoke_integration(
    ws: uuid.UUID,
    integration_id: uuid.UUID,
    request: Request,
    db: Db,
    user: UserDep,
) -> IntegrationOut:
    require_csrf(request)
    await membership(db, ws, user, "manage")
    row = await db.scalar(
        select(Integration)
        .where(Integration.id == integration_id, Integration.workspace_id == ws)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Integration not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        db.add(audit(ws, user.id, "integration.revoke", "integrations", row.id))
        await db.commit()
    return integration_out(row)


@router.post(
    "/workspaces/{ws}/workflows/{workflow_id}/triggers/github",
    response_model=TriggerOut,
    status_code=201,
)
async def create_github_trigger(
    ws: uuid.UUID,
    workflow_id: uuid.UUID,
    body: TriggerCreate,
    request: Request,
    db: Db,
    user: UserDep,
) -> TriggerOut:
    require_csrf(request)
    await membership(db, ws, user, "write")
    workflow = await db.scalar(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.workspace_id == ws)
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if workflow.status != "active" or workflow.published_version_id is None:
        raise HTTPException(status_code=409, detail="Publish an active workflow first")
    version = await db.get(WorkflowVersion, workflow.published_version_id)
    if version is None or not any(
        node["type"] == "trigger.github_issue" for node in version.graph_json["nodes"]
    ):
        raise HTTPException(status_code=409, detail="Published graph needs a GitHub issue trigger")
    integration = await db.scalar(
        select(Integration).where(
            Integration.id == body.integration_id, Integration.workspace_id == ws
        )
    )
    if (
        integration is None
        or integration.provider != "github"
        or integration.revoked_at is not None
    ):
        raise HTTPException(status_code=404, detail="GitHub integration not found")
    row = Trigger(
        id=uuid.uuid4(),
        public_id=secrets.token_urlsafe(24),
        workspace_id=ws,
        workflow_id=workflow_id,
        version_id=version.id,
        integration_id=integration.id,
        type="github.issue",
        config_json={},
        enabled=True,
    )
    db.add(row)
    db.add(audit(ws, user.id, "trigger.create", "triggers", row.id))
    await db.commit()
    return trigger_out(row)


@router.post(
    "/workspaces/{ws}/workflows/{workflow_id}/triggers/schedule",
    response_model=TriggerOut,
    status_code=201,
)
async def create_schedule_trigger(
    ws: uuid.UUID,
    workflow_id: uuid.UUID,
    body: ScheduleCreate,
    request: Request,
    db: Db,
    user: UserDep,
) -> TriggerOut:
    require_csrf(request)
    await membership(db, ws, user, "write")
    workflow = await db.scalar(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.workspace_id == ws)
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if workflow.status != "active" or workflow.published_version_id is None:
        raise HTTPException(status_code=409, detail="Publish an active workflow first")
    version = await db.get(WorkflowVersion, workflow.published_version_id)
    if version is None or not any(
        node["type"] == "trigger.schedule" for node in version.graph_json["nodes"]
    ):
        raise HTTPException(status_code=409, detail="Published graph needs a schedule trigger")
    row = Trigger(
        id=uuid.uuid4(),
        public_id=secrets.token_urlsafe(24),
        workspace_id=ws,
        workflow_id=workflow_id,
        version_id=version.id,
        integration_id=None,
        type="schedule",
        config_json=body.model_dump(),
        enabled=True,
    )
    db.add(row)
    db.add(audit(ws, user.id, "trigger.create", "triggers", row.id))
    await db.commit()
    return trigger_out(row)


@router.post(
    "/workspaces/{ws}/workflows/{workflow_id}/triggers/{trigger_id}/disable",
    response_model=TriggerOut,
)
async def disable_trigger(
    ws: uuid.UUID,
    workflow_id: uuid.UUID,
    trigger_id: uuid.UUID,
    request: Request,
    db: Db,
    user: UserDep,
) -> TriggerOut:
    require_csrf(request)
    await membership(db, ws, user, "write")
    row = await db.scalar(
        select(Trigger)
        .where(
            Trigger.id == trigger_id,
            Trigger.workspace_id == ws,
            Trigger.workflow_id == workflow_id,
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    if row.enabled:
        row.enabled = False
        db.add(audit(ws, user.id, "trigger.disable", "triggers", row.id))
        await db.commit()
    return trigger_out(row)


@router.get("/workspaces/{ws}/workflows/{workflow_id}/triggers", response_model=list[TriggerOut])
async def list_triggers(
    ws: uuid.UUID, workflow_id: uuid.UUID, db: Db, user: UserDep
) -> list[TriggerOut]:
    await membership(db, ws, user, "read")
    workflow = await db.scalar(
        select(Workflow.id).where(Workflow.id == workflow_id, Workflow.workspace_id == ws)
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    rows = await db.scalars(
        select(Trigger)
        .where(Trigger.workspace_id == ws, Trigger.workflow_id == workflow_id)
        .order_by(Trigger.created_at)
    )
    return [trigger_out(row) for row in rows]


@router.post("/webhooks/github/{public_id}", response_model=DeliveryOut, status_code=202)
async def github_webhook(public_id: str, request: Request, db: Db) -> DeliveryOut:
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", public_id):
        raise HTTPException(status_code=404, detail="Trigger not found")
    trigger = await db.scalar(
        select(Trigger).where(
            Trigger.public_id == public_id,
            Trigger.type == "github.issue",
            Trigger.enabled.is_(True),
        )
    )
    if trigger is None or trigger.integration_id is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    integration = await db.scalar(
        select(Integration).where(
            Integration.id == trigger.integration_id,
            Integration.workspace_id == trigger.workspace_id,
        )
    )
    if integration is None or integration.revoked_at is not None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    verified_credential_version = integration.credential_version
    verified_key_version = integration.key_version
    declared_length = request.headers.get("content-length")
    if declared_length:
        if not declared_length.isdecimal():
            raise HTTPException(status_code=400, detail="Invalid content length")
        if int(declared_length) > 64_000:
            raise HTTPException(status_code=413, detail="Payload too large")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 64_000:
            raise HTTPException(status_code=413, detail="Payload too large")
    values = decrypt_credentials(
        trigger.workspace_id,
        integration.id,
        "github",
        integration.encrypted_credentials,
        integration.key_version,
    )
    signature = request.headers.get("x-hub-signature-256", "")
    expected = (
        "sha256=" + hmac.new(values["webhook_secret"].encode(), raw, hashlib.sha256).hexdigest()
    )
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    delivery_id = request.headers.get("x-github-delivery", "")
    if not re.fullmatch(r"[A-Za-z0-9-]{8,80}", delivery_id):
        raise HTTPException(status_code=422, detail="Invalid delivery ID")
    if request.headers.get("x-github-event") != "issues":
        raise HTTPException(status_code=422, detail="Unsupported GitHub event")
    try:
        event = GithubIssueEvent.model_validate_json(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid issue event") from None
    if event.repository.full_name != values["repository"]:
        raise HTTPException(status_code=403, detail="Repository mismatch")
    current_trigger = await db.scalar(
        select(Trigger)
        .where(Trigger.id == trigger.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    current_integration = await db.scalar(
        select(Integration)
        .where(Integration.id == integration.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        current_trigger is None
        or not current_trigger.enabled
        or current_integration is None
        or current_integration.revoked_at is not None
        or current_integration.credential_version != verified_credential_version
        or current_integration.key_version != verified_key_version
    ):
        raise HTTPException(status_code=409, detail="Trigger or credential changed during delivery")
    trigger_id = trigger.id
    workflow = await db.get(Workflow, trigger.workflow_id)
    if workflow is None or workflow.status != "active":
        raise HTTPException(status_code=404, detail="Trigger not found")
    payload = {
        "provider": "github",
        "action": event.action,
        "repository": event.repository.full_name,
        "issue_number": event.issue.number,
        "state": event.issue.state,
    }
    digest = hashlib.sha256(raw).hexdigest()
    delivery = WebhookDelivery(
        trigger_id=trigger_id,
        provider="github",
        delivery_id=delivery_id,
        payload_hash=digest,
    )
    try:
        db.add(delivery)
        await db.flush()
        run_id = uuid.uuid4()
        input_json = {"payload": payload}
        run = WorkflowRun(
            id=run_id,
            workspace_id=trigger.workspace_id,
            workflow_id=trigger.workflow_id,
            version_id=trigger.version_id,
            temporal_workflow_id=f"run:{run_id}",
            idempotency_key=f"webhook:{trigger_id}:{delivery_id}",
            request_hash=hashlib.sha256(
                json.dumps(input_json, sort_keys=True).encode()
            ).hexdigest(),
            input_json=input_json,
            status="queued",
        )
        db.add(run)
        await db.flush()
        if event.issue.title or event.issue.body:
            version = active_key_version()
            db.add(
                IssueContent(
                    run_id=run_id,
                    workspace_id=trigger.workspace_id,
                    key_version=version,
                    encrypted_content=encrypt_credentials(
                        trigger.workspace_id,
                        run_id,
                        "github_issue",
                        {
                            "title": (event.issue.title or "")[:200],
                            "body": (event.issue.body or "")[:4000],
                        },
                        version,
                    ),
                )
            )
        delivery.result_run_id = run_id
        db.add(RunStartOutbox(run_id=run_id))
        db.add(
            RunEvent(
                workspace_id=trigger.workspace_id,
                run_id=run_id,
                event_key="queued",
                type="run.queued",
                payload_json={"status": "queued"},
            )
        )
        db.add(
            audit(trigger.workspace_id, None, "webhook.accept", "webhook_deliveries", delivery.id)
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        prior = await db.scalar(
            select(WebhookDelivery).where(
                WebhookDelivery.trigger_id == trigger_id,
                WebhookDelivery.delivery_id == delivery_id,
            )
        )
        if prior is None or prior.payload_hash != digest:
            raise HTTPException(status_code=409, detail="Delivery ID conflict") from None
        return DeliveryOut(run_id=prior.result_run_id, status="queued", duplicate=True)
    return DeliveryOut(run_id=run_id, status="queued", duplicate=False)
