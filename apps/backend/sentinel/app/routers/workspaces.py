from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.runtime.development_tools as development_tools_module
from app.dependencies import get_db, get_manager_db, get_request_run_registry
from app.models import Session, SubAgentTask, Workspace
from app.models.manager import Machine
from app.routers.workspace_browser import router as browser_router
from app.services.modules.runtime_services import get_browser_pool
from app.services.runtime import workspace_containers as containers
from app.services.runtime.directories import list_local_directories
from app.services.runtime.distributions import Distribution, validate_distribution_tools
from app.services.runtime.panes import TmuxPanes
from app.services.runtime.ssh_runtime import (
    get_runtime_terminal_manager,
    invalidate_runtime_for_session,
    runtime_configured,
)
from app.services.runtime.workspace_metrics import workspace_metrics

router = APIRouter()
router.include_router(browser_router)


class WorkspaceResources(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    cpus: int = Field(default=2, ge=1, le=32)
    memory_gib: int = Field(default=2, ge=1, le=64)
    disk_gib: int = Field(default=32, ge=8, le=1024)


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    machine_id: UUID
    directory: str = Field(min_length=1, max_length=4096)
    development_tools: list[str] = Field(default_factory=list, max_length=64)
    resources: WorkspaceResources | None = None
    distribution: Distribution = "alpine"

    @model_validator(mode="after")
    def distribution_tools(self):
        validate_distribution_tools(self.distribution, self.development_tools)
        return self

    @field_validator("development_tools")
    @classmethod
    def validate_tools(cls, value: list[str]) -> list[str]:

        if set(value) - development_tools_module.TOOL_IDS:
            raise ValueError("Unknown development tool")
        return sorted(set(value))

    @field_validator("name", "directory")
    @classmethod
    def clean(cls, value: str) -> str:
        value = value.strip()
        if not value or any(c in value for c in ("\x00", "\n", "\r")):
            raise ValueError("Enter a nonempty value without control characters")
        return value

    @field_validator("directory")
    @classmethod
    def path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts or str(path) == "/":
            raise ValueError("Choose an absolute project directory, not the filesystem root")
        return str(path)


class WorkspaceUpdate(BaseModel):
    distribution: Distribution | None = None
    directory: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, value):
        return WorkspaceCreate.path(WorkspaceCreate.clean(value)) if value is not None else None

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    resources: WorkspaceResources | None = None
    development_tools: list[str] | None = Field(default=None, max_length=64)

    @field_validator("development_tools")
    @classmethod
    def validate_tools(cls, value):
        return WorkspaceCreate.validate_tools(value) if value is not None else None


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    machine_id: UUID
    directory: str
    development_tools: list[str] = Field(default_factory=list)
    distribution: Distribution = "alpine"
    container_state: str = "stopped"
    container_error: str | None = None
    container_message: str | None = None
    recovery_backup: str | None = None
    recovery_available: bool = False
    resources: dict[str, int] | None = None


class WorkspaceAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: UUID | None


@router.get("/workspaces/{workspace_id}/metrics")
async def get_workspace_metrics(workspace_id: UUID, db: AsyncSession = Depends(get_db)):
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Workspace not found")
    await db.close()
    return await workspace_metrics.get(str(workspace_id))


@router.get("/workspaces", response_model=list[WorkspaceResponse])
async def list_workspaces(db: AsyncSession = Depends(get_db), include_runtime: bool = True):
    rows = list((await db.scalars(select(Workspace).order_by(Workspace.name))).all())
    for row in rows:
        containers.bind(row.id, row.machine_id, row.distribution)
    await db.close()
    if not include_runtime:
        return [
            WorkspaceResponse.model_validate(row).model_copy(update={"container_state": "checking"})
            for row in rows
        ]
    try:
        snapshot = await containers.overview() if rows else {}
        states = snapshot.get("states", {})
        unavailable = None
    except containers.WorkspaceContainerError as exc:
        snapshot, states, unavailable = {}, {}, str(exc)
    return [
        WorkspaceResponse.model_validate(row).model_copy(
            update={
                "container_state": (
                    "unavailable"
                    if unavailable
                    else states.get(str(row.id), {}).get("state", "stopped")
                ),
                "container_error": unavailable or states.get(str(row.id), {}).get("error"),
                "recovery_available": not unavailable
                and states.get(str(row.id), {}).get("recovery_available", False),
                "container_message": states.get(str(row.id), {}).get("message"),
                "resources": states.get(str(row.id), {}).get("resources")
                or snapshot.get("resources"),
            }
        )
        for row in rows
    ]


