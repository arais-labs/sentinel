from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_manager_db
from app.middleware.auth import TokenPayload, require_admin
from app.schemas.instances import InstanceResponse
from app.schemas.runtime_targets import (
    InstanceRuntimeTargetUpdateRequest,
    RuntimeSSHTargetCreateRequest,
    RuntimeSSHTargetResponse,
    RuntimeSSHTargetTestRequest,
    RuntimeSSHTargetTestResponse,
    RuntimeSSHTargetUpdateRequest,
)
from app.services.runtime.ssh_runtime import close_runtime_terminal_manager, invalidate_runtime_for_instance
from app.services.runtime.targets import (
    RuntimeTargetConflict,
    RuntimeTargetError,
    RuntimeTargetNotFound,
    assign_instance_runtime_target,
    create_runtime_target,
    delete_runtime_target,
    get_runtime_target,
    list_runtime_targets,
    runtime_target_response,
    test_runtime_target,
    update_runtime_target,
)

router = APIRouter()


@router.get("/runtime-targets", response_model=list[RuntimeSSHTargetResponse])
async def list_targets(
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
) -> list[RuntimeSSHTargetResponse]:
    return [runtime_target_response(target) for target in await list_runtime_targets(db)]


@router.post("/runtime-targets", response_model=RuntimeSSHTargetResponse, status_code=status.HTTP_201_CREATED)
async def create_target(
    payload: RuntimeSSHTargetCreateRequest,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
) -> RuntimeSSHTargetResponse:
    try:
        target = await create_runtime_target(db, payload)
    except RuntimeTargetConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return runtime_target_response(target)


@router.get("/runtime-targets/{target_id}", response_model=RuntimeSSHTargetResponse)
async def get_target(
    target_id: UUID,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
) -> RuntimeSSHTargetResponse:
    try:
        return runtime_target_response(await get_runtime_target(db, target_id))
    except RuntimeTargetNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/runtime-targets/{target_id}", response_model=RuntimeSSHTargetResponse)
async def update_target(
    target_id: UUID,
    payload: RuntimeSSHTargetUpdateRequest,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
) -> RuntimeSSHTargetResponse:
    try:
        target = await update_runtime_target(db, target_id, payload)
    except RuntimeTargetNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeTargetConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await close_runtime_terminal_manager()
    return runtime_target_response(target)


@router.delete("/runtime-targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_target(
    target_id: UUID,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
) -> None:
    try:
        await delete_runtime_target(db, target_id)
    except RuntimeTargetNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await close_runtime_terminal_manager()


@router.post("/runtime-targets/test", response_model=RuntimeSSHTargetTestResponse)
async def test_target(
    payload: RuntimeSSHTargetTestRequest,
    _user: TokenPayload = Depends(require_admin),
) -> RuntimeSSHTargetTestResponse:
    try:
        result = await test_runtime_target(payload)
    except RuntimeTargetError as exc:
        return RuntimeSSHTargetTestResponse(ok=False, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        return RuntimeSSHTargetTestResponse(ok=False, detail=str(exc))
    return RuntimeSSHTargetTestResponse(
        ok=True,
        detail="Runtime target is reachable.",
        resolved_home=result.resolved_home,
        resolved_workspaces_dir=result.resolved_workspaces_dir,
    )


@router.patch("/instances/{name}/runtime-target", response_model=InstanceResponse)
async def assign_target(
    name: str,
    payload: InstanceRuntimeTargetUpdateRequest,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
) -> InstanceResponse:
    try:
        instance = await assign_instance_runtime_target(
            db,
            instance_name=name,
            target_id=payload.runtime_target_id,
        )
    except RuntimeTargetNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await invalidate_runtime_for_instance(instance.name)
    return InstanceResponse(
        name=instance.name,
        database_name=instance.database_name,
        display_name=instance.display_name,
        runtime_target_id=instance.runtime_target_id,
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )
