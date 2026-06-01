from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_manager_db
from app.middleware.auth import TokenPayload, require_admin
from app.schemas.instances import InstanceResponse
from app.schemas.runtimes import (
    InstanceRuntimeUpdateRequest,
    RuntimeActionResponse,
    RuntimeCapabilitiesResponse,
    RuntimeCreateRequest,
    RuntimeJobResponse,
    RuntimeResponse,
    RuntimeTestRequest,
    RuntimeTestResponse,
    RuntimeUpdateRequest,
)
from app.services.runtime.providers import (
    RuntimeJobNotFound,
    RuntimeProviderError,
    runtime_provider_service,
)
from app.services.runtime.runtimes import (
    RuntimeConflict,
    RuntimeErrorBase,
    RuntimeNotFound,
    assign_instance_runtime,
    create_runtime,
    delete_runtime,
    get_runtime,
    list_runtimes,
    test_runtime,
    update_runtime,
)
from app.services.runtime.ssh_runtime import (
    close_runtime_terminal_manager,
    invalidate_runtime_for_instance,
)

router = APIRouter()


@router.get("/runtimes", response_model=list[RuntimeResponse])
async def list_runtime_rows(
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
) -> list[RuntimeResponse]:
    return [
        await runtime_provider_service.runtime_response(runtime)
        for runtime in await list_runtimes(db)
    ]


@router.post(
    "/runtimes",
    response_model=RuntimeResponse | RuntimeActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_runtime_row(
    payload: RuntimeCreateRequest,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
) -> RuntimeResponse | RuntimeActionResponse:
    try:
        if runtime_provider_service.is_managed(payload.provider):
            return await runtime_provider_service.create_managed(db, payload)
        runtime = await create_runtime(db, payload)
    except RuntimeConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RuntimeProviderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await runtime_provider_service.runtime_response(runtime)


@router.get("/runtimes/capabilities", response_model=RuntimeCapabilitiesResponse)
async def runtime_capabilities(
    _user: TokenPayload = Depends(require_admin),
) -> RuntimeCapabilitiesResponse:
    return runtime_provider_service.capabilities()


@router.get("/runtimes/jobs/{job_id}", response_model=RuntimeJobResponse)
async def runtime_job(
    job_id: UUID,
    _user: TokenPayload = Depends(require_admin),
) -> RuntimeJobResponse:
    try:
        return runtime_provider_service.get_job(job_id)
    except RuntimeJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/runtimes/{runtime_id}", response_model=RuntimeResponse)
async def get_runtime_row(
    runtime_id: UUID,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
) -> RuntimeResponse:
    try:
        return await runtime_provider_service.runtime_response(await get_runtime(db, runtime_id))
    except RuntimeNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/runtimes/{runtime_id}", response_model=RuntimeResponse)
async def update_runtime_row(
    runtime_id: UUID,
    payload: RuntimeUpdateRequest,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
) -> RuntimeResponse:
    try:
        runtime = await update_runtime(db, runtime_id, payload)
    except RuntimeNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await close_runtime_terminal_manager()
    return await runtime_provider_service.runtime_response(runtime)


@router.delete("/runtimes/{runtime_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_runtime_row(
    runtime_id: UUID,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
) -> None:
    try:
        runtime = await get_runtime(db, runtime_id)
        if runtime_provider_service.is_managed(runtime.provider):
            await runtime_provider_service.delete_managed_resources(runtime)
        await delete_runtime(db, runtime_id)
    except RuntimeNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeProviderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await close_runtime_terminal_manager()


@router.post("/runtimes/{runtime_id}/{action}", response_model=RuntimeActionResponse)
async def runtime_action(
    runtime_id: UUID,
    action: str,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
) -> RuntimeActionResponse:
    if action not in {"start", "stop", "rebuild", "delete"}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unsupported runtime action."
        )
    try:
        runtime = await get_runtime(db, runtime_id)
        if not runtime_provider_service.is_managed(runtime.provider):
            raise RuntimeProviderError("This runtime type has no managed lifecycle actions.")
        return await runtime_provider_service.action(db, runtime, action)
    except RuntimeNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeProviderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/runtimes/test", response_model=RuntimeTestResponse)
async def test_runtime_row(
    payload: RuntimeTestRequest,
    _user: TokenPayload = Depends(require_admin),
) -> RuntimeTestResponse:
    try:
        result = await test_runtime(payload)
    except RuntimeErrorBase as exc:
        return RuntimeTestResponse(ok=False, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        return RuntimeTestResponse(ok=False, detail=str(exc))
    return RuntimeTestResponse(
        ok=True,
        detail="Runtime is reachable.",
        resolved_home=result.resolved_home,
        resolved_workspaces_dir=result.resolved_workspaces_dir,
    )


@router.patch("/instances/{name}/runtime", response_model=InstanceResponse)
async def assign_runtime(
    name: str,
    payload: InstanceRuntimeUpdateRequest,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
) -> InstanceResponse:
    try:
        instance = await assign_instance_runtime(
            db,
            instance_name=name,
            runtime_id=payload.runtime_id,
        )
    except RuntimeNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await invalidate_runtime_for_instance(instance.name)
    return InstanceResponse(
        name=instance.name,
        database_name=instance.database_name,
        display_name=instance.display_name,
        runtime_id=instance.runtime_id,
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )
