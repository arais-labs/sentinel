"""What a user can do with workspaces, one path for the app's routes and the agent's tool.

Each operation raises HTTPException with the status its route answers; the `workspaces`
tool reports the same message to the agent.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from shlex import quote
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.workspaces.guest_commands as guest_commands_module
import app.services.workspaces.machines.environment as environment_module
import app.services.workspaces.machines.folders as directories_module
import app.services.workspaces.machines.registry as machines_module
import app.services.workspaces.machines.remote.worker as remote_mac
import app.services.workspaces.machines.transport as storage_module
from app.config import settings
from app.models import Session, SubAgentTask, Workspace
from app.models.manager import Machine
from app.services.modules.runtime_services import get_browser_pool
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.workspaces import catalog, lifecycle
from app.services.workspaces.compatibility import RuntimeCompatibilityError
from app.services.workspaces.machines.folders import list_local_directories
from app.services.workspaces.machines.remote.worker import RemoteMacError
from app.services.workspaces.managers import (
    get_terminal_manager,
    invalidate_session_managers,
    workspace_attached,
)
from app.services.workspaces.terminals.panes import TmuxPanes


class WorkspaceResources(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    cpus: int = Field(default=2, ge=1, le=32)
    memory_gib: int = Field(default=2, ge=1, le=64)
    disk_gib: int = Field(default=32, ge=8, le=1024)


# Catalog ids (distributions, desktops, tools, browsers); sentineld validates them.
CatalogId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    machine_id: UUID
    directory: str = Field(min_length=1, max_length=4096)
    development_tools: list[CatalogId] = Field(default_factory=list, max_length=64)
    resources: WorkspaceResources | None = None
    distribution: CatalogId = "alpine"
    desktop: CatalogId = "none"
    browser: CatalogId = "chromium"

    @field_validator("development_tools")
    @classmethod
    def validate_tools(cls, value: list[str]) -> list[str]:
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
    revision: int | None = Field(default=None, ge=1)
    distribution: CatalogId | None = None
    desktop: CatalogId | None = None
    browser: CatalogId | None = None
    directory: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, value):
        return WorkspaceCreate.path(WorkspaceCreate.clean(value)) if value is not None else None

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    resources: WorkspaceResources | None = None
    development_tools: list[CatalogId] | None = Field(default=None, max_length=64)

    @field_validator("development_tools")
    @classmethod
    def validate_tools(cls, value):
        return WorkspaceCreate.validate_tools(value) if value is not None else None


class WorkspaceDestructiveAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmed: bool


class WorkspaceReinstallAction(WorkspaceDestructiveAction):
    settings: WorkspaceUpdate | None = None


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    revision: int | None = None
    name: str
    machine_id: UUID
    directory: str
    development_tools: list[str] = Field(default_factory=list)
    distribution: str = "alpine"
    browser: str = "chromium"
    desktop: str = "none"
    state: str = "stopped"
    error: str | None = None
    message: str | None = None
    recovery_backup: str | None = None
    recovery_available: bool = False
    resources: dict[str, int] | None = None


class WorkspaceRemovalSession(BaseModel):
    id: UUID
    title: str
    running: bool


class WorkspaceRemoval(BaseModel):
    sessions: list[WorkspaceRemovalSession]


@dataclass
class Actor:
    """Who changes workspaces: a route of the app, or an agent through its `workspaces` tool."""

    registry: AgentRunRegistry
    instance_name: str
    ws_manager: Any | None = None
    # The agent's own run. It may change its own session's workspace while it runs.
    session_id: str | None = None

    def others(self, running: set[str]) -> set[str]:
        return running - {self.session_id}

    async def broadcast(self, session_id, workspace_id) -> None:
        if self.ws_manager:
            await self.ws_manager.broadcast(
                str(session_id),
                {
                    "type": "workspace_changed",
                    "session_id": str(session_id),
                    "workspace_id": str(workspace_id) if workspace_id else None,
                },
            )

    def notification_context(self, name: str) -> dict[str, str]:
        return {"instanceName": self.instance_name, "name": name}


def runtime_ready(machine: Machine) -> bool:
    return machine.provider in {"local", "ssh"} and (
        (machine.provider == "local" and lifecycle.available())
        or (machine.provider == "ssh" and bool((machine.provider_config or {}).get("host_key")))
    )


async def workspace_sessions(db: AsyncSession, workspace_id: UUID) -> list[Session]:
    # Child conversations inherit the root's binding even with a null workspace_id.
    tree = select(Session.id).where(Session.workspace_id == workspace_id).cte(recursive=True)
    tree = tree.union(select(Session.id).join(tree, Session.parent_session_id == tree.c.id))
    return list((await db.scalars(select(Session).where(Session.id.in_(select(tree.c.id))))).all())


async def removal_sessions(db: AsyncSession, sessions: list[Session], running: set[str]):
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


async def agents_running(db, workspace_id, running: set[str]) -> tuple[list[Session], bool]:
    sessions = await workspace_sessions(db, workspace_id)
    return sessions, any(
        session.running for session in await removal_sessions(db, sessions, running)
    )


async def list_workspaces(db: AsyncSession, include_status: bool = True) -> list[WorkspaceResponse]:
    rows = list((await db.scalars(select(Workspace).order_by(Workspace.name))).all())
    for row in rows:
        lifecycle.bind(row.id, row.machine_id, row.distribution, row.desktop, row.browser)
    await db.close()
    if not include_status:
        return [
            WorkspaceResponse.model_validate(row).model_copy(update={"state": "checking"})
            for row in rows
        ]
    try:
        snapshot = await lifecycle.overview() if rows else {}
        states = snapshot.get("states", {})
        unavailable = None
    except lifecycle.WorkspaceLifecycleError as exc:
        snapshot, states, unavailable = {}, {}, str(exc)
    return [
        WorkspaceResponse.model_validate(row).model_copy(
            update={
                "state": (
                    "unavailable"
                    if unavailable
                    else states.get(str(row.id), {}).get("state", "stopped")
                ),
                "revision": states.get(str(row.id), {}).get("revision"),
                "error": unavailable or states.get(str(row.id), {}).get("error"),
                "recovery_available": not unavailable
                and states.get(str(row.id), {}).get("recovery_available", False),
                "message": states.get(str(row.id), {}).get("message"),
                "resources": states.get(str(row.id), {}).get("resources")
                or snapshot.get("resources"),
            }
        )
        for row in rows
    ]


async def create_workspace(
    actor: Actor, db: AsyncSession, manager: AsyncSession, payload: WorkspaceCreate
) -> WorkspaceResponse:
    machine = await manager.get(Machine, payload.machine_id)
    if machine is None:
        raise HTTPException(404, "Machine not found")
    if not runtime_ready(machine):
        raise HTTPException(
            422,
            "Install Sentinel Runtime for this machine before creating workspaces.",
        )
    await manager.close()
    # Omitted resources take the defaults, raised to the distribution's catalog minimum.
    resources = (
        payload.resources.model_dump()
        if payload.resources is not None
        else await lifecycle.default_resources(
            payload.distribution,
            WorkspaceResources().model_dump(),
            machine,
        )
    )
    row = Workspace(**payload.model_dump(exclude={"resources"}))
    if machine.provider == "ssh":
        row.id = uuid4()
        try:
            record = await catalog.configure(
                db,
                row,
                name=row.name,
                project=row.directory,
                tools=row.development_tools,
                distribution=row.distribution,
                desktop=row.desktop,
                browser=row.browser,
                resources=resources,
                revision=0,
            )
            await lifecycle.start(row.id, row.directory, row.development_tools)
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc
        return WorkspaceResponse.model_validate(row).model_copy(
            update={"revision": record["revision"], "state": "preparing"}
        )
    async with actor.registry.workspace_change_guard():
        if await db.scalar(
            select(Workspace.id).where(
                Workspace.machine_id == row.machine_id, Workspace.name == row.name
            )
        ):
            raise HTTPException(409, "A workspace with this name already exists")
        db.add(row)
        await db.commit()
    await db.refresh(row)
    lifecycle.bind(row.id, row.machine_id, row.distribution, row.desktop, row.browser)
    await db.close()
    try:
        await lifecycle.start(
            row.id,
            row.directory,
            row.development_tools,
            notification_context=actor.notification_context(row.name),
            resources=resources,
        )
    except lifecycle.WorkspaceLifecycleError as exc:
        return WorkspaceResponse.model_validate(row).model_copy(
            update={"state": "failed", "error": str(exc)}
        )
    return WorkspaceResponse.model_validate(row).model_copy(
        update={
            "state": "preparing",
            "resources": resources,
        }
    )


async def start_workspace(
    actor: Actor, db: AsyncSession, manager: AsyncSession, workspace_id: UUID
) -> WorkspaceResponse:
    row = await db.get(Workspace, workspace_id)
    if row is None:
        raise HTTPException(404, "Workspace not found")
    machine = await manager.get(Machine, row.machine_id)
    if machine is None or machine.provider not in {"local", "ssh"}:
        raise HTTPException(422, "Choose a local or enrolled remote Mac")
    lifecycle.bind(row.id, row.machine_id, row.distribution, row.desktop, row.browser)
    await manager.close()
    await db.close()
    try:
        await lifecycle.start(
            row.id,
            row.directory,
            row.development_tools,
            notification_context=actor.notification_context(row.name),
        )
    except lifecycle.WorkspaceLifecycleError as exc:
        raise HTTPException(503, str(exc)) from exc
    return WorkspaceResponse.model_validate(row).model_copy(update={"state": "preparing"})


async def stop_workspace(actor: Actor, db: AsyncSession, workspace_id: UUID) -> None:
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Workspace not found")
    sessions = await db.scalars(select(Session.id).where(Session.workspace_id == workspace_id))
    for session_id in sessions:
        if str(session_id) != actor.session_id and await actor.registry.is_running(str(session_id)):
            raise HTTPException(409, "Stop the active agent before stopping its workspace")
    await db.close()
    try:
        await lifecycle.stop(workspace_id)
    except lifecycle.WorkspaceLifecycleError as exc:
        raise HTTPException(502, str(exc)) from exc


async def update_workspace(
    actor: Actor,
    db: AsyncSession,
    manager: AsyncSession,
    workspace_id: UUID,
    payload: WorkspaceUpdate,
) -> WorkspaceResponse:
    async with actor.registry.workspace_change_guard() as running:
        running = actor.others(running)
        row = await db.get(Workspace, workspace_id)
        if row is None:
            raise HTTPException(404, "Workspace not found")
        lifecycle.bind(row.id, row.machine_id, row.distribution, row.desktop, row.browser)
        machine = await manager.get(Machine, row.machine_id)
        await manager.close()
        if machine is not None and machine.provider == "ssh":
            if payload.revision is None:
                raise HTTPException(409, "Refresh workspace settings before editing")
            sessions, busy = await agents_running(db, workspace_id, running)
            if busy:
                raise HTTPException(
                    409, "Wait for this workspace's agents to finish before changing its settings"
                )
            try:
                snapshot = await catalog.snapshot(row.machine_id)
                current = snapshot["workspaces"][str(row.id)]["spec"]
                record = await catalog.configure(
                    db,
                    row,
                    name=payload.name if payload.name is not None else current["name"],
                    project=(
                        payload.directory if payload.directory is not None else current["project"]
                    ),
                    tools=(
                        payload.development_tools
                        if payload.development_tools is not None
                        else current["tools"]
                    ),
                    distribution=payload.distribution or current["distribution"],
                    desktop=payload.desktop if payload.desktop is not None else current["desktop"],
                    browser=payload.browser or current.get("browser", "chromium"),
                    resources=(
                        payload.resources.model_dump()
                        if payload.resources
                        else current["resources"]
                    ),
                    revision=payload.revision,
                )
                spec = record["spec"]
                configuration_changed = any(
                    current.get(key) != spec.get(key)
                    for key in ("project", "tools", "resources", "desktop", "browser")
                )
                prepared = (
                    configuration_changed
                    and snapshot.get("states", {}).get(str(row.id), {}).get("state") == "running"
                )
                if prepared:
                    await lifecycle.start(row.id, row.directory, row.development_tools)
                if current["project"] != spec["project"]:
                    for session in sessions:
                        await invalidate_session_managers(actor.instance_name, session.id)
                        await actor.broadcast(session.id, row.id)
                await db.refresh(row)
            except Exception as exc:
                raise HTTPException(409, str(exc)) from exc
            return WorkspaceResponse.model_validate(row).model_copy(
                update={
                    "revision": record["revision"],
                    "resources": record["spec"]["resources"],
                    **({"state": "preparing"} if prepared else {}),
                }
            )
        if payload.distribution is not None and payload.distribution != row.distribution:
            raise HTTPException(
                422,
                "Choose the distribution when creating a new workspace; existing disks cannot change distribution",
            )
        name = payload.name.strip() if payload.name is not None else row.name
        if not name:
            raise HTTPException(422, "Enter a workspace name")
        if await db.scalar(
            select(Workspace.id).where(
                Workspace.machine_id == row.machine_id,
                Workspace.name == name,
                Workspace.id != row.id,
            )
        ):
            raise HTTPException(409, "A workspace with this name already exists")
        tools_changed = (
            payload.development_tools is not None
            and payload.development_tools != row.development_tools
        )
        desktop_changed = payload.desktop is not None and payload.desktop != row.desktop
        browser_changed = payload.browser is not None and payload.browser != row.browser
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
        if (
            tools_changed
            or desktop_changed
            or browser_changed
            or directory_changed
            or allocation is not None
        ):
            if tools_changed and set(row.development_tools) - set(payload.development_tools):
                raise HTTPException(
                    422, "Installed workspace tools are kept; choose additional tools"
                )
            try:
                state = (await lifecycle.statuses(row.id)).get(str(row.id), {})
            except lifecycle.WorkspaceLifecycleError as exc:
                raise HTTPException(503, str(exc)) from exc
            current_resources = state.get("resources") or WorkspaceResources().model_dump()
            resized = allocation is not None and allocation != current_resources
            if allocation and allocation["disk_gib"] < current_resources["disk_gib"]:
                raise HTTPException(422, "Workspace disks can only be increased")
            if tools_changed or desktop_changed or browser_changed or resized or directory_changed:
                sessions, busy = await agents_running(db, workspace_id, running)
                if busy:
                    raise HTTPException(
                        409,
                        "Wait for the workspace's agents to finish before changing its environment",
                    )
                if state.get("state") in {"preparing", "stopping"}:
                    raise HTTPException(
                        409,
                        "Wait for workspace setup to finish before changing settings",
                    )
        if desktop_changed:
            row.desktop = payload.desktop
        if browser_changed:
            row.browser = payload.browser
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
        if tools_changed or desktop_changed or browser_changed or resized or directory_changed:
            lifecycle.bind(row.id, row.machine_id, row.distribution, row.desktop, row.browser)
            try:
                await lifecycle.start(
                    row.id,
                    row.directory,
                    row.development_tools,
                    notification_context=actor.notification_context(row.name),
                    **({"resources": allocation} if resized else {}),
                )
            except lifecycle.WorkspaceLifecycleError as exc:
                if directory_changed:
                    row.directory = previous_directory
                    await db.commit()
                raise HTTPException(503, str(exc)) from exc
            if directory_changed:
                for session in sessions:
                    await invalidate_session_managers(actor.instance_name, session.id)
                    await actor.broadcast(session.id, row.id)
            return WorkspaceResponse.model_validate(row).model_copy(
                update={
                    "state": (
                        "stopped"
                        if (resized or directory_changed)
                        and not tools_changed
                        and not desktop_changed
                        and not browser_changed
                        and state.get("state") == "stopped"
                        else "preparing"
                    ),
                    "resources": allocation or state.get("resources"),
                }
            )
        return WorkspaceResponse.model_validate(row).model_copy(
            update={
                "state": state.get("state", "stopped"),
                "resources": state.get("resources"),
            }
        )


async def recover_workspace(
    actor: Actor, db: AsyncSession, workspace_id: UUID, payload: WorkspaceDestructiveAction
) -> dict:
    """Back up the disk and check the filesystem of a workspace needing recovery; keeps it."""
    if not payload.confirmed:
        raise HTTPException(422, "Confirm that running commands and unsaved work will be lost")

    async with actor.registry.workspace_change_guard() as running:
        row = await db.get(Workspace, workspace_id)
        if row is None:
            raise HTTPException(404, "Workspace not found")
        if (await agents_running(db, workspace_id, actor.others(running)))[1]:
            raise HTTPException(409, "Stop this workspace's active agents before recovering it")
        lifecycle.bind(row.id, row.machine_id, row.distribution, row.desktop, row.browser)
        await db.close()
        try:
            result = await lifecycle.request("recover", workspace=str(workspace_id))
        except lifecycle.WorkspaceLifecycleError as exc:
            raise HTTPException(503, str(exc)) from exc
    return result


async def reinstall_workspace(
    actor: Actor,
    db: AsyncSession,
    manager: AsyncSession,
    workspace_id: UUID,
    payload: WorkspaceReinstallAction,
) -> WorkspaceResponse:
    """Erase the workspace's Linux disk and prepare it again, optionally with new settings."""
    if not payload.confirmed:
        raise HTTPException(422, "Confirm erasing the workspace Linux disk before reinstalling")

    async with actor.registry.workspace_change_guard() as running:
        row = await db.get(Workspace, workspace_id)
        if row is None:
            raise HTTPException(404, "Workspace not found")
        lifecycle.bind(row.id, row.machine_id, row.distribution, row.desktop, row.browser)
        machine = await manager.get(Machine, row.machine_id)
        if machine is None or machine.provider not in {"local", "ssh"}:
            raise HTTPException(422, "Choose a local or enrolled remote Mac")
        settings = payload.settings
        record = None
        current = {
            "distribution": row.distribution,
            "desktop": row.desktop,
            "browser": row.browser,
            "project": row.directory,
            "tools": row.development_tools,
            "name": row.name,
        }
        if machine.provider == "ssh" and settings:
            if settings.revision is None:
                raise HTTPException(409, "Refresh workspace settings before reinstalling")
            snapshot = await catalog.snapshot(row.machine_id)
            record = snapshot["workspaces"][str(row.id)]
            if settings.revision != record["revision"]:
                raise HTTPException(409, "Workspace settings changed. Refresh before reinstalling")
            current = record["spec"]
        distribution = (
            settings.distribution if settings and settings.distribution else current["distribution"]
        )
        desktop = (
            settings.desktop if settings and settings.desktop is not None else current["desktop"]
        )
        browser = (
            settings.browser
            if settings and settings.browser
            else current.get("browser", "chromium")
        )
        directory = settings.directory if settings and settings.directory else current["project"]
        tools = (
            settings.development_tools
            if settings and settings.development_tools is not None
            else current["tools"]
        )
        name = settings.name.strip() if settings and settings.name is not None else current["name"]
        if not name:
            raise HTTPException(422, "Enter a workspace name")
        if directory != row.directory and machine.provider == "local":
            try:
                await asyncio.to_thread(list_local_directories, directory)
            except OSError as exc:
                raise HTTPException(422, "Choose an existing, accessible project folder") from exc
        if (
            machine.provider == "local"
            and name != row.name
            and await db.scalar(
                select(Workspace.id).where(
                    Workspace.machine_id == row.machine_id,
                    Workspace.name == name,
                    Workspace.id != row.id,
                )
            )
        ):
            raise HTTPException(409, "A workspace with this name already exists")
        if (await agents_running(db, workspace_id, actor.others(running)))[1]:
            raise HTTPException(
                409,
                "Wait for the workspace's agents to finish before reinstalling Linux",
            )
        await manager.close()
        await db.close()
        try:
            state = (await lifecycle.statuses(row.id)).get(str(row.id), {}).get("state")
            if state in {"preparing", "stopping"}:
                raise HTTPException(
                    409, "Wait for workspace setup to finish before reinstalling Linux"
                )
            resources = settings.resources.model_dump() if settings and settings.resources else None
            spec = None
            if machine.provider == "ssh" and settings:
                spec = {
                    **record["spec"],
                    "name": name,
                    "project": directory,
                    "tools": tools,
                    "distribution": distribution,
                    "desktop": desktop,
                    "browser": browser,
                    "resources": resources or record["spec"]["resources"],
                }
            reinstall_options = (
                {
                    "distribution": distribution,
                    "desktop": desktop,
                    "browser": browser,
                    "resources": resources,
                    "spec": spec,
                    "revision": settings.revision,
                }
                if settings
                else {}
            )
            await lifecycle.reinstall(
                row.id,
                directory,
                tools,
                notification_context=actor.notification_context(name),
                **reinstall_options,
            )
        except lifecycle.WorkspaceLifecycleError as exc:
            raise HTTPException(503, str(exc)) from exc
        row = await db.get(Workspace, workspace_id)
        if row is None:
            raise HTTPException(404, "Workspace not found")
        row.name = name
        row.directory = directory
        row.development_tools = tools
        row.distribution = distribution
        row.desktop = desktop
        row.browser = browser
        lifecycle.bind(row.id, row.machine_id, distribution, desktop, browser)
        await db.commit()
        await db.refresh(row)
    return WorkspaceResponse.model_validate(row).model_copy(
        update={
            "state": "preparing",
            "message": "Rebuilding the Linux environment…",
        }
    )


