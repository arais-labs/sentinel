from __future__ import annotations

from app.database.instance_sessions import instance_session_registry
from app.services.instances import (
    InstanceRegistryService,
    InstanceNotFoundError,
    InvalidInstanceNameError,
)

from app.services.workspaces import lifecycle

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import ManagerSessionLocal
from app.models import Session, Workspace
from app.services import instance_runtime_context
import app.services.workspaces.transport.guest as container_transport
import app.services.workspaces.desktop.manager as desktop_module
import app.services.workspaces.files.manager as files_module
from app.services.workspaces.machines.registry import (
    InstanceRuntimeNotConfigured,
    MachineErrorBase,
    MachineNotFound,
    ResolvedMachine,
    get_machine,
    resolve_machine_secret,
)
import app.services.workspaces.forwards as port_forwards
import app.services.workspaces.machines.remote.worker as remote_mac
import app.services.workspaces.terminals.manager as terminal_manager
from app.services.workspaces.paths import WorkspaceLocation


@dataclass(slots=True)
class _SessionManagers:
    machine_id: str
    updated_at_marker: str
    binding: WorkspaceLocation
    terminal: terminal_manager.TerminalManager
    files: files_module.WorkspaceFiles
    forwards: port_forwards.ForwardManager
    desktop: desktop_module.DesktopManager


_bundles: dict[str, _SessionManagers] = {}
_lock = asyncio.Lock()


def instance_name_from_session_factory(
    session_factory: async_sessionmaker | None,
) -> str | None:
    if session_factory is None:
        return None
    context = instance_runtime_context.instance_runtime_context_registry.find_by_session_factory(
        session_factory
    )
    return context.name if context is not None else None


async def workspace_attached(
    *,
    session_id: str | UUID | None = None,
    instance_name: str | None = None,
    session_factory: async_sessionmaker | None = None,
) -> bool:
    try:
        await _resolve_workspace(
            session_id=session_id,
            instance_name=instance_name,
            session_factory=session_factory,
        )
    except (InstanceRuntimeNotConfigured, MachineNotFound, MachineErrorBase):
        return False
    return True


async def get_terminal_manager(
    *,
    session_id: str | UUID | None = None,
    instance_name: str | None = None,
    session_factory: async_sessionmaker | None = None,
) -> terminal_manager.TerminalManager:
    return (
        await _get_bundle(
            session_id=session_id,
            instance_name=instance_name,
            session_factory=session_factory,
        )
    ).terminal


async def get_workspace_files(
    *,
    session_id: str | UUID | None = None,
    instance_name: str | None = None,
    session_factory: async_sessionmaker | None = None,
) -> files_module.WorkspaceFiles:
    return (
        await _get_bundle(
            session_id=session_id,
            instance_name=instance_name,
            session_factory=session_factory,
        )
    ).files


async def get_forward_manager(
    *,
    session_id: str | UUID | None = None,
    instance_name: str | None = None,
    session_factory: async_sessionmaker | None = None,
) -> port_forwards.ForwardManager:
    return (
        await _get_bundle(
            session_id=session_id,
            instance_name=instance_name,
            session_factory=session_factory,
        )
    ).forwards


async def get_desktop_manager(
    *,
    session_id: str | UUID | None = None,
    instance_name: str | None = None,
    session_factory: async_sessionmaker | None = None,
) -> desktop_module.DesktopManager:
    return (
        await _get_bundle(
            session_id=session_id,
            instance_name=instance_name,
            session_factory=session_factory,
        )
    ).desktop


async def invalidate_instance_managers(instance_name: str) -> None:
    prefix = _normalize_required_instance_name(instance_name) + ":"
    async with _lock:
        keys = [key for key in _bundles if key.startswith(prefix)]
        bundles = [_bundles.pop(key) for key in keys]
    for bundle in bundles:
        await _close_bundle(bundle)


async def invalidate_session_managers(instance_name: str, session_id: str | UUID) -> None:
    key = f"{_normalize_required_instance_name(instance_name)}:{UUID(str(session_id))}"
    async with _lock:
        bundle = _bundles.pop(key, None)
    if bundle:
        await _close_bundle(bundle)


async def close_session_managers() -> None:
    async with _lock:
        bundles = list(_bundles.values())
        _bundles.clear()
    for bundle in bundles:
        await _close_bundle(bundle)


