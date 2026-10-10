"""Manual run creation, durable projection reads, and resumable SSE."""

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.responses import StreamingResponse

from app.auth import Db, UserDep, membership, require_csrf
from app.db import session_factory
from app.models import RunEvent, RunStartOutbox, StepRun, Workflow, WorkflowRun, WorkflowVersion

router = APIRouter(prefix="/api/v1", tags=["runs"])
logger = logging.getLogger(__name__)


class ManualRunInput(BaseModel):
    payload: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=50)

    @model_validator(mode="after")
    def bound_input(self) -> "ManualRunInput":
        if len(self.model_dump_json()) > 16_000:
            raise ValueError("Input exceeds 16 KB")
        if any(
            word in key.lower()
            for key in self.payload
            for word in ("secret", "token", "password", "credential", "authorization")
        ):
            raise ValueError("Manual input cannot contain secret-like fields")
        return self


class RunAccepted(BaseModel):
    run_id: uuid.UUID
    status: str
    version_id: uuid.UUID


class StepOut(BaseModel):
    node_id: str
    attempt: int
    status: str
    output: dict[str, Any] | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class RunOut(BaseModel):
    run_id: uuid.UUID
    workflow_id: uuid.UUID
    version_id: uuid.UUID
    status: str
    input: dict[str, Any]
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    steps: list[StepOut]


class RunSummary(BaseModel):
    run_id: uuid.UUID
    version_id: uuid.UUID
    status: str
    created_at: datetime


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[redacted]"
                if any(
                    word in key.lower()
                    for word in ("secret", "token", "password", "credential", "authorization")
                )
                else redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


async def scoped_run(db: Db, ws: uuid.UUID, run_id: uuid.UUID) -> WorkflowRun:
    run = await db.scalar(
        select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.workspace_id == ws)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.post(
    "/workspaces/{ws}/workflows/{workflow_id}/runs", response_model=RunAccepted, status_code=202
)
async def start_run(
    ws: uuid.UUID,
    workflow_id: uuid.UUID,
    body: ManualRunInput,
    request: Request,
    db: Db,
    user: UserDep,
    idempotency_key: str = Header(min_length=8, max_length=128),
) -> RunAccepted:
    require_csrf(request)
    await membership(db, ws, user, "run")
    if not all(char.isalnum() or char in "-_" for char in idempotency_key):
        raise HTTPException(status_code=422, detail="Invalid Idempotency-Key")
    workflow = await db.scalar(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.workspace_id == ws)
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if workflow.status != "active" or workflow.published_version_id is None:
        raise HTTPException(status_code=409, detail="Publish an active workflow before running")
    version = await db.get(WorkflowVersion, workflow.published_version_id)
    if version is None or not any(
        node["type"] == "trigger.manual" for node in version.graph_json["nodes"]
    ):
        raise HTTPException(
            status_code=409, detail="Published workflow does not have a manual trigger"
        )
    payload = body.model_dump(mode="json")
    request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    existing = await db.scalar(
        select(WorkflowRun).where(
            WorkflowRun.workspace_id == ws,
            WorkflowRun.workflow_id == workflow_id,
            WorkflowRun.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(
                status_code=409, detail="Idempotency-Key reused with different input"
            )
        return RunAccepted(
            run_id=existing.id, status=existing.status, version_id=existing.version_id
        )
    run_id = uuid.uuid4()
    run = WorkflowRun(
        id=run_id,
        workspace_id=ws,
        workflow_id=workflow_id,
        version_id=workflow.published_version_id,
        temporal_workflow_id=f"run:{run_id}",
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        input_json=payload,
        status="queued",
    )
    db.add(run)
    try:
        await db.flush()
        db.add(RunStartOutbox(run_id=run_id))
        db.add(
            RunEvent(
                workspace_id=ws,
                run_id=run_id,
                event_key="queued",
                type="run.queued",
                payload_json={"status": "queued"},
            )
        )
        await db.commit()
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
        if constraint != "uq_run_idempotency":
            logger.error("run creation integrity failure: %s", constraint or "unknown")
            await db.rollback()
            raise HTTPException(status_code=500, detail="Run creation failed") from None
        await db.rollback()
        raced = await db.scalar(
            select(WorkflowRun).where(
                WorkflowRun.workspace_id == ws,
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRun.idempotency_key == idempotency_key,
            )
        )
        if raced is None or raced.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="Idempotency-Key conflict") from exc
        return RunAccepted(run_id=raced.id, status=raced.status, version_id=raced.version_id)
    return RunAccepted(run_id=run_id, status="queued", version_id=run.version_id)


@router.get("/workspaces/{ws}/runs/{run_id}", response_model=RunOut)
async def inspect_run(ws: uuid.UUID, run_id: uuid.UUID, db: Db, user: UserDep) -> RunOut:
    access = await membership(db, ws, user, "read")
    viewer = access.role == "viewer"
    run = await scoped_run(db, ws, run_id)
    rows = await db.scalars(
        select(StepRun)
        .where(StepRun.run_id == run_id)
        .order_by(StepRun.started_at, StepRun.node_id, StepRun.attempt)
    )
    return RunOut(
        run_id=run.id,
        workflow_id=run.workflow_id,
        version_id=run.version_id,
        status=run.status,
        input={"redacted": True} if viewer else redact(run.input_json),
        error=run.error,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        steps=[
            StepOut(
                node_id=step.node_id,
                attempt=step.attempt,
                status=step.status,
                output=None if viewer else redact(step.output_json),
                error=step.error,
                started_at=step.started_at,
                finished_at=step.finished_at,
            )
            for step in rows
        ],
    )


@router.get("/workspaces/{ws}/workflows/{workflow_id}/runs", response_model=list[RunSummary])
async def list_runs(
    ws: uuid.UUID, workflow_id: uuid.UUID, db: Db, user: UserDep
) -> list[RunSummary]:
    await membership(db, ws, user, "read")
    workflow = await db.scalar(
        select(Workflow.id).where(Workflow.id == workflow_id, Workflow.workspace_id == ws)
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    rows = await db.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.workspace_id == ws, WorkflowRun.workflow_id == workflow_id)
        .order_by(WorkflowRun.created_at.desc())
        .limit(50)
    )
    return [
        RunSummary(
            run_id=row.id, version_id=row.version_id, status=row.status, created_at=row.created_at
        )
        for row in rows
    ]