async def workspace_removal(actor: Actor, db: AsyncSession, workspace_id: UUID) -> WorkspaceRemoval:
    async with actor.registry.workspace_change_guard() as running:
        if await db.get(Workspace, workspace_id) is None:
            raise HTTPException(404, "Workspace not found")
        sessions = await workspace_sessions(db, workspace_id)
        return WorkspaceRemoval(
            sessions=await removal_sessions(db, sessions, actor.others(running))
        )


async def delete_workspace(
    actor: Actor, db: AsyncSession, workspace_id: UUID, *, detach_sessions: bool = False
) -> None:
    async with actor.registry.workspace_change_guard() as running:
        row = await db.get(Workspace, workspace_id)
        if row is None:
            raise HTTPException(404, "Workspace not found")
        sessions, busy = await agents_running(db, workspace_id, actor.others(running))
        if sessions and not detach_sessions:
            raise HTTPException(
                409,
                "Confirm detaching the linked sessions before removing this workspace.",
            )
        if busy:
            raise HTTPException(
                409,
                "Wait for all agents in the linked sessions to finish before removing this workspace.",
            )
        await db.commit()
        # Keep the registration and bindings on disk if workspace cleanup fails.
        try:
            await lifecycle.stop(row.id, delete=True)
        except lifecycle.WorkspaceLifecycleError as exc:
            raise HTTPException(502, str(exc)) from exc
        for session in sessions:
            await get_browser_pool().remove(
                str(session.id), instance_name=actor.instance_name, stop_remote=False
            )
            await invalidate_session_managers(actor.instance_name, session.id)
        for session in sessions:
            session.workspace_id = None
        await db.flush()
        await db.delete(row)
        await db.commit()
    for session in sessions:
        await actor.broadcast(session.id, None)


