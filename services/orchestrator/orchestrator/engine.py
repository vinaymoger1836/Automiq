"""Deterministic Phase 2 DAG traversal. All side effects live in activities."""

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

PROJECTION_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=30)
)
ACTION_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3)


def read_path(path: str, trigger: dict[str, Any], steps: dict[str, dict[str, Any]]) -> Any:
    parts = path.split(".")
    if parts[:2] == ["trigger", "payload"]:
        value: Any = trigger
        parts = parts[2:]
    elif len(parts) >= 3 and parts[0] == "steps" and parts[2] == "output":
        value = steps.get(parts[1])
        parts = parts[3:]
    else:
        return None
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


async def project(
    run_id: str,
    kind: str,
    event_key: str,
    *,
    node_id: str | None = None,
    attempt: int = 1,
    output: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    command = {
        "run_id": run_id,
        "type": kind,
        "event_key": event_key,
        "node_id": node_id,
        "attempt": attempt,
        "output": output,
        "error": error,
    }
    await workflow.execute_activity(
        "project_event",
        command,
        start_to_close_timeout=timedelta(seconds=10),
        retry_policy=PROJECTION_RETRY,
    )


@workflow.defn
class WorkflowExecution:
    @workflow.run
    async def run(self, run_id: str) -> str:
        try:
            snapshot: dict[str, Any] = await workflow.execute_activity(
                "load_run",
                run_id,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=PROJECTION_RETRY,
            )
        except ActivityError:
            await project(run_id, "run.failed", "run:failed", error="validation_error")
            return "failed"
        graph = snapshot["graph"]
        nodes = {node["id"]: node for node in graph["nodes"]}
        outgoing: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
        for edge in graph["edges"]:
            outgoing[edge["source"]].append(edge)
        current = next(node["id"] for node in graph["nodes"] if node["type"] == "trigger.manual")
        trigger = snapshot["input"]["payload"]
        outputs: dict[str, dict[str, Any]] = {}
        await project(run_id, "run.started", "run:started")

        while True:
            node = nodes[current]
            kind = node["type"]
            await project(run_id, "step.running", f"{current}:running:1", node_id=current)
            result: dict[str, Any]
            attempts = 1
            if kind == "trigger.manual":
                result = trigger
            elif kind == "action.http":
                try:
                    action: dict[str, Any] = await workflow.execute_activity(
                        "execute_mock_action",
                        {"run_id": run_id, "node_id": current, "config": node["config"]},
                        start_to_close_timeout=timedelta(
                            seconds=node["config"].get("timeout_seconds", 10)
                        ),
                        retry_policy=ACTION_RETRY,
                    )
                except ActivityError:
                    await project(
                        run_id,
                        "step.failed",
                        f"{current}:terminal-failed",
                        node_id=current,
                        error="external_error",
                    )
                    await project(run_id, "run.failed", "run:failed", error="external_error")
                    return "failed"
                result = action["output"]
                attempts = action["attempts"]
            elif kind == "condition":
                expression = node["config"]["expression"]
                value = read_path(expression["path"], trigger, outputs)
                operator = expression["operator"]
                matched = (
                    value is not None
                    if operator == "exists"
                    else value == expression.get("value")
                    if operator == "eq"
                    else value != expression.get("value")
                )
                result = {"result": matched}
            else:
                result = {}
            outputs[current] = result
            await project(
                run_id,
                "step.succeeded",
                f"{current}:succeeded",
                node_id=current,
                attempt=attempts,
                output=result,
            )
            if kind == "end":
                await project(run_id, "run.succeeded", "run:succeeded")
                return "succeeded"
            edges = outgoing[current]
            if kind == "condition":
                chosen = "true" if result["result"] else "false"
                skipped = next(edge["target"] for edge in edges if edge["source_handle"] != chosen)
                stack = [skipped]
                while stack:
                    skipped_id = stack.pop()
                    await project(
                        run_id,
                        "step.skipped",
                        f"{skipped_id}:skipped",
                        node_id=skipped_id,
                        attempt=0,
                    )
                    stack.extend(edge["target"] for edge in outgoing[skipped_id])
                current = next(edge["target"] for edge in edges if edge["source_handle"] == chosen)
            else:
                current = edges[0]["target"]
