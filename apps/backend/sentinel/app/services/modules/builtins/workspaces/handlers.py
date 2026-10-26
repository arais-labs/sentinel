"""The agent's side of Workspaces: each action is the operation the app's routes run."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

import app.database as database_module
import app.services.workspaces.machines.registry as machines_module
from app.config import settings
from app.models import Session, Workspace
from app.services.modules.runtime_services import get_app_state, get_ws_manager
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.runtime_context import require_runtime_session_id, require_session_id
from app.services.workspaces import lifecycle, operations, reports
from app.services.workspaces.compatibility import RuntimeCompatibilityError
from app.services.workspaces.machines.remote.worker import RemoteMacError
from sentral.errors import ToolValidationError


@asynccontextmanager
async def _context(runtime: ToolRuntimeContext):
    """The run's actor and the instance and manager databases, with the routes' errors as tool
    errors."""
    registry = getattr(get_app_state(), "agent_run_registry", None)
    if registry is None or runtime.db_session_factory is None:
        raise ToolValidationError("Workspaces are unavailable outside the Sentinel app.")
    actor = operations.Actor(
        registry=registry,
        instance_name=runtime.instance_name or "main",
        ws_manager=get_ws_manager(),
        session_id=str(require_runtime_session_id(runtime)),
    )
    try:
        async with (
            runtime.db_session_factory() as db,
            database_module.ManagerSessionLocal() as manager,
        ):
            yield actor, db, manager
    except HTTPException as exc:
        raise ToolValidationError(str(exc.detail)) from exc
    except ValidationError as exc:
        raise ToolValidationError(
            "; ".join(
                f"{'.'.join(map(str, error['loc'])) or 'payload'}: {error['msg']}"
                for error in exc.errors()
            )
        ) from exc
    except (lifecycle.WorkspaceLifecycleError, RemoteMacError, RuntimeCompatibilityError) as exc:
        raise ToolValidationError(str(exc)) from exc


def _id(payload: dict[str, Any], field: str) -> UUID:
    try:
        return UUID(str(payload[field]))
    except (KeyError, ValueError) as exc:
        raise ToolValidationError(f"{field} must be an id from workspaces list") from exc


def _workspace(response: operations.WorkspaceResponse) -> dict[str, Any]:
    return response.model_dump(mode="json", exclude={"recovery_backup"})


def _preparing(
    actor: operations.Actor, runtime: ToolRuntimeContext, response: operations.WorkspaceResponse
) -> dict[str, Any]:
    """A preparation this agent started: a [Workspace Report] tells it when it settles."""
    if response.state == "preparing":
        reports.watch(
            session_id=require_session_id(runtime),
            workspace_id=response.id,
            name=response.name,
            db_factory=runtime.db_session_factory,
            run_registry=actor.registry,
        )
    return _workspace(response)


async def handle_list(_payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    async with _context(runtime) as (_actor, db, manager):
        session = await db.get(Session, require_runtime_session_id(runtime))
        attached = session.workspace_id if session else None
        machines = await machines_module.list_machines(manager)
        workspaces = await operations.list_workspaces(db)
    return {
        "workspaces": [_workspace(row) for row in workspaces],
        "attached_workspace_id": str(attached) if attached else None,
        "machines": [
            {
                "id": str(machine.id),
                "name": machine.name,
                "provider": machine.provider,
                "status": machine.status,
                "workspaces_available": operations.runtime_ready(machine),
            }
            for machine in machines
        ],
    }


async def handle_catalog(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    async with _context(runtime) as (_actor, _db, manager):
        return await operations.machine_catalog(manager, _id(payload, "machine_id"))


async def handle_usage(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    async with _context(runtime) as (_actor, db, manager):
        return await operations.machine_usage(db, manager, _id(payload, "machine_id"))


async def handle_help(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    topic = payload.get("topic", "distributions")
    async with _context(runtime) as (_actor, _db, manager):
        return await operations.machine_request(
            manager,
            _id(payload, "machine_id"),
            "help",
            **({} if topic == "overview" else {"topic": topic}),
        )


def _distribution_op(action: str, *fields: str):
    async def handle(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
        values = {field: payload[field] for field in ("distribution", *fields) if field in payload}
        async with _context(runtime) as (_actor, _db, manager):
            return await operations.machine_request(
                manager, _id(payload, "machine_id"), action, **values
            )

    return handle


handle_distribution = _distribution_op("distribution", "files")
handle_distribution_delete = _distribution_op("distribution_delete")
_list_files = _distribution_op("distribution_read")


async def handle_distribution_read(
    payload: dict[str, Any], runtime: ToolRuntimeContext
) -> dict[str, Any]:
    if not payload.get("paths"):
        return await _list_files(payload, runtime)
    async with _context(runtime) as (_actor, _db, manager):
        return await operations.read_distribution(
            manager, _id(payload, "machine_id"), payload["distribution"], payload["paths"]
        )


async def handle_source(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    async with _context(runtime) as (_actor, _db, manager):
        browse = {k: payload[k] for k in ("match", "paths", "offset", "length") if k in payload}
        if browse:  # a reply sized to what one tool result holds
            browse["limit"] = settings.tool_result_max_chars
        return await operations.machine_request(
            manager, _id(payload, "machine_id"), "source", url=payload["url"], **browse
        )


async def handle_log(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    async with _context(runtime) as (_actor, db, _manager):
        return await operations.workspace_log(
            db, _id(payload, "workspace_id"), payload.get("lines")
        )


async def handle_create(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    async with _context(runtime) as (actor, db, manager):
        values = operations.WorkspaceCreate.model_validate(payload)
        return _preparing(
            actor, runtime, await operations.create_workspace(actor, db, manager, values)
        )


async def handle_start(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    async with _context(runtime) as (actor, db, manager):
        return _preparing(
            actor,
            runtime,
            await operations.start_workspace(actor, db, manager, _id(payload, "workspace_id")),
        )


async def handle_stop(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    workspace_id = _id(payload, "workspace_id")
    async with _context(runtime) as (actor, db, _manager):
        await operations.stop_workspace(actor, db, workspace_id)
    return {"workspace_id": str(workspace_id), "state": "stopped"}


async def handle_update(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    workspace_id = _id(payload, "workspace_id")
    async with _context(runtime) as (actor, db, manager):
        values = operations.WorkspaceUpdate.model_validate(
            {key: value for key, value in payload.items() if key != "workspace_id"}
        )
        return _preparing(
            actor,
            runtime,
            await operations.update_workspace(actor, db, manager, workspace_id, values),
        )


async def handle_reinstall(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    workspace_id = _id(payload, "workspace_id")
    settings = {key: value for key, value in payload.items() if key != "workspace_id"}
    async with _context(runtime) as (actor, db, manager):
        # The action's approval is the user's confirmation of erasing the disk.
        values = operations.WorkspaceReinstallAction(
            confirmed=True,
            settings=operations.WorkspaceUpdate.model_validate(settings) if settings else None,
        )
        return _preparing(
            actor,
            runtime,
            await operations.reinstall_workspace(actor, db, manager, workspace_id, values),
        )


async def handle_recover(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    workspace_id = _id(payload, "workspace_id")
    async with _context(runtime) as (actor, db, _manager):
        row = await db.get(Workspace, workspace_id)
        name = row.name if row else ""
        result = await operations.recover_workspace(
            actor, db, workspace_id, operations.WorkspaceDestructiveAction(confirmed=True)
        )
        reports.watch(
            session_id=require_session_id(runtime),
            workspace_id=workspace_id,
            name=name,
            db_factory=runtime.db_session_factory,
            run_registry=actor.registry,
        )
    return {"workspace_id": str(workspace_id), **result}


async def handle_attach(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    detach = payload.get("detach") is True
    if detach == ("workspace_id" in payload):
        raise ToolValidationError("Give either workspace_id (to attach) or detach: true.")
    workspace_id = None if detach else _id(payload, "workspace_id")
    async with _context(runtime) as (actor, db, _manager):
        await operations.attach_workspace(actor, db, require_session_id(runtime), workspace_id)
    return {"attached_workspace_id": str(workspace_id) if workspace_id else None}


async def handle_delete(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    workspace_id = _id(payload, "workspace_id")
    async with _context(runtime) as (actor, db, _manager):
        removal = await operations.workspace_removal(actor, db, workspace_id)
        if removal.sessions and payload.get("detach_sessions") is not True:
            raise ToolValidationError(
                "These chats use this workspace and would be detached: "
                + ", ".join(session.title for session in removal.sessions)
                + ". Ask the user, then delete with detach_sessions: true."
            )
        await operations.delete_workspace(
            actor, db, workspace_id, detach_sessions=payload.get("detach_sessions") is True
        )
    return {
        "deleted_workspace_id": str(workspace_id),
        "detached_chats": [session.title for session in removal.sessions],
    }