async def attach_workspace(
    actor: Actor, db: AsyncSession, session_id: UUID, workspace_id: UUID | None
) -> Workspace | None:
    async with actor.registry.idle_guard(str(session_id)) as idle:
        if not idle and str(session_id) != actor.session_id:
            raise HTTPException(409, "Wait for the agent to finish before changing its workspace")
        session = await db.get(Session, session_id)
        if session is None:
            raise HTTPException(404, "Session not found")
        if session.parent_session_id:
            raise HTTPException(409, "Sub-agents use their parent session's workspace")
        workspace = await db.get(Workspace, workspace_id) if workspace_id else None
        if workspace_id and workspace is None:
            raise HTTPException(404, "Workspace not found")
        if session.workspace_id == workspace_id:
            return workspace
        instance_name = actor.instance_name
        await db.commit()
        stop_remote = True
        if session.workspace_id:
            try:
                if await workspace_attached(instance_name=instance_name, session_id=session_id):
                    terminal = await get_terminal_manager(
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
            except (RuntimeCompatibilityError, RemoteMacError):
                # Detach this client without issuing commands to an incompatible or
                # unreachable worker: a Mac that is off must never pin a session to it.
                # Existing programs remain owned by their workspace.
                stop_remote = False
        if session.workspace_id:
            await get_browser_pool().remove(
                str(session_id), instance_name=instance_name, stop_remote=stop_remote
            )
        await invalidate_session_managers(instance_name, session_id)
        session.workspace_id = workspace_id
        await db.commit()
    await actor.broadcast(session_id, workspace_id)
    return workspace


async def machine_catalog(manager: AsyncSession, machine_id: UUID) -> dict:
    """What a workspace on this machine can be: sentineld's catalog, read again."""
    try:
        machine = machines_module.resolve_machine_secret(
            await machines_module.get_machine(manager, machine_id)
        )
        os_name = await environment_module.machine_os(machine)
    except machines_module.MachineNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except (machines_module.MachineErrorBase, RuntimeError, OSError, TimeoutError) as exc:
        raise HTTPException(
            502,
            "Could not detect this machine's operating system. Check its connection.",
        ) from exc

    available = os_name == "darwin" and (
        (machine.provider == "local" and lifecycle.available())
        or (machine.provider == "ssh" and bool(machine.runtime_root))
    )
    result = {}
    if available:
        # sentineld's catalog is the source of truth for what a workspace can be.
        try:
            remote = await remote_mac.get_runtime(machine.id) if machine.provider == "ssh" else None
            result = await lifecycle.catalog(remote, refresh=True)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                503, f"Could not read this machine's workspace catalog: {exc}"
            ) from exc
    return {
        "os": os_name,
        "workspaces_available": available,
        **{
            key: result.get(key, [])
            for key in ("distributions", "problems", "desktops", "tools", "stacks")
        },
    }