@router.get("/workspaces/{workspace_id}/status", response_model=WorkspaceResponse)
async def get_workspace_status(workspace_id: UUID, db: AsyncSession = Depends(get_db)):
    row = await db.get(Workspace, workspace_id)
    if row is None:
        raise HTTPException(404, "Workspace not found")
    containers.bind(row.id, row.machine_id, row.distribution)
    await db.close()
    try:
        states = await asyncio.wait_for(containers.statuses(workspace_id), timeout=8)
        state = states.get(str(workspace_id), {})
    except (TimeoutError, containers.WorkspaceContainerError) as exc:
        state = {
            "state": "unavailable",
            "error": (
                "Connection timed out. Retrying…" if isinstance(exc, TimeoutError) else str(exc)
            ),
        }
    return WorkspaceResponse.model_validate(row).model_copy(
        update={
            "container_state": state.get("state", "stopped"),
            "container_error": state.get("error"),
            "container_message": state.get("message"),
            "recovery_backup": state.get("recovery_backup"),
            "recovery_available": state.get("recovery_available", False),
            "resources": state.get("resources"),
        }
    )


@router.post("/workspaces", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    payload: WorkspaceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    manager: AsyncSession = Depends(get_manager_db),
):
    machine = await manager.get(Machine, payload.machine_id)
    if machine is None:
        raise HTTPException(404, "Machine not found")
    if (
        machine.provider not in {"local", "ssh"}
        or (machine.provider == "local" and not containers.available())
        or (machine.provider == "ssh" and not (machine.provider_config or {}).get("runtime_root"))
    ):
        raise HTTPException(
            422,
            "Install Sentinel Runtime for this machine before creating workspaces.",
        )
    await manager.close()
    row = Workspace(**payload.model_dump(exclude={"resources"}))
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "A workspace with this name already exists") from exc
    await db.refresh(row)
    containers.bind(row.id, row.machine_id, row.distribution)
    await db.close()
    try:
        await containers.start(
            row.id,
            row.directory,
            row.development_tools,
            notification_context={
                "instanceName": request.path_params["instance_name"],
                "name": row.name,
            },
            **(
                {"resources": payload.resources.model_dump()}
                if payload.resources is not None
                else {}
            ),
        )
    except containers.WorkspaceContainerError as exc:
        return WorkspaceResponse.model_validate(row).model_copy(
            update={"container_state": "failed", "container_error": str(exc)}
        )
    return WorkspaceResponse.model_validate(row).model_copy(
        update={
            "container_state": "preparing",
            "resources": payload.resources.model_dump() if payload.resources else None,
        }
    )


@router.post("/workspaces/{workspace_id}/start", response_model=WorkspaceResponse)
async def start_workspace(
    workspace_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    manager: AsyncSession = Depends(get_manager_db),
):
    row = await db.get(Workspace, workspace_id)
    if row is None:
        raise HTTPException(404, "Workspace not found")
    machine = await manager.get(Machine, row.machine_id)
    if machine is None or machine.provider not in {"local", "ssh"}:
        raise HTTPException(422, "Choose a local or enrolled remote Mac")
    containers.bind(row.id, row.machine_id, row.distribution)
    await manager.close()
    await db.close()
    try:
        await containers.start(
            row.id,
            row.directory,
            row.development_tools,
            notification_context={
                "instanceName": request.path_params["instance_name"],
                "name": row.name,
            },
        )
    except containers.WorkspaceContainerError as exc:
        raise HTTPException(503, str(exc)) from exc
    return WorkspaceResponse.model_validate(row).model_copy(update={"container_state": "preparing"})


