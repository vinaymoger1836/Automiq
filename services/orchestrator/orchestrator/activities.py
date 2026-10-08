"""Database projections and deterministic mock action side effects."""

import asyncio
import uuid
from typing import Any

from app.db import session_factory
from app.graph import Graph, HttpConfig
from app.models import ActionEffect, RunEvent, StepRun, WorkflowRun, WorkflowVersion
from pydantic import ValidationError
from sqlalchemy import func, select
from temporalio import activity
from temporalio.exceptions import ApplicationError


@activity.defn(name="load_run")
async def load_run(run_id: str) -> dict[str, Any]:
    async with session_factory()() as db:
        run = await db.get(WorkflowRun, uuid.UUID(run_id))
        if run is None:
            raise ApplicationError("Run missing", non_retryable=True)
        version = await db.get(WorkflowVersion, run.version_id)
        if version is None:
            raise ApplicationError("Published version missing", non_retryable=True)
        try:
            graph = Graph.model_validate(version.graph_json)
        except ValidationError:
            raise ApplicationError("Published graph is incompatible", non_retryable=True) from None
        return {
            "graph": graph.model_dump(mode="json"),
            "input": run.input_json,
            "version_id": str(version.id),
        }


@activity.defn(name="project_event")
async def project_event(command: dict[str, Any]) -> None:
    run_id = uuid.UUID(command["run_id"])
    kind = command["type"]
    async with session_factory()() as db:
        run = await db.get(WorkflowRun, run_id)
        if run is None:
            raise ApplicationError("Run missing", non_retryable=True)
        existing = await db.scalar(
            select(RunEvent.id).where(
                RunEvent.run_id == run_id, RunEvent.event_key == command["event_key"]
            )
        )
        if existing is not None:
            return
        node_id = command.get("node_id")
        attempt = command.get("attempt", 1)
        output = command.get("output")
        error = command.get("error")
        if kind == "run.started" and run.status == "queued":
            run.status = "running"
            run.started_at = func.now()
        elif kind in {"run.succeeded", "run.failed"} and run.status not in {"succeeded", "failed"}:
            run.status = "succeeded" if kind == "run.succeeded" else "failed"
            run.error = error
            run.completed_at = func.now()
        elif kind.startswith("step.") and node_id:
            step = await db.scalar(
                select(StepRun).where(
                    StepRun.run_id == run_id, StepRun.node_id == node_id, StepRun.attempt == attempt
                )
            )
            status = kind.split(".", 1)[1]
            if step is None:
                step = StepRun(run_id=run_id, node_id=node_id, attempt=attempt, status=status)
                db.add(step)
            else:
                step.status = status
            if status in {"succeeded", "failed", "skipped"}:
                step.finished_at = func.now()
            if output is not None:
                step.output_json = output
            if error is not None:
                step.error = error
        payload: dict[str, Any] = {"status": kind.split(".", 1)[1]}
        if node_id:
            payload["node_id"] = node_id
            payload["attempt"] = attempt
        if output is not None:
            payload["output"] = output
        if error is not None:
            payload["error"] = error
        db.add(
            RunEvent(
                workspace_id=run.workspace_id,
                run_id=run_id,
                event_key=command["event_key"],
                type=kind,
                payload_json=payload,
            )
        )
        await db.commit()


@activity.defn(name="execute_mock_action")
async def execute_mock_action(command: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(command["run_id"])
    node_id = str(command["node_id"])
    try:
        config = HttpConfig.model_validate(command["config"])
    except ValidationError:
        raise ApplicationError("Action configuration is invalid", non_retryable=True) from None
    effect_key = f"{run_id}:{node_id}"
    if config.delay_seconds:
        await asyncio.sleep(config.delay_seconds)
    async with session_factory()() as db:
        effect = await db.get(ActionEffect, effect_key, with_for_update=True)
        if effect is not None and effect.output_json is not None:
            return {"output": effect.output_json, "attempts": effect.invocations}
        if effect is None:
            effect = ActionEffect(
                effect_key=effect_key, run_id=run_id, node_id=node_id, invocations=1
            )
            db.add(effect)
        else:
            effect.invocations += 1
        attempts = effect.invocations
        if not config.permanent_failure and attempts > config.failures_before_success:
            effect.output_json = config.mock_output
        await db.commit()
    if config.permanent_failure or attempts <= config.failures_before_success:
        await project_event(
            {
                "run_id": str(run_id),
                "type": "step.failed",
                "event_key": f"{node_id}:failed:{attempts}",
                "node_id": node_id,
                "attempt": attempts,
                "error": "mock_permanent" if config.permanent_failure else "mock_transient",
            }
        )
        if not config.permanent_failure:
            await project_event(
                {
                    "run_id": str(run_id),
                    "type": "step.running",
                    "event_key": f"{node_id}:running:{attempts + 1}",
                    "node_id": node_id,
                    "attempt": attempts + 1,
                }
            )
        raise ApplicationError(
            "Mock action failed",
            type="mock_permanent" if config.permanent_failure else "mock_transient",
            non_retryable=config.permanent_failure,
        )
    return {"output": config.mock_output, "attempts": attempts}
