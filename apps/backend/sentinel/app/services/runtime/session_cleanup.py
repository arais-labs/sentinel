"""Transactional session-cleanup outbox; never starts an offline workspace."""

from __future__ import annotations

import app.services.browser.pool as browser_pool_module

import asyncio
import logging

from sqlalchemy import select

from app.models import Session, SessionRuntimeCleanup, Workspace
import app.services.modules.runtime_services as module_services
from app.services.runtime import workspace_containers as containers
import app.services.runtime.container_transport as container_transport
import app.services.runtime.ssh_runtime as ssh_runtime
import app.services.runtime.terminal_manager as terminal_manager
from app.services.runtime.workspace import WorkspaceLocation

_logger = logging.getLogger(__name__)


async def cleanup_records(db, sessions: list[Session]) -> list[SessionRuntimeCleanup]:
    records = []
    for session in sessions:
        owner = session
        while owner.parent_session_id is not None:
            parent = await db.get(Session, owner.parent_session_id)
            if parent is None:
                break
            owner = parent
        workspace = await db.get(Workspace, owner.workspace_id) if owner.workspace_id else None
        if workspace is not None:
            records.append(
                SessionRuntimeCleanup(
                    session_id=session.id,
                    workspace_id=workspace.id,
                    directory=workspace.directory,
                    tools=list(workspace.development_tools or []),
                )
            )
    return records


async def drain_cleanup(session_factory, instance_name: str) -> None:

    async with session_factory() as db:
        records = list((await db.scalars(select(SessionRuntimeCleanup))).all())
    if not records:
        return
    states = await containers.statuses()
    for record in records:
        session_key = str(record.session_id)
        try:
            # Release backend-owned connections even when the guest is offline.
            await module_services.get_browser_pool().remove(
                session_key, instance_name=instance_name, stop_remote=False
            )
            await ssh_runtime.invalidate_runtime_for_session(
                instance_name, session_key, stop_remote=False
            )
            async with session_factory() as db:
                workspace = await db.get(Workspace, record.workspace_id)
            if workspace is not None:
                if states.get(str(record.workspace_id), {}).get("state") != "running":
                    continue
                transport = container_transport.RunningContainerTransport(
                    record.workspace_id, record.directory, record.tools
                )
                manager = terminal_manager.RuntimeTerminalManager(
                    transport,
                    workspace_location=WorkspaceLocation(
                        record.directory,
                        "/var/lib/sentinel",
                        str(record.workspace_id),
                        record.directory,
                        tuple(record.tools),
                    ),
                )
                try:

                    command = browser_pool_module._build_browser_stop_command(
                        session_key, root=manager.workspace_location
                    )
                    result = await transport.run(command, timeout=30)
                    if result.exit_status != 0:
                        raise RuntimeError("Browser cleanup failed")
                    await manager.delete_session_state(session_key)
                finally:
                    await manager.close()
            # A deleted workspace has already had its runtime storage removed.
            async with session_factory() as db:
                row = await db.get(SessionRuntimeCleanup, record.session_id)
                if row is not None:
                    await db.delete(row)
                    await db.commit()
        except Exception:
            _logger.debug("Session cleanup deferred for %s", session_key, exc_info=True)


async def run_cleanup_worker(
    session_factory, instance_name: str, stop_event: asyncio.Event
) -> None:
    while not stop_event.is_set():
        try:
            await drain_cleanup(session_factory, instance_name)
        except Exception:
            _logger.debug("Session cleanup retry deferred", exc_info=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=5)
        except TimeoutError:
            pass