async def machine_request(manager: AsyncSession, machine_id: UUID, action: str, **values) -> dict:
    """One of the machine's sentineld ops that no workspace owns: help, sources, distributions."""
    return await (await _machine_ops(manager, machine_id))(action, **values)


async def machine_usage(db: AsyncSession, manager: AsyncSession, machine_id: UUID) -> dict:
    """How much of the machine's disk Sentinel uses, in bytes: each of this instance's workspaces
    on it (what deleting it frees), workspaces it does not know (other), and the shared items."""
    rows = list(
        (
            await db.scalars(
                select(Workspace).where(Workspace.machine_id == machine_id).order_by(Workspace.name)
            )
        ).all()
    )
    names = {str(row.id): row.name for row in rows}
    await db.close()
    reply = await machine_request(manager, machine_id, "usage")
    sizes = reply.get("workspaces") or {}
    workspaces = sorted(
        (
            {"id": workspace_id, "name": name, "bytes": int(sizes.get(workspace_id, 0))}
            for workspace_id, name in names.items()
        ),
        key=lambda entry: -entry["bytes"],
    )
    return {
        "workspaces": workspaces,
        "other": sum(
            int(size) for workspace_id, size in sizes.items() if workspace_id not in names
        ),
        "shared": {
            key: int((reply.get("shared") or {}).get(key, 0))
            for key in ("systems", "packages", "sources", "installs", "distributions")
        },
        "total": int(reply.get("total", 0)),
        "free": reply.get("free"),
        "capacity": reply.get("capacity"),
    }


