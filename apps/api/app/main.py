import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from temporalio.client import Client

from app.config import get_settings


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, str] | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    get_settings()
    yield


app = FastAPI(title="Automiq Control Plane", lifespan=lifespan)


@app.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/health/ready", response_model=HealthResponse)
async def ready(response: Response) -> HealthResponse:
    settings = get_settings()
    checks: dict[str, str] = {}
    engine = create_async_engine(settings.database_url, connect_args={"timeout": 3})
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "unavailable"
    finally:
        await engine.dispose()

    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3)
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"
    finally:
        await redis.aclose()

    try:
        client = await asyncio.wait_for(
            Client.connect(settings.temporal_address, namespace=settings.temporal_namespace),
            timeout=3,
        )
        await client.service_client.check_health()
        checks["temporal"] = "ok"
    except Exception:
        checks["temporal"] = "unavailable"

    healthy = all(value == "ok" for value in checks.values())
    if not healthy:
        response.status_code = 503
    return HealthResponse(status="ok" if healthy else "degraded", checks=checks)
