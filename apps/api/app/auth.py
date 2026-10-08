"""OIDC sign-in, local-only synthetic identities, and workspace permissions."""

import secrets
import uuid
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated, Literal, cast

from authlib.integrations.starlette_client import OAuth  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import session_factory
from app.models import Membership, User

Role = Literal["owner", "editor", "viewer"]
Permission = Literal["read", "write", "manage", "run"]
ALLOWED: dict[Permission, set[Role]] = {
    "read": {"owner", "editor", "viewer"},
    "write": {"owner", "editor"},
    "manage": {"owner"},
    "run": {"owner", "editor"},
}


async def database() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        yield session


Db = Annotated[AsyncSession, Depends(database)]


async def current_user(request: Request, db: Db) -> User:
    user_id = request.session.get("user_id")
    try:
        parsed = uuid.UUID(user_id) if isinstance(user_id, str) else None
    except ValueError:
        parsed = None
    user = await db.get(User, parsed) if parsed else None
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    return user


UserDep = Annotated[User, Depends(current_user)]


def require_csrf(request: Request) -> None:
    settings = get_settings()
    origin = request.headers.get("origin")
    token = request.headers.get("x-csrf-token")
    if (
        origin != settings.web_origin
        or not token
        or not secrets.compare_digest(token, request.session.get("csrf_token", ""))
    ):
        raise HTTPException(status_code=403, detail="CSRF check failed")


def require_dev_origin(request: Request) -> None:
    if get_settings().app_env not in {"development", "test"}:
        raise HTTPException(status_code=404, detail="Not found")
    if request.headers.get("origin") != get_settings().web_origin:
        raise HTTPException(status_code=403, detail="Origin check failed")


async def membership(
    db: AsyncSession, workspace_id: uuid.UUID, user: User, permission: Permission
) -> Membership:
    result = await db.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == user.id
        )
    )
    # Mask a foreign workspace ID as missing; role denials remain explicit.
    if result is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if result.role not in ALLOWED[permission]:
        raise HTTPException(status_code=403, detail="Permission denied")
    return result


class DevSignIn(BaseModel):
    identity: Literal["owner", "editor", "viewer"]


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/modes")
async def auth_modes() -> dict[str, bool]:
    return {"dev_identity": get_settings().app_env in {"development", "test"}}


@router.post("/dev")
async def dev_sign_in(body: DevSignIn, request: Request, db: Db) -> dict[str, str]:
    require_dev_origin(request)
    issuer, subject = "urn:automiq:local-test", body.identity
    async with db.begin():
        user = await db.scalar(
            select(User).where(User.oidc_issuer == issuer, User.oidc_subject == subject)
        )
        if user is None:
            user = User(oidc_issuer=issuer, oidc_subject=subject, email=f"{subject}@local.test")
            db.add(user)
            await db.flush()
    request.session.clear()
    request.session["user_id"] = str(user.id)
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    return {"status": "signed_in"}


@lru_cache
def oidc() -> OAuth:
    settings = get_settings()
    if not (
        settings.oidc_issuer.startswith("https://")
        and settings.oidc_client_id
        and settings.oidc_client_secret.get_secret_value()
    ):
        raise HTTPException(status_code=503, detail="OIDC provider is not configured")
    oauth = OAuth()
    oauth.register(
        name="provider",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret.get_secret_value(),
        server_metadata_url=f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth


@router.get("/login")
async def oidc_login(request: Request) -> Response:
    client = oidc().create_client("provider")
    assert client is not None
    callback = f"{get_settings().api_public_url.rstrip('/')}/auth/callback"
    return cast(Response, await client.authorize_redirect(request, callback))


@router.get("/callback")
async def oidc_callback(request: Request, db: Db) -> Response:
    client = oidc().create_client("provider")
    assert client is not None
    try:
        token = await client.authorize_access_token(request)
        identity = token["userinfo"]
        subject = identity["sub"]
        email = identity.get("email", "")
        if not subject or not email or identity.get("email_verified") is not True:
            raise ValueError("Verified email and subject required")
    except Exception as exc:
        # Never expose provider errors or tokens in the response.
        raise HTTPException(status_code=401, detail="OIDC sign-in failed") from exc
    settings = get_settings()
    async with db.begin():
        user = await db.scalar(
            select(User).where(
                User.oidc_issuer == settings.oidc_issuer, User.oidc_subject == subject
            )
        )
        if user is None:
            user = User(oidc_issuer=settings.oidc_issuer, oidc_subject=subject, email=email)
            db.add(user)
            await db.flush()
        elif user.email != email:
            user.email = email
    request.session.clear()
    request.session["user_id"] = str(user.id)
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    from starlette.responses import RedirectResponse

    return RedirectResponse(settings.web_origin, status_code=303)


@router.post("/logout")
async def logout(request: Request) -> dict[str, str]:
    require_csrf(request)
    request.session.clear()
    return {"status": "signed_out"}
