"""Workspace-scoped workflow definition API."""

import hashlib
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth import Db, UserDep, membership, require_csrf
from app.config import get_settings
from app.graph import EMPTY_GRAPH, Diagnostic, Graph, validate_graph
from app.models import AuditLog, Integration, Membership, User, Workflow, WorkflowVersion, Workspace

router = APIRouter(prefix="/api/v1", tags=["workflows"])


def runnable_diagnostics(graph: Graph) -> list[Diagnostic]:
    diagnostics = validate_graph(graph)
    if not get_settings().http_connector_enabled:
        diagnostics.extend(
            Diagnostic(
                code="http_connector_disabled",
                message="HTTPS GET connector is disabled for this environment",
                node_id=node.id,
            )
            for node in graph.nodes
            if node.type == "action.http" and node.config.operation == "https_get"
        )
    return diagnostics


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=63)


class WorkspaceOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    role: str


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    csrf_token: str
    workspaces: list[WorkspaceOut]


class MemberGrant(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(pattern=r"^(owner|editor|viewer)$")


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    role: str


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class WorkflowRename(WorkflowCreate):
    pass


class WorkflowOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    status: str
    draft_graph: Graph
    draft_revision: int
    published_version_id: uuid.UUID | None
    updated_at: datetime


class DraftSave(BaseModel):
    revision: int = Field(ge=0)
    graph: Graph


class PublishRequest(BaseModel):
    revision: int = Field(ge=0)


class VersionOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    version: int
    checksum: str
    published_at: datetime
    graph: Graph


class ValidationOut(BaseModel):
    valid: bool
    diagnostics: list[Diagnostic]


def out(workflow: Workflow) -> WorkflowOut:
    return WorkflowOut(
        id=workflow.id,
        workspace_id=workflow.workspace_id,
        name=workflow.name,
        status=workflow.status,
        draft_graph=Graph.model_validate(workflow.draft_graph),
        draft_revision=workflow.draft_revision,
        published_version_id=workflow.published_version_id,
        updated_at=workflow.updated_at,
    )


def audit(
    workspace_id: uuid.UUID, user_id: uuid.UUID, action: str, resource: Workflow | Workspace
) -> AuditLog:
    return AuditLog(
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=user_id,
        action=action,
        resource_type=resource.__tablename__,
        resource_id=resource.id,
        metadata_json={},
    )


async def scoped_workflow(db: Db, ws: uuid.UUID, workflow_id: uuid.UUID) -> Workflow:
    workflow = await db.scalar(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.workspace_id == ws)
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


@router.get("/me", response_model=MeOut)
async def me(request: Request, db: Db, user: UserDep) -> MeOut:
    rows = await db.execute(
        select(Workspace, Membership.role)
        .join(Membership)
        .where(Membership.user_id == user.id)
        .order_by(Workspace.created_at)
    )
    return MeOut(
        id=user.id,
        email=user.email,
        csrf_token=request.session["csrf_token"],
        workspaces=[
            WorkspaceOut(id=ws.id, name=ws.name, slug=ws.slug, role=role) for ws, role in rows
        ],
    )


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
async def create_workspace(
    body: WorkspaceCreate, request: Request, db: Db, user: UserDep
) -> WorkspaceOut:
    require_csrf(request)
    ws = Workspace(name=body.name.strip(), slug=body.slug)
    if not ws.name:
        raise HTTPException(status_code=422, detail="Name is required")
    db.add(ws)
    try:
        await db.flush()
        db.add(Membership(workspace_id=ws.id, user_id=user.id, role="owner"))
        db.add(audit(ws.id, user.id, "workspace.create", ws))
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Workspace slug already exists") from exc
    return WorkspaceOut(id=ws.id, name=ws.name, slug=ws.slug, role="owner")


@router.get("/workspaces", response_model=list[WorkspaceOut])
async def list_workspaces(db: Db, user: UserDep) -> list[WorkspaceOut]:
    rows = await db.execute(
        select(Workspace, Membership.role)
        .join(Membership)
        .where(Membership.user_id == user.id)
        .order_by(Workspace.created_at)
    )
    return [WorkspaceOut(id=ws.id, name=ws.name, slug=ws.slug, role=role) for ws, role in rows]


@router.get("/workspaces/{ws}/memberships", response_model=list[MemberOut])
async def list_memberships(ws: uuid.UUID, db: Db, user: UserDep) -> list[MemberOut]:
    await membership(db, ws, user, "manage")
    rows = await db.execute(
        select(User, Membership.role)
        .join(Membership)
        .where(Membership.workspace_id == ws)
        .order_by(User.email)
    )
    return [MemberOut(user_id=person.id, email=person.email, role=role) for person, role in rows]


@router.post("/workspaces/{ws}/memberships", response_model=MemberOut)
async def grant_membership(
    ws: uuid.UUID, body: MemberGrant, request: Request, db: Db, user: UserDep
) -> MemberOut:
    require_csrf(request)
    await membership(db, ws, user, "manage")
    people = list(
        await db.scalars(
            select(User).where(func.lower(User.email) == body.email.strip().lower()).limit(2)
        )
    )
    if not people:
        raise HTTPException(
            status_code=404, detail="User must sign in before access can be granted"
        )
    if len(people) > 1:
        raise HTTPException(status_code=409, detail="Email matches multiple identities")
    person = people[0]
    if person.id == user.id:
        raise HTTPException(status_code=409, detail="Cannot change your own owner role")
    existing = await db.scalar(
        select(Membership)
        .where(Membership.workspace_id == ws, Membership.user_id == person.id)
        .with_for_update()
    )
    if existing is None:
        db.add(Membership(workspace_id=ws, user_id=person.id, role=body.role))
    else:
        existing.role = body.role
    db.add(
        AuditLog(
            workspace_id=ws,
            actor_type="user",
            actor_id=user.id,
            action="membership.grant",
            resource_type="users",
            resource_id=person.id,
            metadata_json={"role": body.role},
        )
    )
    await db.commit()
    return MemberOut(user_id=person.id, email=person.email, role=body.role)


@router.get("/workspaces/{ws}/workflows", response_model=list[WorkflowOut])
async def list_workflows(ws: uuid.UUID, db: Db, user: UserDep) -> list[WorkflowOut]:
    await membership(db, ws, user, "read")
    rows = await db.scalars(
        select(Workflow)
        .where(Workflow.workspace_id == ws)
        .order_by(Workflow.updated_at.desc())
        .limit(100)
    )
    return [out(row) for row in rows]


@router.post("/workspaces/{ws}/workflows", response_model=WorkflowOut, status_code=201)
async def create_workflow(
    ws: uuid.UUID, body: WorkflowCreate, request: Request, db: Db, user: UserDep
) -> WorkflowOut:
    require_csrf(request)
    await membership(db, ws, user, "write")
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    workflow = Workflow(
        workspace_id=ws, name=body.name.strip(), draft_graph=EMPTY_GRAPH.model_dump(mode="json")
    )
    db.add(workflow)
    await db.flush()
    db.add(audit(ws, user.id, "workflow.create", workflow))
    await db.commit()
    await db.refresh(workflow)
    return out(workflow)


@router.get("/workspaces/{ws}/workflows/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(ws: uuid.UUID, workflow_id: uuid.UUID, db: Db, user: UserDep) -> WorkflowOut:
    await membership(db, ws, user, "read")
    return out(await scoped_workflow(db, ws, workflow_id))


@router.patch("/workspaces/{ws}/workflows/{workflow_id}", response_model=WorkflowOut)
async def rename_workflow(
    ws: uuid.UUID,
    workflow_id: uuid.UUID,
    body: WorkflowRename,
    request: Request,
    db: Db,
    user: UserDep,
) -> WorkflowOut:
    require_csrf(request)
    await membership(db, ws, user, "write")
    workflow = await scoped_workflow(db, ws, workflow_id)
    if workflow.status != "active" or not body.name.strip():
        raise HTTPException(status_code=409, detail="Workflow cannot be renamed")
    workflow.name = body.name.strip()
    db.add(audit(ws, user.id, "workflow.rename", workflow))
    await db.commit()
    await db.refresh(workflow)
    return out(workflow)


@router.post("/workspaces/{ws}/workflows/{workflow_id}/archive", response_model=WorkflowOut)
async def archive_workflow(
    ws: uuid.UUID, workflow_id: uuid.UUID, request: Request, db: Db, user: UserDep
) -> WorkflowOut:
    require_csrf(request)
    await membership(db, ws, user, "write")
    workflow = await scoped_workflow(db, ws, workflow_id)
    workflow.status = "archived"
    db.add(audit(ws, user.id, "workflow.archive", workflow))
    await db.commit()
    await db.refresh(workflow)
    return out(workflow)


@router.put("/workspaces/{ws}/workflows/{workflow_id}/draft", response_model=WorkflowOut)
async def save_draft(
    ws: uuid.UUID, workflow_id: uuid.UUID, body: DraftSave, request: Request, db: Db, user: UserDep
) -> WorkflowOut:
    require_csrf(request)
    await membership(db, ws, user, "write")
    workflow = await db.scalar(
        select(Workflow)
        .where(Workflow.id == workflow_id, Workflow.workspace_id == ws)
        .with_for_update()
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if workflow.status != "active":
        raise HTTPException(status_code=409, detail="Archived workflow")
    if workflow.draft_revision != body.revision:
        raise HTTPException(status_code=409, detail="Draft revision conflict")
    workflow.draft_graph = body.graph.model_dump(mode="json")
    workflow.draft_revision += 1
    db.add(audit(ws, user.id, "workflow.draft.save", workflow))
    await db.commit()
    await db.refresh(workflow)
    return out(workflow)


@router.post("/workspaces/{ws}/workflows/{workflow_id}/validate", response_model=ValidationOut)
async def validate_draft(
    ws: uuid.UUID, workflow_id: uuid.UUID, graph: Graph, request: Request, db: Db, user: UserDep
) -> ValidationOut:
    require_csrf(request)
    await membership(db, ws, user, "write")
    await scoped_workflow(db, ws, workflow_id)
    diagnostics = runnable_diagnostics(graph)
    return ValidationOut(valid=not diagnostics, diagnostics=diagnostics)


@router.post("/workspaces/{ws}/workflows/{workflow_id}/publish", response_model=VersionOut)
async def publish(
    ws: uuid.UUID,
    workflow_id: uuid.UUID,
    body: PublishRequest,
    request: Request,
    db: Db,
    user: UserDep,
) -> VersionOut:
    require_csrf(request)
    await membership(db, ws, user, "write")
    workflow = await db.scalar(
        select(Workflow)
        .where(Workflow.id == workflow_id, Workflow.workspace_id == ws)
        .with_for_update()
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if workflow.status != "active" or workflow.draft_revision != body.revision:
        raise HTTPException(status_code=409, detail="Archived workflow or draft revision conflict")
    graph = Graph.model_validate(workflow.draft_graph)
    diagnostics = runnable_diagnostics(graph)
    for node in graph.nodes:
        integration_id: uuid.UUID | None
        if node.type == "action.github_comment":
            provider = "github"
            integration_id = node.config.integration_id
        elif node.type == "action.slack_message":
            provider = "slack"
            integration_id = node.config.integration_id
        elif node.type == "agent" and node.config.allowed_tools:
            provider = "github"
            integration_id = node.config.github_integration_id
        else:
            continue
        integration = await db.scalar(
            select(Integration).where(
                Integration.id == integration_id,
                Integration.workspace_id == ws,
                Integration.provider == provider,
                Integration.revoked_at.is_(None),
            )
        )
        if integration is None:
            diagnostics.append(
                Diagnostic(
                    code="invalid_integration",
                    message="Node needs an active integration in this workspace",
                    node_id=node.id,
                )
            )
        if node.type == "action.github_comment" and not any(
            item.type == "trigger.github_issue" for item in graph.nodes
        ):
            diagnostics.append(
                Diagnostic(
                    code="invalid_trigger",
                    message="GitHub comment action needs a GitHub issue trigger",
                    node_id=node.id,
                )
            )
    if diagnostics:
        raise HTTPException(status_code=422, detail=[item.model_dump() for item in diagnostics])
    canonical = json.dumps(graph.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    number = (
        await db.scalar(
            select(func.max(WorkflowVersion.version)).where(
                WorkflowVersion.workflow_id == workflow.id
            )
        )
    ) or 0
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version=number + 1,
        graph_json=graph.model_dump(mode="json"),
        schema_version=graph.schema_version,
        checksum=hashlib.sha256(canonical.encode()).hexdigest(),
    )
    db.add(version)
    await db.flush()
    workflow.published_version_id = version.id
    db.add(audit(ws, user.id, "workflow.publish", workflow))
    await db.commit()
    return VersionOut(
        id=version.id,
        workflow_id=workflow.id,
        version=version.version,
        checksum=version.checksum,
        published_at=version.published_at,
        graph=graph,
    )


@router.get("/workspaces/{ws}/workflows/{workflow_id}/versions", response_model=list[VersionOut])
async def list_versions(
    ws: uuid.UUID, workflow_id: uuid.UUID, db: Db, user: UserDep
) -> list[VersionOut]:
    await membership(db, ws, user, "read")
    await scoped_workflow(db, ws, workflow_id)
    versions = await db.scalars(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.version.desc())
        .limit(100)
    )
    return [
        VersionOut(
            id=v.id,
            workflow_id=v.workflow_id,
            version=v.version,
            checksum=v.checksum,
            published_at=v.published_at,
            graph=Graph.model_validate(v.graph_json),
        )
        for v in versions
    ]
