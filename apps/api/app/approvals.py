"""Workspace-scoped human approval inbox and decisions."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from temporalio.client import Client

from app.auth import Db, UserDep, membership, require_csrf
from app.config import get_settings
from app.models import Approval, AuditLog, RunEvent, WorkflowRun

router = APIRouter(prefix="/api/v1", tags=["approvals"])
logger = logging.getLogger(__name__)


class ApprovalOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    node_id: str
    title: str
    status: str
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    can_approve: bool


class DecisionIn(BaseModel):
    decision: Literal["approved", "rejected"]


def present(row: Approval, can_approve: bool) -> ApprovalOut:
    return ApprovalOut(
        id=row.id,
        run_id=row.run_id,
        node_id=row.node_id,
        title=row.title,
        status=row.status,
        created_at=row.created_at,
        expires_at=row.expires_at,
        decided_at=row.decided_at,
        can_approve=can_approve,
    )


@router.get("/workspaces/{ws}/approvals", response_model=list[ApprovalOut])
async def list_approvals(ws: uuid.UUID, db: Db, user: UserDep) -> list[ApprovalOut]:
    access = await membership(db, ws, user, "read")
    rows = await db.scalars(
        select(Approval)
        .where(Approval.workspace_id == ws)
        .order_by(Approval.created_at.desc())
        .limit(100)
    )
    return [present(row, access.role == "owner" and row.status == "pending") for row in rows]


@router.post("/workspaces/{ws}/approvals/{approval_id}/decision", response_model=ApprovalOut)
async def decide_approval(
    ws: uuid.UUID, approval_id: uuid.UUID, body: DecisionIn, request: Request, db: Db, user: UserDep
) -> ApprovalOut:
    require_csrf(request)
    await membership(db, ws, user, "approve")
    row = await db.scalar(
        select(Approval)
        .where(Approval.id == approval_id, Approval.workspace_id == ws)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    now = datetime.now(UTC)
    if row.status == "pending" and now >= row.expires_at:
        row.status = "expired"
        await db.commit()
        raise HTTPException(status_code=409, detail="Approval expired")
    if row.status != "pending":
        if row.status != body.decision:
            raise HTTPException(status_code=409, detail="Approval already decided")
        return present(row, False)
    row.status = body.decision
    row.decided_at = now
    row.decided_by = user.id
    db.add(
        AuditLog(
            workspace_id=ws,
            actor_type="user",
            actor_id=user.id,
            action="approval.decided",
            resource_type="approvals",
            resource_id=row.id,
            metadata_json={"decision": body.decision, "run_id": str(row.run_id)},
        )
    )
    db.add(
        RunEvent(
            workspace_id=ws,
            run_id=row.run_id,
            event_key=f"{row.node_id}:decision",
            type="approval.decided",
            payload_json={"node_id": row.node_id, "decision": body.decision},
        )
    )
    await db.commit()
    await db.refresh(row)
    # The worker retries unsent signals if the API or Temporal connection fails here.
    try:
        settings = get_settings()
        run = await db.get(WorkflowRun, row.run_id)
        if run is not None:
            client = await Client.connect(
                settings.temporal_address, namespace=settings.temporal_namespace
            )
            await client.get_workflow_handle(run.temporal_workflow_id).signal(
                "approval_decided", str(row.id)
            )
            row.signal_sent_at = datetime.now(UTC)
            await db.commit()
    except Exception:
        # The committed decision remains authoritative; the worker outbox will deliver it.
        logger.warning("Approval signal deferred: approval_id=%s run_id=%s", row.id, row.run_id)
    return present(row, False)
