"""Bounded read-only checks used by durable Temporal wait nodes."""

import re
import uuid
from typing import Any

from app.config import get_settings
from app.db import session_factory
from app.graph import AppEventWaitConfig, GithubPrMergeWaitConfig
from app.models import AppConnection, AppEventReceipt, WorkflowRun
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from orchestrator.providers import ProviderError, provider_json, resolve_integration


@activity.defn(name="check_app_event_wait")
async def check_app_event_wait(command: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(command["run_id"])
    config = AppEventWaitConfig.model_validate(command["config"])
    value = command["correlation_value"]
    if isinstance(value, bool) or not isinstance(value, (str, int)) or len(str(value)) > 2000:
        raise ApplicationError("invalid_correlation", non_retryable=True)
    async with session_factory()() as db:
        run = await db.get(WorkflowRun, run_id)
        if run is None:
            raise ApplicationError("run_missing", non_retryable=True)
        connection = await db.scalar(
            select(AppConnection).where(
                AppConnection.id == config.connection_id,
                AppConnection.workspace_id == run.workspace_id,
                AppConnection.contract_version == config.contract_version,
                AppConnection.revoked_at.is_(None),
            )
        )
        if connection is None or config.event_type not in connection.inbound_schemas:
            raise ApplicationError("app_connection_unavailable", non_retryable=True)
        receipt = await db.scalar(
            select(AppEventReceipt)
            .where(
                AppEventReceipt.connection_id == connection.id,
                AppEventReceipt.event_type == config.event_type,
                AppEventReceipt.received_at >= run.created_at,
                AppEventReceipt.data_json.contains({config.correlation_field: value}),
            )
            .order_by(AppEventReceipt.received_at, AppEventReceipt.id)
            .limit(1)
        )
        if receipt is None:
            return {"state": "pending"}
        return {
            "state": "matched",
            "event_id": receipt.event_id,
            "data": receipt.data_json,
        }


@activity.defn(name="check_github_pr_merge")
async def check_github_pr_merge(command: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(command["run_id"])
    config = GithubPrMergeWaitConfig.model_validate(command["config"])
    number = command["pr_number"]
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ApplicationError("invalid_pr_number", non_retryable=True)
    try:
        values = await resolve_integration(run_id, config.integration_id, "github")
        repository = values.get("repository", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ProviderError("integration_unavailable")
        if get_settings().integration_provider_mode == "fake":
            # The deterministic test adapter stays open for one check, then merges.
            state = "open" if command["poll_index"] == 0 else "merged"
        else:
            from urllib.parse import quote

            data = await provider_json(
                "api.github.com",
                "GET",
                f"/repos/{quote(repository, safe='/')}/pulls/{number}",
                values["token"],
            )
            if data.get("number") != number or data.get("state") not in {"open", "closed"}:
                raise ProviderError("invalid_provider_response")
            merged_at = data.get("merged_at")
            if merged_at is not None and (not isinstance(merged_at, str) or len(merged_at) > 80):
                raise ProviderError("invalid_provider_response")
            state = "merged" if merged_at else (
                "closed_unmerged" if data["state"] == "closed" else "open"
            )
        return {"state": state, "repository": repository, "pr_number": number}
    except ProviderError as exc:
        raise ApplicationError(exc.code, non_retryable=not exc.retryable) from None
