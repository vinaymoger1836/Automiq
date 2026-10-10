"""Shared Redis ingress limits and serialized workspace run admission."""

import uuid

from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import WorkflowRun, Workspace

_WINDOW = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


async def admit_request(kind: str, identifier: uuid.UUID, limit: int) -> None:
    """Use one Redis key per source and minute; fail closed when unavailable."""
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        count = await redis.eval(_WINDOW, 1, f"limit:{kind}:{identifier}", 60)
    except Exception:
        raise HTTPException(status_code=503, detail="Request limiter unavailable") from None
    finally:
        await redis.aclose()
    if int(count) > limit:
        raise HTTPException(
            status_code=429,
            detail="Request rate limit exceeded",
            headers={"Retry-After": "60"},
        )


async def workspace_has_capacity(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """Caller holds the workspace row lock until its new run is committed."""
    workspace = await db.scalar(
        select(Workspace).where(Workspace.id == workspace_id).with_for_update()
    )
    if workspace is None:
        return False
    active = await db.scalar(
        select(func.count(WorkflowRun.id)).where(
            WorkflowRun.workspace_id == workspace_id,
            WorkflowRun.status.in_(["queued", "running"]),
        )
    )
    return int(active or 0) < get_settings().workspace_active_run_limit
