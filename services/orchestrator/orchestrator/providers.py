"""Fixed-origin GitHub and Slack operations with bounded responses.

Mutations use an effect ledger. An uncertain result fails closed for review;
it is never blindly repeated after a worker crash or transport loss.
"""

import asyncio
import json
import re
import ssl
import uuid
from typing import Any
from urllib.parse import quote

import httpx
from app.config import get_settings
from app.db import session_factory
from app.graph import GithubCommentConfig, SlackMessageConfig
from app.integration_crypto import decrypt_credentials
from app.models import ActionEffect, AuditLog, Integration, WorkflowRun
from fastapi import HTTPException
from sqlalchemy import select

from orchestrator.http_connector import HttpActionError, pinned_address

MAX_PROVIDER_RESPONSE = 16_384


class ProviderError(Exception):
    def __init__(
        self, code: str, *, retryable: bool = False, retry_after: int | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after


async def resolve_integration(
    run_id: uuid.UUID, integration_id: uuid.UUID, provider: str
) -> dict[str, str]:
    async with session_factory()() as db:
        run = await db.get(WorkflowRun, run_id)
        if run is None:
            raise ProviderError("run_missing")
        row = await db.scalar(
            select(Integration).where(
                Integration.id == integration_id,
                Integration.workspace_id == run.workspace_id,
                Integration.provider == provider,
                Integration.revoked_at.is_(None),
            )
        )
        if row is None:
            raise ProviderError("integration_unavailable")
        try:
            return decrypt_credentials(
                row.workspace_id,
                row.id,
                row.provider,
                row.encrypted_credentials,
                row.key_version,
            )
        except HTTPException:
            raise ProviderError("integration_unavailable") from None


async def provider_json(
    host: str,
    method: str,
    path: str,
    token: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    mutation: bool = False,
) -> dict[str, Any]:
    if host not in {"api.github.com", "slack.com"} or not path.startswith("/"):
        raise ProviderError("unsafe_destination")
    try:
        address = await pinned_address(host, 443, get_settings())
        authority = f"[{address}]" if ":" in address else address
        async with asyncio.timeout(12):
            async with httpx.AsyncClient(
                verify=ssl.create_default_context(),
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(10, connect=3),
            ) as client:
                async with client.stream(
                    method,
                    f"https://{authority}:443{path}",
                    params=params,
                    json=body,
                    headers={
                        "Host": host,
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json"
                        if host == "api.github.com"
                        else "application/json",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    },
                    extensions={"sni_hostname": host},
                ) as response:
                    if response.status_code == 429 or (
                        response.status_code == 403
                        and (
                            response.headers.get("x-ratelimit-remaining") == "0"
                            or response.headers.get("retry-after")
                        )
                    ):
                        header = response.headers.get("retry-after", "")
                        delay = min(30, max(1, int(header))) if header.isdecimal() else None
                        raise ProviderError("rate_limited", retryable=True, retry_after=delay)
                    if response.status_code >= 500:
                        raise ProviderError("provider_unavailable", retryable=not mutation)
                    if response.status_code in {401, 403}:
                        raise ProviderError("auth_error")
                    if response.status_code >= 300:
                        raise ProviderError("provider_rejected")
                    data = bytearray()
                    async for chunk in response.aiter_raw():
                        data.extend(chunk)
                        if len(data) > MAX_PROVIDER_RESPONSE:
                            raise ProviderError("provider_response_too_large")
                    try:
                        parsed = json.loads(data)
                    except (ValueError, UnicodeDecodeError):
                        raise ProviderError("invalid_provider_response") from None
                    if not isinstance(parsed, dict):
                        raise ProviderError("invalid_provider_response")
                    return parsed
    except HttpActionError as exc:
        raise ProviderError(exc.code, retryable=exc.retryable and not mutation) from None
    except (TimeoutError, httpx.TransportError):
        raise ProviderError(
            "ambiguous_provider_result" if mutation else "provider_transport",
            retryable=not mutation,
        ) from None


async def github_search_issues(
    run_id: uuid.UUID,
    integration_id: uuid.UUID,
    query: str,
) -> list[dict[str, Any]]:
    """Read-only tool entrypoint for the bounded Phase 5 agent."""
    if len(query) > 120 or not re.fullmatch(r"[A-Za-z0-9 _.-]+", query):
        raise ProviderError("invalid_search_query")
    values = await resolve_integration(run_id, integration_id, "github")
    if get_settings().integration_provider_mode == "fake":
        result: list[dict[str, Any]] = []
    else:
        repo = values.get("repository", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ProviderError("integration_unavailable")
        data = await provider_json(
            "api.github.com",
            "GET",
            "/search/issues",
            values["token"],
            params={"q": f"repo:{repo} is:issue {query}", "per_page": "10"},
        )
        items = data.get("items")
        if not isinstance(items, list):
            raise ProviderError("invalid_provider_response")
        result = [
            {"number": item["number"], "state": item["state"]}
            for item in items[:10]
            if isinstance(item, dict)
            and isinstance(item.get("number"), int)
            and item.get("state") in {"open", "closed"}
        ]
    async with session_factory()() as db:
        run = await db.get(WorkflowRun, run_id)
        if run is None:
            raise ProviderError("run_missing")
        db.add(
            AuditLog(
                workspace_id=run.workspace_id,
                actor_type="service",
                actor_id=None,
                action="integration.search",
                resource_type="integrations",
                resource_id=integration_id,
                metadata_json={"provider": "github", "result_count": len(result)},
            )
        )
        await db.commit()
    return result


async def execute_provider_action(command: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(command["run_id"])
    node_id = str(command["node_id"])
    kind = str(command["kind"])
    if kind not in {"action.github_comment", "action.slack_message"}:
        raise ProviderError("invalid_action")
    config = (
        GithubCommentConfig.model_validate(command["config"])
        if kind == "action.github_comment"
        else SlackMessageConfig.model_validate(command["config"])
    )
    provider = "github" if kind == "action.github_comment" else "slack"
    effect_key = f"{run_id}:{node_id}"
    async with session_factory()() as db:
        run = await db.get(WorkflowRun, run_id)
        if run is None:
            raise ProviderError("run_missing")
        trigger = run.input_json.get("payload", {})
        workspace_id = run.workspace_id
        completed = await db.get(ActionEffect, effect_key)
        saved = completed.output_json if completed is not None else None
        if completed is not None and saved is not None and saved.get("_state") == "done":
            if not isinstance(saved.get("result"), dict):
                raise ProviderError("effect_corrupt")
            return {"output": saved["result"], "attempts": completed.invocations}
    values = await resolve_integration(run_id, config.integration_id, provider)
    if provider == "github":
        repo = values.get("repository", "")
        if repo != trigger.get("repository") or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo
        ):
            raise ProviderError("repository_mismatch")
        issue_number = trigger.get("issue_number")
        if not isinstance(issue_number, int) or issue_number < 1:
            raise ProviderError("invalid_issue")

    async with session_factory()() as db:
        async with db.begin():
            effect = await db.get(ActionEffect, effect_key, with_for_update=True)
            if effect is not None:
                saved = effect.output_json or {}
                if saved.get("_state") == "done":
                    if not isinstance(saved.get("result"), dict):
                        raise ProviderError("effect_corrupt")
                    return {"output": saved["result"], "attempts": effect.invocations}
                if saved.get("_state") != "retryable":
                    raise ProviderError("ambiguous_provider_result")
                effect.invocations += 1
                attempts = effect.invocations
                effect.output_json = {"_state": "started"}
            else:
                attempts = 1
                db.add(
                    ActionEffect(
                        effect_key=effect_key,
                        run_id=run_id,
                        node_id=node_id,
                        invocations=attempts,
                        output_json={"_state": "started"},
                    )
                )
    try:
        if get_settings().integration_provider_mode == "fake":
            if get_settings().integration_fake_rate_limit_first and attempts == 1:
                raise ProviderError("rate_limited", retryable=True, retry_after=1)
            result = {"provider": provider, "fake": True, "delivered": True}
        elif provider == "github":
            assert isinstance(config, GithubCommentConfig)
            path = f"/repos/{quote(repo, safe='/')}/issues/{issue_number}/comments"
            data = await provider_json(
                "api.github.com",
                "POST",
                path,
                values["token"],
                body={"body": config.body},
                mutation=True,
            )
            if not isinstance(data.get("id"), int):
                raise ProviderError("invalid_provider_response")
            result = {"provider": "github", "comment_id": data["id"]}
        else:
            assert isinstance(config, SlackMessageConfig)
            data = await provider_json(
                "slack.com",
                "POST",
                "/api/chat.postMessage",
                values["token"],
                body={"channel": config.channel, "text": config.text},
                mutation=True,
            )
            if data.get("error") == "ratelimited":
                raise ProviderError("rate_limited", retryable=True)
            if data.get("ok") is not True or not isinstance(data.get("ts"), str):
                raise ProviderError("provider_rejected")
            result = {"provider": "slack", "message_ts": data["ts"]}
    except ProviderError as exc:
        async with session_factory()() as db:
            async with db.begin():
                effect = await db.get(ActionEffect, effect_key, with_for_update=True)
                assert effect is not None
                effect.output_json = {"_state": "retryable" if exc.retryable else "uncertain"}
                db.add(
                    AuditLog(
                        workspace_id=workspace_id,
                        actor_type="service",
                        actor_id=None,
                        action="integration.action.failed",
                        resource_type="action_effects",
                        resource_id=run_id,
                        metadata_json={"node_id": node_id, "code": exc.code},
                    )
                )
        raise
    async with session_factory()() as db:
        async with db.begin():
            effect = await db.get(ActionEffect, effect_key, with_for_update=True)
            assert effect is not None
            effect.output_json = {"_state": "done", "result": result}
            db.add(
                AuditLog(
                    workspace_id=workspace_id,
                    actor_type="service",
                    actor_id=None,
                    action="integration.action.succeeded",
                    resource_type="action_effects",
                    resource_id=run_id,
                    metadata_json={"node_id": node_id, "provider": provider},
                )
            )
    return {"output": result, "attempts": attempts}