async def _machine_ops(manager: AsyncSession, machine_id: UUID):
    machine = await manager.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(404, "Machine not found")
    if not runtime_ready(machine):
        raise HTTPException(422, "Install Sentinel Runtime for this machine first.")
    await manager.close()
    remote = await remote_mac.get_runtime(machine.id) if machine.provider == "ssh" else None

    async def request(action: str, **values) -> dict:
        try:
            return await lifecycle.runtime_request(remote, action, **values)
        except lifecycle.WorkspaceLifecycleError as exc:
            raise HTTPException(502, str(exc)) from exc

    return request


async def read_distribution(
    manager: AsyncSession, machine_id: UUID, distribution: str, paths: list[str]
) -> dict:
    """Files of a distribution by path, in one reply that fits a tool result
    (settings.tool_result_max_chars): read whole, or refused with each file's size so the
    reader asks for fewer. Never a partial file."""
    request = await _machine_ops(manager, machine_id)
    listed = {
        f["path"]: f["bytes"]
        for f in (await request("distribution_read", distribution=distribution))["files"]
    }
    if missing := [path for path in paths if path not in listed]:
        raise HTTPException(404, f"No such file in {distribution}: {', '.join(missing)}")
    limit = settings.tool_result_max_chars
    sizes = {path: listed[path] for path in paths}

    def refusal(size: int) -> HTTPException:
        return HTTPException(
            413,
            f"These files take {size} characters, over the {limit} one reply holds. Read fewer "
            f"at a time; sizes in bytes: {json.dumps(sizes)}",
        )

    if sum(sizes.values()) > limit:
        raise refusal(sum(sizes.values()))
    files = {}
    for path in paths:
        reply = await request("distribution_read", distribution=distribution, path=path)
        files[path] = {"text": reply["text"], "executable": reply["executable"]}
    result = {"distribution": distribution, "files": files}
    if (size := len(json.dumps(result))) > limit:  # escaping made it longer than its bytes
        raise refusal(size)
    return result


