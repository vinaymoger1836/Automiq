import asyncio
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db import session_factory
from app.models import RunStartOutbox, WorkflowRun
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from orchestrator.activities import execute_mock_action, load_run, project_event
from orchestrator.engine import WorkflowExecution


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
    settings = get_settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[BootstrapCheck, WorkflowExecution],
        activities=[check_postgres, load_run, project_event, execute_mock_action],
    )
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(worker.run())
        tasks.create_task(reconcile_outbox(client))


async def reconcile_outbox(client: Client) -> None:
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
                    except Exception:
                        outbox.last_error = "temporal_start_unavailable"
                    else:
                        outbox.started_at = datetime.now(UTC)
                        outbox.last_error = None
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