async def _get_bundle(
    *,
    session_id: str | UUID | None,
    instance_name: str | None,
    session_factory: async_sessionmaker | None,
) -> _SessionManagers:
    runtime, binding = await _resolve_workspace(
        session_id=session_id,
        instance_name=instance_name,
        session_factory=session_factory,
    )
    key = (
        _normalize_required_instance_name(
            instance_name or instance_name_from_session_factory(session_factory)
        )
        + f":{UUID(str(session_id))}"
    )
    async with _lock:
        existing = _bundles.get(key)
        if (
            existing is not None
            and existing.machine_id == str(runtime.id)
            and existing.updated_at_marker == runtime.updated_at_marker
            and existing.binding == binding
        ):
            return existing
        if existing is not None:
            await _close_bundle(existing)
        bundle = _build_bundle(runtime, binding)
        _bundles[key] = bundle
        return bundle


async def _resolve_workspace(
    *,
    session_id: str | UUID | None,
    instance_name: str | None,
    session_factory: async_sessionmaker | None,
) -> tuple[ResolvedMachine, WorkspaceLocation]:
    resolved_name = _normalize_required_instance_name(
        instance_name or instance_name_from_session_factory(session_factory)
    )
    if session_id is None:
        raise InstanceRuntimeNotConfigured(
            "No workspace attached. Use Attach in the session toolbar to choose a workspace."
        )
    if session_factory is None:
        try:
            async with ManagerSessionLocal() as manager_db:
                instance = await InstanceRegistryService().get_instance(manager_db, resolved_name)
        except (InstanceNotFoundError, InvalidInstanceNameError) as exc:
            raise InstanceRuntimeNotConfigured(str(exc)) from exc
        session_factory = instance_session_registry.session_factory(instance.database_name)
    async with session_factory() as db:
        session = await db.get(Session, UUID(str(session_id)))
        # Sub-agents operate in their parent session's workspace and terminal context.
        while session is not None and session.parent_session_id is not None:
            session = await db.get(Session, session.parent_session_id)
        workspace = (
            await db.get(Workspace, session.workspace_id)
            if session and session.workspace_id
            else None
        )
        if workspace is None:
            raise InstanceRuntimeNotConfigured(
                "No workspace attached. Use Attach in the session toolbar to choose a workspace."
            )
    async with ManagerSessionLocal() as db:
        machine = resolve_machine_secret(await get_machine(db, workspace.machine_id))

    if machine.provider == "ssh":
        from app.services.workspaces import catalog

        snapshot = await catalog.snapshot(machine.id)
        record = snapshot.get("workspaces", {}).get(str(workspace.id))
        if record is None:
            raise InstanceRuntimeNotConfigured("This workspace no longer exists on its worker")
        spec = record["spec"]
        workspace.directory = spec["project"]
        workspace.distribution = spec["distribution"]
        workspace.desktop = spec["desktop"]
        workspace.browser = spec.get("browser", "chromium")
        workspace.development_tools = spec["tools"]
    lifecycle.bind(
        workspace.id,
        workspace.machine_id,
        workspace.distribution,
        workspace.desktop,
        workspace.browser,
    )
    entry = await lifecycle.distribution_entry(
        workspace.distribution,
        await remote_mac.get_runtime(machine.id) if machine.provider == "ssh" else None,
    )
    binding = WorkspaceLocation(
        workspace.directory,
        "/var/lib/sentinel",
        str(workspace.id),
        workspace.directory,
        tuple(workspace.development_tools or []),
        distribution=workspace.distribution,
        desktop=workspace.desktop,
        features=frozenset(entry.get("features", ())),
        guide=entry.get("guide", ""),
    )
    return machine, binding


def _build_bundle(runtime: ResolvedMachine, binding: WorkspaceLocation) -> _SessionManagers:
    transport = container_transport.GuestTransport(
        UUID(binding.workspace_id), binding.host_directory, list(binding.tools)
    )
    terminal = terminal_manager.TerminalManager(transport, workspace_location=binding)
    files = files_module.WorkspaceFiles(transport, workspace_location=binding)
    forwards = port_forwards.ForwardManager(transport)
    desktop = desktop_module.DesktopManager(terminal, workspace_location=binding)
    return _SessionManagers(
        machine_id=str(runtime.id),
        binding=binding,
        updated_at_marker=runtime.updated_at_marker,
        terminal=terminal,
        files=files,
        forwards=forwards,
        desktop=desktop,
    )


async def _close_bundle(bundle: _SessionManagers) -> None:
    await bundle.forwards.close_all()
    await bundle.terminal.close()


def _normalize_required_instance_name(instance_name: str | None) -> str:
    if not instance_name:
        raise InstanceRuntimeNotConfigured("No runtime selected for this instance.")
    return instance_name.strip().lower()
