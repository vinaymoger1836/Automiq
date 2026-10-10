"""Replay-safe scheduled-fire workflow; database work stays in an activity."""

from datetime import timedelta
from typing import cast

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class ScheduleFire:
    @workflow.run
    async def run(self, trigger_id: str) -> str | None:
        return cast(
            str | None,
            await workflow.execute_activity(
                "create_scheduled_run",
                {
                    "trigger_id": trigger_id,
                    "fire_id": workflow.info().workflow_id,
                    "started_at": workflow.info().start_time.isoformat(),
                },
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=20),
                    maximum_attempts=10,
                ),
            ),
        )
