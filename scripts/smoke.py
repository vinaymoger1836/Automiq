import asyncio
import uuid
from datetime import timedelta

from app.config import get_settings
from orchestrator.worker import BootstrapCheck
from temporalio.client import Client


async def main() -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    result = await client.execute_workflow(
        BootstrapCheck.run,
        id=f"bootstrap:{uuid.uuid4()}",
        task_queue=settings.temporal_task_queue,
        execution_timeout=timedelta(seconds=30),
    )
    if result != "postgres-ok":
        raise RuntimeError("bootstrap workflow returned an unexpected result")
    print("Temporal worker registered; PostgreSQL activity succeeded")


if __name__ == "__main__":
    asyncio.run(main())
