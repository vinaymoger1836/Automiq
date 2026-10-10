import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db import session_factory
from app.models import Approval, RunStartOutbox, WorkflowRun
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from orchestrator.activities import (
    approval_status,
    execute_agent,
    execute_https_action,
    execute_mock_action,
    execute_provider_activity,
    load_run,
    open_approval,
    project_event,
)
from orchestrator.engine import WorkflowExecution
from orchestrator.schedule_engine import ScheduleFire
from orchestrator.schedules import create_scheduled_run, reconcile_schedules

logger = logging.getLogger(__name__)


@activity.defn
async def check_postgres() -> str:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return "postgres-ok"
    finally:
        await engine.dispose()


@workflow.defn
class BootstrapCheck:
    @workflow.run
    async def run(self) -> str:
        return await workflow.execute_activity(
            check_postgres, start_to_close_timeout=timedelta(seconds=10)
        )


async def main() -> None:
    from app.observability import close_tracing, configure_tracing
    from prometheus_client import start_http_server

    settings = get_settings()
    configure_tracing("automiq-worker", settings.otel_exporter_otlp_endpoint)
    start_http_server(9465, addr="0.0.0.0")
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[BootstrapCheck, WorkflowExecution, ScheduleFire],
        activities=[
            check_postgres,
            load_run,
            project_event,
            execute_mock_action,
            execute_https_action,
            execute_provider_activity,
            execute_agent,
            open_approval,
            approval_status,
            create_scheduled_run,
        ],
    )
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(worker.run())
            tasks.create_task(reconcile_outbox(client))
            tasks.create_task(reconcile_schedules(client))
            tasks.create_task(reconcile_approval_signals(client))
    finally:
        close_tracing()


async def reconcile_outbox(client: Client) -> None:
    from app.observability import event

    settings = get_settings()
    while True:
        async with session_factory()() as db:
            async with db.begin():
                row = (
                    await db.execute(
                        select(RunStartOutbox, WorkflowRun)
                        .join(WorkflowRun, RunStartOutbox.run_id == WorkflowRun.id)
                        .where(RunStartOutbox.started_at.is_(None))
                        .order_by(RunStartOutbox.created_at)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                ).first()
                if row is not None:
                    outbox, run = row
                    outbox.attempts += 1
                    try:
                        await asyncio.wait_for(
                            client.start_workflow(
                                WorkflowExecution.run,
                                str(run.id),
                                id=run.temporal_workflow_id,
                                task_queue=settings.temporal_task_queue,
                                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                            ),
                            timeout=10,
                        )
                    except WorkflowAlreadyStartedError:
                        outbox.started_at = datetime.now(UTC)
                        outbox.last_error = None
                        event(logger, "run.start_reconciled", run_id=str(run.id))
                    except Exception:
                        outbox.last_error = "temporal_start_unavailable"
                        event(logger, "run.start_deferred", run_id=str(run.id))
                    else:
                        outbox.started_at = datetime.now(UTC)
                        outbox.last_error = None
                        event(logger, "run.started", run_id=str(run.id))
        await asyncio.sleep(2)


async def reconcile_approval_signals(client: Client) -> None:
    while True:
        async with session_factory()() as db:
            async with db.begin():
                row = (
                    await db.execute(
                        select(Approval, WorkflowRun)
                        .join(WorkflowRun, Approval.run_id == WorkflowRun.id)
                        .where(
                            Approval.status.in_(["approved", "rejected"]),
                            Approval.signal_sent_at.is_(None),
                            WorkflowRun.status == "running",
                        )
                        .order_by(Approval.decided_at)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                ).first()
                if row is not None:
                    approval, run = row
                    try:
                        await asyncio.wait_for(
                            client.get_workflow_handle(run.temporal_workflow_id).signal(
                                "approval_decided", str(approval.id)
                            ),
                            timeout=10,
                        )
                    except Exception:
                        logger.warning("Approval signal retry pending: approval_id=%s", approval.id)
                    else:
                        approval.signal_sent_at = datetime.now(UTC)
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
