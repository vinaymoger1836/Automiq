import asyncio
from datetime import timedelta

from app.config import get_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker


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
        client, task_queue=settings.temporal_task_queue,
        workflows=[BootstrapCheck], activities=[check_postgres],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
