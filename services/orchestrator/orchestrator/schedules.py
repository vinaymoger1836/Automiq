"""Temporal Schedules create deduplicated business runs through the existing outbox."""

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import timedelta

from app.config import get_settings
from app.db import session_factory
from app.models import AuditLog, RunEvent, RunStartOutbox, Trigger, Workflow, WorkflowRun
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from temporalio import activity
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
)

from orchestrator.schedule_engine import ScheduleFire

logger = logging.getLogger(__name__)


@activity.defn(name="create_scheduled_run")
async def create_scheduled_run(command: dict[str, str]) -> str | None:
    from app.limits import workspace_has_capacity
    from app.observability import current_traceparent
    from opentelemetry import trace

    trigger_id = uuid.UUID(command["trigger_id"])
    fire_id = command["fire_id"]
    if not fire_id.startswith(f"schedule-fire:{trigger_id}-") or len(fire_id) > 119:
        return None
    async with session_factory()() as db:
        trigger = await db.get(Trigger, trigger_id)
        if trigger is None or not trigger.enabled or trigger.type != "schedule":
            return None
        workflow_row = await db.get(Workflow, trigger.workflow_id)
        if workflow_row is None or workflow_row.status != "active":
            return None
        workspace_id = trigger.workspace_id
        workflow_id = trigger.workflow_id
        key = f"schedule:{fire_id}"
        existing = await db.scalar(
            select(WorkflowRun).where(
                WorkflowRun.workspace_id == workspace_id,
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRun.idempotency_key == key,
            )
        )
        if existing is not None:
            return str(existing.id)
        if not await workspace_has_capacity(db, workspace_id):
            logger.warning("schedule skipped at active run limit: trigger_id=%s", trigger_id)
            return None
        run_id = uuid.uuid4()
        input_json = {"payload": {"scheduled_at": command["started_at"]}}
        with trace.get_tracer(__name__).start_as_current_span("schedule.fire") as span:
            span.set_attribute("run.id", str(run_id))
            traceparent = current_traceparent()
        db.add(
            WorkflowRun(
                id=run_id,
                workspace_id=workspace_id,
                workflow_id=workflow_id,
                version_id=trigger.version_id,
                temporal_workflow_id=f"run:{run_id}",
                idempotency_key=key,
                request_hash=hashlib.sha256(
                    json.dumps(input_json, sort_keys=True).encode()
                ).hexdigest(),
                traceparent=traceparent,
                input_json=input_json,
                status="queued",
            )
        )
        try:
            await db.flush()
            db.add(RunStartOutbox(run_id=run_id))
            db.add(
                RunEvent(
                    workspace_id=workspace_id,
                    run_id=run_id,
                    event_key="queued",
                    type="run.queued",
                    payload_json={"status": "queued"},
                )
            )
            db.add(
                AuditLog(
                    workspace_id=workspace_id,
                    actor_type="service",
                    actor_id=None,
                    action="schedule.fire",
                    resource_type="triggers",
                    resource_id=trigger_id,
                    metadata_json={"run_id": str(run_id)},
                )
            )
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raced = await db.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.workspace_id == workspace_id,
                    WorkflowRun.workflow_id == workflow_id,
                    WorkflowRun.idempotency_key == key,
                )
            )
            if raced is None:
                raise
            return str(raced.id)
        return str(run_id)


async def reconcile_schedules(client: Client) -> None:
    settings = get_settings()
    while True:
        async with session_factory()() as db:
            rows = list(
                await db.scalars(select(Trigger).where(Trigger.type == "schedule").limit(1000))
            )
        for trigger in rows:
            schedule_id = f"schedule:{trigger.id}"
            handle = client.get_schedule_handle(schedule_id)
            try:
                await client.create_schedule(
                    schedule_id,
                    Schedule(
                        action=ScheduleActionStartWorkflow(
                            ScheduleFire.run,
                            str(trigger.id),
                            id=f"schedule-fire:{trigger.id}",
                            task_queue=settings.temporal_task_queue,
                        ),
                        spec=ScheduleSpec(
                            cron_expressions=[trigger.config_json["cron"]],
                            time_zone_name=trigger.config_json["timezone"],
                        ),
                        policy=SchedulePolicy(
                            overlap=ScheduleOverlapPolicy.ALLOW_ALL,
                            catchup_window=timedelta(minutes=10),
                        ),
                        state=ScheduleState(paused=not trigger.enabled),
                    ),
                )
            except ScheduleAlreadyRunningError:
                try:
                    description = await handle.describe()
                    if description.schedule.state.paused != (not trigger.enabled):
                        if trigger.enabled:
                            await handle.unpause(note="Enabled in Automiq")
                        else:
                            await handle.pause(note="Disabled in Automiq")
                except Exception:
                    logger.warning("schedule synchronization failed for trigger %s", trigger.id)
            except Exception:
                logger.warning("schedule registration failed for trigger %s", trigger.id)
        await asyncio.sleep(30)
