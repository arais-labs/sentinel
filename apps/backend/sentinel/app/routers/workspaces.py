from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_manager_db, get_request_run_registry
from app.models import Session, Workspace
from app.models.manager import Machine
from app.routers.workspace_browser import router as browser_router
from app.services.workspaces import catalog, lifecycle, operations
from app.services.workspaces.compatibility import RuntimeCompatibilityError
from app.services.workspaces.metrics import workspace_metrics
from app.services.workspaces.operations import (
    WorkspaceCreate,
    WorkspaceDestructiveAction,
    WorkspaceReinstallAction,
    WorkspaceRemoval,
    WorkspaceResponse,
    WorkspaceUpdate,
)

router = APIRouter()
router.include_router(browser_router)


class WorkspaceAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: UUID | None


def _actor(request: Request) -> operations.Actor:
    return operations.Actor(
        registry=get_request_run_registry(request),
        instance_name=request.path_params["instance_name"],
        ws_manager=getattr(request.app.state, "ws_manager", None),
    )


@router.get("/workspaces/{workspace_id}/metrics")
async def get_workspace_metrics(workspace_id: UUID, db: AsyncSession = Depends(get_db)):
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Workspace not found")
    await db.close()
    return await workspace_metrics.get(str(workspace_id))


@router.get("/workspaces", response_model=list[WorkspaceResponse])
async def list_workspaces(db: AsyncSession = Depends(get_db), include_status: bool = True):
    return await operations.list_workspaces(db, include_status)


@router.get("/workspaces/usage/{machine_id}")
async def get_machine_usage(
    machine_id: UUID,
    db: AsyncSession = Depends(get_db),
    manager: AsyncSession = Depends(get_manager_db),
) -> dict:
    return await operations.machine_usage(db, manager, machine_id)


@router.get("/workspaces/discover/{machine_id}", response_model=list[WorkspaceResponse])
async def discover_worker_workspaces(
    machine_id: UUID,
    db: AsyncSession = Depends(get_db),
    manager: AsyncSession = Depends(get_manager_db),
):
    machine = await manager.get(Machine, machine_id)
    if machine is None or machine.provider != "ssh":
        raise HTTPException(404, "Remote worker not found")
    await manager.close()
    try:
        async with asyncio.timeout(10):
            snapshot = await catalog.snapshot(machine_id)
        result = []
        for workspace_id, record in snapshot["workspaces"].items():
            row = await catalog.cache_record(db, machine_id, workspace_id, record)
            state = snapshot.get("states", {}).get(workspace_id, {})
            result.append(
                WorkspaceResponse.model_validate(row).model_copy(
                    update={
                        "revision": record["revision"],
                        "resources": record["spec"]["resources"],
                        "state": state.get("state", "stopped"),
                        "error": state.get("error"),
                        "recovery_available": state.get("recovery_available", False),
                        "recovery_backup": state.get("recovery_backup"),
                    }
                )
            )
        await db.commit()
        return result
    except RuntimeCompatibilityError:
        await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        raise HTTPException(503, str(exc) or "Worker connection timed out") from exc


@router.get("/workspaces/{workspace_id}/status", response_model=WorkspaceResponse)
async def get_workspace_status(workspace_id: UUID, db: AsyncSession = Depends(get_db)):
    row = await db.get(Workspace, workspace_id)
    if row is None:
        raise HTTPException(404, "Workspace not found")
    lifecycle.bind(row.id, row.machine_id, row.distribution, row.desktop, row.browser)
    await db.close()
    try:
        states = await asyncio.wait_for(lifecycle.statuses(workspace_id), timeout=8)
        state = states.get(str(workspace_id), {})
    except (TimeoutError, lifecycle.WorkspaceLifecycleError) as exc:
        state = {
            "state": "unavailable",
            "error": (
                "Connection timed out. Retrying…" if isinstance(exc, TimeoutError) else str(exc)
            ),
        }
    return WorkspaceResponse.model_validate(row).model_copy(
        update={
            "state": state.get("state", "stopped"),
            "revision": state.get("revision"),
            "error": state.get("error"),
            "message": state.get("message"),
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
    return await operations.create_workspace(_actor(request), db, manager, payload)


@router.post("/workspaces/{workspace_id}/start", response_model=WorkspaceResponse)
async def start_workspace(
    workspace_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    manager: AsyncSession = Depends(get_manager_db),
):
    return await operations.start_workspace(_actor(request), db, manager, workspace_id)


@router.post("/workspaces/{workspace_id}/stop", status_code=204)
async def stop_workspace(workspace_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    await operations.stop_workspace(_actor(request), db, workspace_id)


@router.post("/workspaces/{workspace_id}/recover", status_code=202)
async def recover_workspace(
    workspace_id: UUID,
    payload: WorkspaceDestructiveAction,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await operations.recover_workspace(_actor(request), db, workspace_id, payload)


@router.post("/workspaces/{workspace_id}/reinstall", response_model=WorkspaceResponse)
async def reinstall_workspace(
    workspace_id: UUID,
    payload: WorkspaceReinstallAction,
    request: Request,
    db: AsyncSession = Depends(get_db),
    manager: AsyncSession = Depends(get_manager_db),
):
    return await operations.reinstall_workspace(_actor(request), db, manager, workspace_id, payload)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: UUID,
    payload: WorkspaceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    manager: AsyncSession = Depends(get_manager_db),
):
    return await operations.update_workspace(_actor(request), db, manager, workspace_id, payload)


@router.get("/workspaces/{workspace_id}/removal", response_model=WorkspaceRemoval)
async def workspace_removal(
    workspace_id: UUID, request: Request, db: AsyncSession = Depends(get_db)
):
    return await operations.workspace_removal(_actor(request), db, workspace_id)


@router.delete("/workspaces/{workspace_id}", status_code=204)
async def delete_workspace(
    workspace_id: UUID,
    request: Request,
    detach_sessions: bool = False,
    db: AsyncSession = Depends(get_db),
):
    await operations.delete_workspace(
        _actor(request), db, workspace_id, detach_sessions=detach_sessions
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
    return await operations.attach_workspace(_actor(request), db, session_id, payload.workspace_id)