async def workspace_log(db: AsyncSession, workspace_id: UUID, lines: int | None = None) -> dict:
    """The end of the workspace's newest boot console and of the install that made its disks."""
    row = await db.get(Workspace, workspace_id)
    if row is None:
        raise HTTPException(404, "Workspace not found")
    lifecycle.bind(row.id, row.machine_id, row.distribution, row.desktop, row.browser)
    await db.close()
    try:
        return await lifecycle.request(
            "log", workspace=str(workspace_id), **({"lines": lines} if lines else {})
        )
    except lifecycle.WorkspaceLifecycleError as exc:
        raise HTTPException(502, str(exc)) from exc


async def browse_directories(manager: AsyncSession, machine_id: UUID, path: str = "") -> dict:
    """A folder's subfolders on a machine; the user's home without a path."""
    if path and (not path.startswith("/") or "\0" in path):
        raise HTTPException(status_code=422, detail="Choose an absolute directory path")
    try:
        machine = machines_module.resolve_machine_secret(
            await machines_module.get_machine(manager, machine_id)
        )
    except machines_module.MachineNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except machines_module.MachineErrorBase as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if machine.provider == "local":
        try:
            return await asyncio.to_thread(list_local_directories, path)
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail="Cannot open this directory. Check its path and permissions.",
            ) from exc
    transport = storage_module.machine_transport(machine)
    try:
        result = await transport.run_script(
            f"set -- {quote(path)}\n"
            + guest_commands_module.load_guest_command("common/directories.sh"),
            timeout=15,
        )
        if result.exit_status != 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot open this directory. Check its path and permissions.",
            )
        fields = result.stdout.split("\0")
        current = fields[0]
        if not current.startswith("/"):
            raise HTTPException(status_code=502, detail="Machine returned an invalid directory")
        return {
            "path": current,
            "parent": str(PurePosixPath(current).parent),
            "directories": sorted((name for name in fields[1:] if name), key=str.casefold),
        }
    except (OSError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not browse folders on this machine. Check its connection.",
        ) from exc
    finally:
        await transport.close()


