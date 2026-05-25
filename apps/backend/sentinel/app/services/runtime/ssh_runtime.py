from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import ManagerSessionLocal
from app.services.instance_runtime_context import instance_runtime_context_registry
from app.services.runtime.desktop import RuntimeDesktopManager
from app.services.runtime.files import RuntimeWorkspaceFiles
from app.services.runtime.port_forwards import RuntimePortForwardManager
from app.services.runtime.ssh_client import SSHClient
from app.services.runtime.runtimes import (
    InstanceRuntimeNotConfigured,
    ResolvedRuntime,
    RuntimeNotFound,
    resolve_instance_runtime,
)
from app.services.runtime.terminal_manager import RuntimeTerminalManager


@dataclass(slots=True)
class _RuntimeBundle:
    runtime_id: str
    updated_at_marker: str
    terminal: RuntimeTerminalManager
    files: RuntimeWorkspaceFiles
    forwards: RuntimePortForwardManager
    desktop: RuntimeDesktopManager


_bundles: dict[str, _RuntimeBundle] = {}
_lock = asyncio.Lock()


def instance_name_from_session_factory(session_factory: async_sessionmaker | None) -> str | None:
    if session_factory is None:
        return None
    context = instance_runtime_context_registry.find_by_session_factory(session_factory)
    return context.name if context is not None else None


async def runtime_configured(
    *,
    instance_name: str | None = None,
    session_factory: async_sessionmaker | None = None,
) -> bool:
    try:
        await _resolve_runtime(instance_name=instance_name, session_factory=session_factory)
    except (InstanceRuntimeNotConfigured, RuntimeNotFound):
        return False
    return True


async def get_runtime_terminal_manager(
    *,
    instance_name: str | None = None,
    session_factory: async_sessionmaker | None = None,
) -> RuntimeTerminalManager:
    return (
        await _get_bundle(instance_name=instance_name, session_factory=session_factory)
    ).terminal


async def get_runtime_workspace_files(
    *,
    instance_name: str | None = None,
    session_factory: async_sessionmaker | None = None,
) -> RuntimeWorkspaceFiles:
    return (await _get_bundle(instance_name=instance_name, session_factory=session_factory)).files


async def get_runtime_port_forward_manager(
    *,
    instance_name: str | None = None,
    session_factory: async_sessionmaker | None = None,
) -> RuntimePortForwardManager:
    return (await _get_bundle(instance_name=instance_name, session_factory=session_factory)).forwards


async def get_runtime_desktop_manager(
    *,
    instance_name: str | None = None,
    session_factory: async_sessionmaker | None = None,
) -> RuntimeDesktopManager:
    return (await _get_bundle(instance_name=instance_name, session_factory=session_factory)).desktop


async def invalidate_runtime_for_instance(instance_name: str) -> None:
    key = _normalize_required_instance_name(instance_name)
    async with _lock:
        bundle = _bundles.pop(key, None)
    if bundle is not None:
        await _close_bundle(bundle)


async def close_runtime_terminal_manager() -> None:
    async with _lock:
        bundles = list(_bundles.values())
        _bundles.clear()
    for bundle in bundles:
        await _close_bundle(bundle)


async def _get_bundle(
    *,
    instance_name: str | None,
    session_factory: async_sessionmaker | None,
) -> _RuntimeBundle:
    runtime = await _resolve_runtime(instance_name=instance_name, session_factory=session_factory)
    key = _normalize_required_instance_name(instance_name or instance_name_from_session_factory(session_factory))
    async with _lock:
        existing = _bundles.get(key)
        if (
            existing is not None
            and existing.runtime_id == str(runtime.id)
            and existing.updated_at_marker == runtime.updated_at_marker
        ):
            return existing
        if existing is not None:
            await _close_bundle(existing)
        bundle = _build_bundle(runtime)
        _bundles[key] = bundle
        return bundle


async def _resolve_runtime(
    *,
    instance_name: str | None,
    session_factory: async_sessionmaker | None,
) -> ResolvedRuntime:
    resolved_name = _normalize_required_instance_name(
        instance_name or instance_name_from_session_factory(session_factory)
    )
    async with ManagerSessionLocal() as db:
        return await resolve_instance_runtime(db, instance_name=resolved_name)


def _build_bundle(runtime: ResolvedRuntime) -> _RuntimeBundle:
    ssh = SSHClient(runtime.credentials())
    terminal = RuntimeTerminalManager(ssh, workspaces_root=runtime.workspaces_dir)
    files = RuntimeWorkspaceFiles(ssh, workspaces_root=runtime.workspaces_dir)
    forwards = RuntimePortForwardManager(ssh)
    desktop = RuntimeDesktopManager(terminal, workspaces_root=runtime.workspaces_dir)
    return _RuntimeBundle(
        runtime_id=str(runtime.id),
        updated_at_marker=runtime.updated_at_marker,
        terminal=terminal,
        files=files,
        forwards=forwards,
        desktop=desktop,
    )


async def _close_bundle(bundle: _RuntimeBundle) -> None:
    await bundle.desktop.close_all()
    await bundle.forwards.close_all()
    await bundle.terminal.close()


def _normalize_required_instance_name(instance_name: str | None) -> str:
    if not instance_name:
        raise InstanceRuntimeNotConfigured("No runtime selected for this instance.")
    return instance_name.strip().lower()
