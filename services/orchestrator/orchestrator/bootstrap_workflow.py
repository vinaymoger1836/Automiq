"""Small deterministic workflow used to verify worker activity execution."""

from datetime import timedelta
from typing import cast

from temporalio import workflow


@workflow.defn
class BootstrapCheck:
    @workflow.run
    async def run(self) -> str:
        return cast(
            str,
            await workflow.execute_activity(
                "check_postgres", start_to_close_timeout=timedelta(seconds=10)
            ),
        )