async def create_directory(manager: AsyncSession, machine_id: UUID, path: str, name: str) -> dict:
    """A new, empty folder `name` in the existing folder `path` on a machine."""
    try:
        directories_module.validate_new_directory(path, name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        machine = machines_module.resolve_machine_secret(
            await machines_module.get_machine(manager, machine_id)
        )
    except machines_module.MachineNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except machines_module.MachineErrorBase as exc:
        raise HTTPException(400, str(exc)) from exc
    if machine.provider == "local":
        try:
            return await asyncio.to_thread(directories_module.create_local_directory, path, name)
        except FileExistsError as exc:
            raise HTTPException(409, "A file or folder with that name already exists.") from exc
        except OSError as exc:
            raise HTTPException(
                400,
                "Cannot create this folder. Check the name and parent folder permissions.",
            ) from exc
    transport = storage_module.machine_transport(machine)
    try:
        result = await transport.run_script(
            f"set -- {quote(path)} {quote(name)}\n"
            + guest_commands_module.load_guest_command("common/create_directory.sh"),
            timeout=15,
        )
        if result.exit_status == 17:
            raise HTTPException(409, "A file or folder with that name already exists.")
        if result.exit_status != 0:
            raise HTTPException(
                400,
                "Cannot create this folder. Check the name and parent folder permissions.",
            )
        created = result.stdout.rstrip("\n")
        if not created.startswith("/"):
            raise HTTPException(502, "Machine returned an invalid directory")
        return {
            "path": created,
            "parent": str(PurePosixPath(created).parent),
            "directories": [],
        }
    except (OSError, TimeoutError) as exc:
        raise HTTPException(
            502, "Could not create folder on this machine. Check its connection."
        ) from exc
    finally:
        await transport.close()
