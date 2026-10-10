"""Database projections and deterministic mock action side effects."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db import session_factory
from app.graph import Graph, HttpConfig
from app.models import (
    ActionEffect,
    Approval,
    IssueContent,
    RunEvent,
    StepRun,
    WorkflowRun,
    WorkflowVersion,
)
from pydantic import ValidationError
from sqlalchemy import func, select
from temporalio import activity
from temporalio.exceptions import ApplicationError

logger = logging.getLogger(__name__)


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
    from opentelemetry import trace

    from app.observability import STEP_DURATION, WORKFLOW_DURATION, WORKFLOW_RUNS, event

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
        finished_at = datetime.now(UTC)
        terminal_run = kind in {"run.succeeded", "run.failed"} and run.status not in {
            "succeeded",
            "failed",
        }
        run_duration = (
            max(0.0, (finished_at - run.started_at).total_seconds())
            if terminal_run and run.started_at is not None
            else None
        )
        step_duration: float | None = None
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
                if step.started_at is not None:
                    step_duration = max(0.0, (finished_at - step.started_at).total_seconds())
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
        with trace.get_tracer(__name__).start_as_current_span("projection.write") as span:
            span.set_attribute("run.id", str(run_id))
            span.set_attribute("event.type", kind)
            await db.commit()
        if terminal_run:
            WORKFLOW_RUNS.labels(run.status).inc()
            if run_duration is not None:
                WORKFLOW_DURATION.observe(run_duration)
        if step_duration is not None:
            STEP_DURATION.labels(kind.split(".", 1)[1]).observe(step_duration)
        event(logger, "run.projection", run_id=str(run_id), node_id=node_id, status=kind)


@activity.defn(name="execute_mock_action")
async def execute_mock_action(command: dict[str, Any]) -> dict[str, Any]:
    from app.observability import ACTIVITY_RETRIES

    run_id = uuid.UUID(command["run_id"])
    node_id = str(command["node_id"])
    if activity.info().attempt > 1:
        ACTIVITY_RETRIES.labels("mock").inc()
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


@activity.defn(name="execute_https_action")
async def execute_https_action(command: dict[str, Any]) -> dict[str, Any]:
    from app.observability import ACTIVITY_RETRIES

    # Keep network-client imports out of Temporal's deterministic workflow sandbox.
    from orchestrator.http_connector import HttpActionError, execute_https_get

    try:
        config = HttpConfig.model_validate(command["config"])
    except ValidationError:
        raise ApplicationError("Action configuration is invalid", non_retryable=True) from None
    attempt = activity.info().attempt
    if attempt > 1:
        ACTIVITY_RETRIES.labels("https").inc()
    try:
        output = await execute_https_get(config)
    except HttpActionError as exc:
        run_id = str(command["run_id"])
        node_id = str(command["node_id"])
        await project_event(
            {
                "run_id": run_id,
                "type": "step.failed",
                "event_key": f"{node_id}:failed:{attempt}",
                "node_id": node_id,
                "attempt": attempt,
                "error": exc.code,
            }
        )
        if exc.retryable and attempt < 3:
            await project_event(
                {
                    "run_id": run_id,
                    "type": "step.running",
                    "event_key": f"{node_id}:running:{attempt + 1}",
                    "node_id": node_id,
                    "attempt": attempt + 1,
                }
            )
        raise ApplicationError(
            "HTTPS action failed", type=exc.code, non_retryable=not exc.retryable
        ) from None
    return {"output": output, "attempts": attempt}


@activity.defn(name="execute_provider_action")
async def execute_provider_activity(command: dict[str, Any]) -> dict[str, Any]:
    from app.observability import ACTIVITY_RETRIES

    from orchestrator.providers import ProviderError, execute_provider_action

    try:
        if activity.info().attempt > 1:
            ACTIVITY_RETRIES.labels("provider").inc()
        return await execute_provider_action(command)
    except ProviderError as exc:
        attempt = activity.info().attempt
        run_id = str(command["run_id"])
        node_id = str(command["node_id"])
        await project_event(
            {
                "run_id": run_id,
                "type": "step.failed",
                "event_key": f"{node_id}:failed:{attempt}",
                "node_id": node_id,
                "attempt": attempt,
                "error": exc.code,
            }
        )
        if exc.retryable and attempt < 3:
            await project_event(
                {
                    "run_id": run_id,
                    "type": "step.running",
                    "event_key": f"{node_id}:running:{attempt + 1}",
                    "node_id": node_id,
                    "attempt": attempt + 1,
                }
            )
        raise ApplicationError(
            "Provider action failed",
            type=exc.code,
            non_retryable=not exc.retryable,
            next_retry_delay=timedelta(seconds=exc.retry_after) if exc.retry_after else None,
        ) from None


@activity.defn(name="execute_agent")
async def execute_agent(command: dict[str, Any]) -> dict[str, Any]:
    from opentelemetry import trace

    from app.integration_crypto import decrypt_credentials
    from app.observability import AGENT_COST, AGENT_TOKENS

    from orchestrator.agent import AgentFailure, run_agent

    try:
        source = dict(command["payload"])
        run_id = uuid.UUID(command["run_id"])
        async with session_factory()() as db:
            run = await db.get(WorkflowRun, run_id)
            content = await db.get(IssueContent, run_id)
            if run is None:
                raise AgentFailure("run_missing")
            if content is not None:
                if content.workspace_id != run.workspace_id:
                    raise AgentFailure("input_unavailable")
                source.update(
                    decrypt_credentials(
                        run.workspace_id,
                        run_id,
                        "github_issue",
                        content.encrypted_content,
                        content.key_version,
                    )
                )
        with trace.get_tracer(__name__).start_as_current_span("agent.invoke") as span:
            span.set_attribute("run.id", command["run_id"])
            span.set_attribute("model.profile", command["config"]["model_profile"])
            result = await run_agent(command["run_id"], command["config"], source)
        profile = command["config"]["model_profile"]
        usage = result["usage"]
        AGENT_TOKENS.labels(profile, "input").inc(usage["input_tokens"])
        AGENT_TOKENS.labels(profile, "output").inc(usage["output_tokens"])
        AGENT_COST.inc(usage["cost_microusd"])
        return result
    except AgentFailure as exc:
        await project_event(
            {
                "run_id": command["run_id"],
                "type": "step.failed",
                "event_key": f"{command['node_id']}:failed:1",
                "node_id": command["node_id"],
                "error": exc.code,
            }
        )
        raise ApplicationError("Agent failed", type=exc.code, non_retryable=True) from None


@activity.defn(name="open_approval")
async def open_approval(command: dict[str, Any]) -> str:
    run_id = uuid.UUID(command["run_id"])
    node_id = command["node_id"]
    async with session_factory()() as db:
        run = await db.get(WorkflowRun, run_id, with_for_update=True)
        if run is None:
            raise ApplicationError("Run missing", non_retryable=True)
        row = await db.scalar(
            select(Approval).where(Approval.run_id == run_id, Approval.node_id == node_id)
        )
        if row is None:
            row = Approval(
                workspace_id=run.workspace_id,
                run_id=run_id,
                node_id=node_id,
                title=command["title"],
                status="pending",
                expires_at=datetime.now(UTC) + timedelta(seconds=command["timeout_seconds"]),
            )
            db.add(row)
            await db.flush()
        await db.commit()
        return str(row.id)


@activity.defn(name="approval_status")
async def approval_status(command: dict[str, Any]) -> str:
    from app.observability import APPROVAL_WAIT

    async with session_factory()() as db:
        row = await db.get(Approval, uuid.UUID(command["approval_id"]), with_for_update=True)
        if row is None:
            raise ApplicationError("Approval missing", non_retryable=True)
        if row.status == "pending" and (
            command.get("expire") or datetime.now(UTC) >= row.expires_at
        ):
            row.status = "expired"
            await db.commit()
            APPROVAL_WAIT.observe(max(0.0, (datetime.now(UTC) - row.created_at).total_seconds()))
        return row.status
