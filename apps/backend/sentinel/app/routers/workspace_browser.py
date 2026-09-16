"""Project browsing and uploads, independent of conversation and terminal lifetime."""

from __future__ import annotations

import asyncio
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.requests import ClientDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Workspace
from app.services.runtime import workspace_containers as containers
from app.services.runtime.guest_commands import load_guest_python
from app.services.runtime.uploads import upload_file, get_progress, record_progress
from app.services.runtime.file_stream import file_response

router = APIRouter()
OPERATIONS = {
    "files": "list_files",
    "file": "preview_file",
    "repositories": "git_roots",
    "context": "git_context",
    "changes": "git_changed",
    "diff": "git_diff",
    "history": "git_history",
    "search": "search_files",
}


@router.get("/workspaces/{workspace_id}/browse/uploads/{transfer_id}")
async def upload_progress(
    workspace_id: UUID,
    transfer_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if await db.get(Workspace, workspace_id) is None:
        raise HTTPException(404, "Workspace not found")
    key = (
        request.path_params.get("instance_name", ""),
        str(workspace_id),
        str(transfer_id),
    )
    return {"received": get_progress(key)}


@router.post("/workspaces/{workspace_id}/browse/upload")
async def upload_workspace_file(
    workspace_id: UUID,
    request: Request,
    name: str = Query(min_length=1, max_length=4096),
    size: int = Query(ge=0, le=9007199254740991),
    path: str = "",
    kind: Literal["file", "directory"] = "file",
    transfer_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    if any(part in {"", ".", ".."} or "\0" in part or "\\" in part for part in name.split("/")):
        raise HTTPException(422, "Invalid upload filename")
    if kind == "directory" and size:
        raise HTTPException(422, "Folders cannot contain a file body")
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    containers.bind(workspace.id, workspace.machine_id, workspace.distribution)
    await db.close()
    key = (
        request.path_params.get("instance_name", ""),
        str(workspace_id),
        str(transfer_id),
    )
    if transfer_id:
        record_progress(key, 0)
    try:
        return await upload_file(
            workspace,
            destination=path,
            name=name,
            size=size,
            kind=kind,
            chunks=request.stream(),
            progress=((lambda received: record_progress(key, received)) if transfer_id else None),
        )
    except ClientDisconnect:
        raise HTTPException(499, "Upload cancelled") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (containers.WorkspaceContainerError, TimeoutError) as exc:
        raise HTTPException(503, str(exc) or "File transfer timed out. Try again.") from exc


@router.get("/workspaces/{workspace_id}/browse/content")
@router.head("/workspaces/{workspace_id}/browse/content", include_in_schema=False)
async def read_workspace_file(
    workspace_id: UUID,
    request: Request,
    path: str = Query(min_length=1),
    download: bool = False,
    db: AsyncSession = Depends(get_db),
):
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    containers.bind(workspace.id, workspace.machine_id, workspace.distribution)
    await db.close()
    try:
        return await file_response(
            workspace,
            path,
            range_header=request.headers.get("range"),
            if_range=request.headers.get("if-range"),
            head=request.method == "HEAD",
            download=download,
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc) or "Could not read file") from exc
    except (containers.WorkspaceContainerError, TimeoutError) as exc:
        raise HTTPException(503, str(exc) or "File transfer timed out") from exc


@router.get("/workspaces/{workspace_id}/browse/{operation}")
async def browse_workspace(
    workspace_id: UUID,
    operation: Literal[
        "files",
        "file",
        "repositories",
        "context",
        "changes",
        "diff",
        "history",
        "search",
    ],
    path: str = "",
    query: str = "",
    base_ref: str = "HEAD",
    staged: bool = False,
    include_generated: bool = False,
    include_worktrees: bool = True,
    limit: int = Query(default=500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
):
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    await db.close()
    return await _file_operation(
        workspace,
        OPERATIONS[operation],
        {
            "path": path,
            "query": query,
            "base_ref": base_ref,
            "staged": staged,
            "limit": limit,
            "max_bytes": 200000,
            "include_generated": include_generated,
            "include_worktrees": include_worktrees,
            "group_worktrees": operation == "repositories",
        },
    )


@router.delete("/workspaces/{workspace_id}/browse/path")
async def delete_workspace_path(
    workspace_id: UUID,
    path: str = Query(min_length=1),
    db: AsyncSession = Depends(get_db),
):
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    await db.close()
    return await _file_operation(workspace, "delete_path", {"path": path})


async def _file_operation(workspace: Workspace, operation: str, payload: dict):
    workspace_id = workspace.id
    containers.bind(workspace.id, workspace.machine_id, workspace.distribution)
    request = {
        "operation": operation,
        "workspace": workspace.directory,
        "session_id": str(workspace_id),
        "session_root": "/var/lib/sentinel",
        "payload": payload,
    }
    try:
        async with asyncio.timeout(35):
            state = (await containers.statuses()).get(str(workspace_id), {})
            if state.get("state") != "running":
                raise HTTPException(
                    409,
                    state.get("error") or "Start this workspace to browse its files.",
                )
            result = await containers.request(
                "exec",
                workspace=str(workspace_id),
                timeout=30,
                arguments=[
                    "python3",
                    "-c",
                    load_guest_python("common/files/operations.py"),
                    json.dumps(request),
                ],
            )
    except (containers.WorkspaceContainerError, TimeoutError) as exc:
        raise HTTPException(503, str(exc) or "Workspace browsing timed out. Try again.") from exc
    if result.get("exitCode", 0):
        raise HTTPException(502, result.get("stderr") or "Workspace file service failed")
    try:
        payload = json.loads(result.get("stdout", ""))
    except ValueError as exc:
        raise HTTPException(502, "Invalid workspace file response") from exc
    if not payload.get("ok"):
        raise HTTPException(
            404 if payload.get("error") == "not_found" else 422,
            payload.get("detail") or "Could not read workspace",
        )
    data = payload["data"]
    data.pop("session_id", None)
    data["workspace_id"] = str(workspace_id)
    return data