@router.post("/workspaces/{workspace_id}/stop", status_code=204)
async def stop_workspace(workspace_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Workspace not found")

    registry = get_request_run_registry(request)
    sessions = await db.scalars(select(Session.id).where(Session.workspace_id == workspace_id))
    for session_id in sessions:
        if await registry.is_running(str(session_id)):
            raise HTTPException(409, "Stop the active agent before stopping its workspace")
    await db.close()
    try:
        await containers.stop(workspace_id)
    except containers.WorkspaceContainerError as exc:
        raise HTTPException(502, str(exc)) from exc


class WorkspaceRecovery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmed: bool


@router.post("/workspaces/{workspace_id}/recover", status_code=202)
async def recover_workspace(
    workspace_id: UUID,
    payload: WorkspaceRecovery,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not payload.confirmed:
        raise HTTPException(422, "Confirm that running commands and unsaved work will be lost")

    async with get_request_run_registry(request).workspace_change_guard() as running:
        row = await db.get(Workspace, workspace_id)
        if row is None:
            raise HTTPException(404, "Workspace not found")
        sessions = await _workspace_sessions(db, workspace_id)
        if any(session.running for session in await _removal_sessions(db, sessions, running)):
            raise HTTPException(409, "Stop this workspace's active agents before recovering it")
        containers.bind(row.id, row.machine_id, row.distribution)
        await db.close()
        try:
            result = await containers.request("recover", workspace=str(workspace_id))
        except containers.WorkspaceContainerError as exc:
            raise HTTPException(503, str(exc)) from exc
    return result


@router.post("/workspaces/{workspace_id}/reinstall", response_model=WorkspaceResponse)
async def reinstall_workspace(
    workspace_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    manager: AsyncSession = Depends(get_manager_db),
):

    async with get_request_run_registry(request).workspace_change_guard() as running:
        row = await db.get(Workspace, workspace_id)
        if row is None:
            raise HTTPException(404, "Workspace not found")
        containers.bind(row.id, row.machine_id, row.distribution)
        machine = await manager.get(Machine, row.machine_id)
        if machine is None or machine.provider not in {"local", "ssh"}:
            raise HTTPException(422, "Choose a local or enrolled remote Mac")
        sessions = await _workspace_sessions(db, workspace_id)
        if any(session.running for session in await _removal_sessions(db, sessions, running)):
            raise HTTPException(
                409,
                "Wait for the workspace's agents to finish before reinstalling tools",
            )
        await manager.close()
        await db.close()
        try:
            state = (await containers.statuses()).get(str(row.id), {}).get("state")
            if state in {"preparing", "stopping"}:
                raise HTTPException(
                    409, "Wait for workspace setup to finish before reinstalling tools"
                )
            await containers.reinstall(
                row.id,
                row.directory,
                row.development_tools,
                notification_context={
                    "instanceName": request.path_params["instance_name"],
                    "name": row.name,
                },
            )
        except containers.WorkspaceContainerError as exc:
            raise HTTPException(503, str(exc)) from exc
    return WorkspaceResponse.model_validate(row).model_copy(
        update={
            "container_state": "preparing",
            "container_message": "Reinstalling workspace tools…",
        }
    )


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: UUID,
    payload: WorkspaceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):

    async with get_request_run_registry(request).workspace_change_guard() as running:
        row = await db.get(Workspace, workspace_id)
        if row is None:
            raise HTTPException(404, "Workspace not found")
        containers.bind(row.id, row.machine_id, row.distribution)
        if payload.distribution is not None and payload.distribution != row.distribution:
            raise HTTPException(
                422,
                "Choose the distribution when creating a new workspace; existing disks cannot change distribution",
            )
        try:
            validate_distribution_tools(
                row.distribution,
                (
                    payload.development_tools
                    if payload.development_tools is not None
                    else row.development_tools
                ),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        name = payload.name.strip() if payload.name is not None else row.name
        if not name:
            raise HTTPException(422, "Enter a workspace name")
        tools_changed = (
            payload.development_tools is not None
            and payload.development_tools != row.development_tools
        )
        previous_directory = row.directory
        # Release the read connection while retaining objects for the later update.
        await db.commit()
        directory_changed = payload.directory is not None and payload.directory != row.directory
        if directory_changed:

            try:
                await asyncio.to_thread(list_local_directories, payload.directory)
            except OSError as exc:
                raise HTTPException(422, "Choose an existing, accessible project folder") from exc
        allocation = payload.resources.model_dump() if payload.resources else None
        resized = False
        state = {}
        if tools_changed or directory_changed or allocation is not None:
            if tools_changed and set(row.development_tools) - set(payload.development_tools):
                raise HTTPException(
                    422, "Installed workspace tools are kept; choose additional tools"
                )
            try:
                state = (await containers.statuses()).get(str(row.id), {})
            except containers.WorkspaceContainerError as exc:
                raise HTTPException(503, str(exc)) from exc
            current_resources = state.get("resources") or WorkspaceResources().model_dump()
            resized = allocation is not None and allocation != current_resources
            if allocation and allocation["disk_gib"] < current_resources["disk_gib"]:
                raise HTTPException(422, "Workspace disks can only be increased")
            if tools_changed or resized or directory_changed:
                sessions = await _workspace_sessions(db, workspace_id)
                if any(
                    session.running for session in await _removal_sessions(db, sessions, running)
                ):
                    raise HTTPException(
                        409,
                        "Wait for the workspace's agents to finish before changing its environment",
                    )
                if state.get("state") in {"preparing", "stopping"}:
                    raise HTTPException(
                        409,
                        "Wait for workspace setup to finish before changing settings",
                    )
        if tools_changed:
            row.development_tools = payload.development_tools
        if directory_changed:
            row.directory = payload.directory
        row.name = name
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(409, "A workspace with this name already exists") from exc
        await db.refresh(row)
        await db.commit()
        if tools_changed or resized or directory_changed:
            try:
                await containers.start(
                    row.id,
                    row.directory,
                    row.development_tools,
                    notification_context={
                        "instanceName": request.path_params["instance_name"],
                        "name": row.name,
                    },
                    **({"resources": allocation} if resized else {}),
                )
            except containers.WorkspaceContainerError as exc:
                if directory_changed:
                    row.directory = previous_directory
                    await db.commit()
                raise HTTPException(503, str(exc)) from exc
            if directory_changed:
                instance_name = request.path_params["instance_name"]
                manager = getattr(request.app.state, "ws_manager", None)
                for session in sessions:
                    await invalidate_runtime_for_session(
                        instance_name, session.id, stop_remote=False
                    )
                    if manager:
                        await manager.broadcast(
                            str(session.id),
                            {"type": "workspace_changed", "workspace_id": str(row.id)},
                        )
            return WorkspaceResponse.model_validate(row).model_copy(
                update={
                    "container_state": (
                        "stopped"
                        if (resized or directory_changed)
                        and not tools_changed
                        and state.get("state") == "stopped"
                        else "preparing"
                    ),
                    "resources": allocation or state.get("resources"),
                }
            )
        return WorkspaceResponse.model_validate(row).model_copy(
            update={
                "container_state": state.get("state", "stopped"),
                "resources": state.get("resources"),
            }
        )


class WorkspaceRemovalSession(BaseModel):
    id: UUID
    title: str
    running: bool


class WorkspaceRemoval(BaseModel):
    sessions: list[WorkspaceRemovalSession]


async def _workspace_sessions(db: AsyncSession, workspace_id: UUID) -> list[Session]:
    # Child conversations inherit the root's binding even with a null workspace_id.
    tree = select(Session.id).where(Session.workspace_id == workspace_id).cte(recursive=True)
    tree = tree.union(select(Session.id).join(tree, Session.parent_session_id == tree.c.id))
    return list((await db.scalars(select(Session).where(Session.id.in_(select(tree.c.id))))).all())


async def _removal_sessions(db: AsyncSession, sessions: list[Session], running: set[str]):
    delegated = set(
        await db.scalars(
            select(SubAgentTask.session_id).where(
                SubAgentTask.session_id.in_([session.id for session in sessions]),
                SubAgentTask.status.in_(["pending", "running"]),
            )
        )
    )
    return [
        WorkspaceRemovalSession(
            id=session.id,
            title=session.title or "Untitled session",
            running=str(session.id) in running or session.id in delegated,
        )
        for session in sessions
    ]


@router.get("/workspaces/{workspace_id}/removal", response_model=WorkspaceRemoval)
async def workspace_removal(
    workspace_id: UUID, request: Request, db: AsyncSession = Depends(get_db)
):

    async with get_request_run_registry(request).workspace_change_guard() as running:
        if await db.get(Workspace, workspace_id) is None:
            raise HTTPException(404, "Workspace not found")
        sessions = await _workspace_sessions(db, workspace_id)
        return WorkspaceRemoval(sessions=await _removal_sessions(db, sessions, running))


@router.delete("/workspaces/{workspace_id}", status_code=204)
async def delete_workspace(
    workspace_id: UUID,
    request: Request,
    detach_sessions: bool = False,
    db: AsyncSession = Depends(get_db),
):

    instance_name = request.path_params["instance_name"]
    async with get_request_run_registry(request).workspace_change_guard() as running:
        row = await db.get(Workspace, workspace_id)
        if row is None:
            raise HTTPException(404, "Workspace not found")
        sessions = await _workspace_sessions(db, workspace_id)
        if sessions and not detach_sessions:
            raise HTTPException(
                409,
                "Confirm detaching the linked sessions before removing this workspace.",
            )
        if any(session.running for session in await _removal_sessions(db, sessions, running)):
            raise HTTPException(
                409,
                "Wait for all agents in the linked sessions to finish before removing this workspace.",
            )
        await db.commit()
        # Keep the registration and bindings on disk if container cleanup fails.
        try:
            await containers.stop(row.id, delete=True)
        except containers.WorkspaceContainerError as exc:
            raise HTTPException(502, str(exc)) from exc
        for session in sessions:
            await get_browser_pool().remove(
                str(session.id), instance_name=instance_name, stop_remote=False
            )
            await invalidate_runtime_for_session(instance_name, session.id, stop_remote=False)
        for session in sessions:
            session.workspace_id = None
        await db.flush()
        await db.delete(row)
        await db.commit()
    manager = getattr(request.app.state, "ws_manager", None)
    if manager:
        for session in sessions:
            await manager.broadcast(
                str(session.id),
                {
                    "type": "workspace_changed",
                    "session_id": str(session.id),
                    "workspace_id": None,
                },
            )


@router.get("/sessions/{session_id}/workspace", response_model=WorkspaceResponse | None)
async def session_workspace(session_id: UUID, db: AsyncSession = Depends(get_db)):
    session = await db.get(Session, session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    while session.parent_session_id:
        session = await db.get(Session, session.parent_session_id)
        if session is None:
            raise HTTPException(404, "Parent session not found")
    return await db.get(Workspace, session.workspace_id) if session.workspace_id else None


@router.put("/sessions/{session_id}/workspace", response_model=WorkspaceResponse | None)
async def attach_workspace(
    session_id: UUID,
    payload: WorkspaceAttachment,
    request: Request,
    db: AsyncSession = Depends(get_db),
):

    registry = get_request_run_registry(request)
    async with registry.idle_guard(str(session_id)) as idle:
        if not idle:
            raise HTTPException(409, "Wait for the agent to finish before changing its workspace")
        session = await db.get(Session, session_id)
        if session is None:
            raise HTTPException(404, "Session not found")
        if session.parent_session_id:
            raise HTTPException(409, "Sub-agents use their parent session's workspace")
        workspace = await db.get(Workspace, payload.workspace_id) if payload.workspace_id else None
        if payload.workspace_id and workspace is None:
            raise HTTPException(404, "Workspace not found")
        if session.workspace_id == payload.workspace_id:
            return workspace
        instance_name = request.path_params["instance_name"]
        await db.commit()
        if session.workspace_id:

            if await runtime_configured(instance_name=instance_name, session_id=session_id):
                terminal = await get_runtime_terminal_manager(
                    instance_name=instance_name, session_id=session_id
                )

                if any(
                    pane["busy"]
                    for window in await TmuxPanes(terminal).tree(str(session_id))
                    for pane in window["panes"]
                ):
                    raise HTTPException(
                        409,
                        "Stop or finish running terminal commands before changing the workspace",
                    )
        if session.workspace_id:
            await get_browser_pool().remove(str(session_id), instance_name=instance_name)
        await invalidate_runtime_for_session(instance_name, session_id)
        session.workspace_id = payload.workspace_id
        await db.commit()
    manager = getattr(request.app.state, "ws_manager", None)
    if manager:
        await manager.broadcast(
            str(session_id),
            {
                "type": "workspace_changed",
                "session_id": str(session_id),
                "workspace_id": (str(payload.workspace_id) if payload.workspace_id else None),
            },
        )
    return workspace
