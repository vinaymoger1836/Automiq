import asyncio
import hmac
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace
from opentelemetry.propagate import extract
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse
from temporalio.client import Client

from app.approvals import router as approvals_router
from app.auth import router as auth_router
from app.config import get_settings
from app.integrations import router as integrations_router
from app.observability import (
    HTTP_DURATION,
    HTTP_REQUESTS,
    close_tracing,
    configure_tracing,
    event,
)
from app.runs import router as runs_router
from app.workflows import router as workflows_router


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, str] | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_tracing("automiq-api", settings.otel_exporter_otlp_endpoint)
    yield
    close_tracing()


app = FastAPI(title="Automiq Control Plane", lifespan=lifespan)
settings = get_settings()
logger = logging.getLogger(__name__)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret.get_secret_value(),
    session_cookie="automiq_session",
    same_site="lax",
    https_only=settings.app_env == "production",
    max_age=60 * 60 * 12,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "Last-Event-ID"],
)
app.include_router(auth_router)
app.include_router(workflows_router)
app.include_router(runs_router)
app.include_router(integrations_router)
app.include_router(approvals_router)


@app.middleware("http")
async def request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
    request.state.request_id = str(uuid.uuid4())
    started = time.perf_counter()
    status = 500
    context = extract(dict(request.headers))
    with trace.get_tracer(__name__).start_as_current_span("http.request", context=context) as span:
        span.set_attribute("http.request.method", request.method)
        span.set_attribute("request.id", request.state.request_id)
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request.state.request_id
            return response
        finally:
            route = request.scope.get("route")
            template = getattr(route, "path", "unmatched")
            span.set_attribute("http.route", template)
            span.set_attribute("http.response.status_code", status)
            HTTP_REQUESTS.labels(request.method, template, str(status)).inc()
            HTTP_DURATION.labels(request.method, template).observe(time.perf_counter() - started)
            event(
                logger,
                "http.request",
                request_id=request.state.request_id,
                method=request.method,
                route=template,
                status=status,
            )


@app.get("/internal/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    token = settings.metrics_token.get_secret_value()
    if not token or not hmac.compare_digest(request.headers.get("x-metrics-token", ""), token):
        raise HTTPException(status_code=404, detail="Not found")
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    code = {
        401: "unauthenticated",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "payload_too_large",
        422: "validation_error",
    }.get(exc.status_code, "request_error")
    detail = exc.detail
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "error": {
                "code": code,
                "message": detail if isinstance(detail, str) else "Invalid workflow graph",
                "request_id": request.state.request_id,
                **({"diagnostics": detail} if isinstance(detail, list) else {}),
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, _exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Invalid request",
                "request_id": request.state.request_id,
            }
        },
    )


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
