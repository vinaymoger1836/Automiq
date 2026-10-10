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
    def __init__(self) -> None:
        self._approval_signal: str | None = None
        self._expected_approval_id: str | None = None

    def approval_ready(self) -> bool:
        return self._approval_signal == self._expected_approval_id

    @workflow.signal
    def approval_decided(self, approval_id: str) -> None:
        self._approval_signal = approval_id

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
        current = next(node["id"] for node in graph["nodes"] if node["type"].startswith("trigger."))
        trigger = snapshot["input"]["payload"]
        outputs: dict[str, dict[str, Any]] = {}
        await project(run_id, "run.started", "run:started")

        while True:
            node = nodes[current]
            kind = node["type"]
            await project(run_id, "step.running", f"{current}:running:1", node_id=current)
            result: dict[str, Any]
            attempts = 1
            if kind.startswith("trigger."):
                result = trigger
            elif kind == "action.http":
                try:
                    activity_name = (
                        "execute_https_action"
                        if node["config"].get("operation") == "https_get"
                        else "execute_mock_action"
                    )
                    action: dict[str, Any] = await workflow.execute_activity(
                        activity_name,
                        {"run_id": run_id, "node_id": current, "config": node["config"]},
                        start_to_close_timeout=timedelta(
                            seconds=node["config"].get("timeout_seconds", 10)
                        ),
                        retry_policy=ACTION_RETRY,
                    )
                except ActivityError:
                    if node["config"].get("operation") != "https_get":
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
            elif kind in {"action.github_comment", "action.slack_message"}:
                try:
                    action = await workflow.execute_activity(
                        "execute_provider_action",
                        {
                            "run_id": run_id,
                            "node_id": current,
                            "kind": kind,
                            "config": node["config"],
                        },
                        start_to_close_timeout=timedelta(seconds=20),
                        retry_policy=ACTION_RETRY,
                    )
                except ActivityError:
                    await project(run_id, "run.failed", "run:failed", error="external_error")
                    return "failed"
                result = action["output"]
                attempts = action["attempts"]
            elif kind == "agent":
                try:
                    result = await workflow.execute_activity(
                        "execute_agent",
                        {
                            "run_id": run_id,
                            "node_id": current,
                            "config": node["config"],
                            "payload": trigger,
                        },
                        start_to_close_timeout=timedelta(
                            seconds=node["config"].get("max_duration_seconds", 30)
                        ),
                        retry_policy=RetryPolicy(maximum_attempts=1),
                    )
                except ActivityError:
                    await project(run_id, "run.failed", "run:failed", error="agent_failed")
                    return "failed"
            elif kind == "approval":
                config = node["config"]
                approval_id: str = await workflow.execute_activity(
                    "open_approval",
                    {
                        "run_id": run_id,
                        "node_id": current,
                        "title": config["title"],
                        "timeout_seconds": config["timeout_seconds"],
                    },
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=PROJECTION_RETRY,
                )
                await project(
                    run_id,
                    "step.awaiting_approval",
                    f"{current}:awaiting",
                    node_id=current,
                    output={"approval_id": approval_id},
                )
                self._expected_approval_id = approval_id
                try:
                    await workflow.wait_condition(
                        self.approval_ready,
                        timeout=timedelta(seconds=config["timeout_seconds"]),
                    )
                    expired = False
                except TimeoutError:
                    expired = True
                status: str = await workflow.execute_activity(
                    "approval_status",
                    {"approval_id": approval_id, "expire": expired},
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=PROJECTION_RETRY,
                )
                if status != "approved":
                    await project(
                        run_id,
                        "step.failed",
                        f"{current}:terminal-failed",
                        node_id=current,
                        error="approval_rejected" if status == "rejected" else "approval_expired",
                    )
                    await project(run_id, "run.failed", "run:failed", error="approval_denied")
                    return "failed"
                result = {"decision": "approved", "approval_id": approval_id}
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