@router.get("/workspaces/{ws}/runs/{run_id}/events")
async def stream_events(
    ws: uuid.UUID,
    run_id: uuid.UUID,
    request: Request,
    db: Db,
    user: UserDep,
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    await membership(db, ws, user, "read")
    await scoped_run(db, ws, run_id)
    try:
        cursor = int(last_event_id) if last_event_id is not None else 0
        if cursor < 0:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid event cursor") from exc

    async def events() -> AsyncIterator[str]:
        nonlocal cursor
        idle = 0
        while not await request.is_disconnected():
            async with session_factory()() as session:
                allowed = await session.scalar(
                    select(WorkflowRun.id).where(
                        WorkflowRun.id == run_id, WorkflowRun.workspace_id == ws
                    )
                )
                if allowed is None:
                    return
                # Recheck membership so revocation also closes long-lived streams.
                try:
                    access = await membership(session, ws, user, "read")
                except HTTPException:
                    return
                rows = await session.scalars(
                    select(RunEvent)
                    .where(
                        RunEvent.run_id == run_id, RunEvent.workspace_id == ws, RunEvent.id > cursor
                    )
                    .order_by(RunEvent.id)
                    .limit(100)
                )
                batch = list(rows)
                terminal = await session.scalar(
                    select(WorkflowRun.status).where(
                        WorkflowRun.id == run_id, WorkflowRun.workspace_id == ws
                    )
                )
            for event in batch:
                cursor = event.id
                payload = redact(event.payload_json)
                if access.role == "viewer":
                    payload.pop("output", None)
                data = json.dumps(payload, separators=(",", ":"))
                yield f"id: {event.id}\nevent: {event.type}\ndata: {data}\n\n"
            if terminal in {"succeeded", "failed"} and not batch:
                return
            idle = 0 if batch else idle + 1
            if idle >= 15:
                yield ": heartbeat\n\n"
                idle = 0
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
