from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_manager_db
from app.database.instance_sessions import instance_session_registry
from app.middleware.auth import TokenPayload, require_admin
from app.models.manager import SentinelInstance
from app.schemas.instances import (
    InstanceCreateRequest,
    InstanceRenameRequest,
    InstanceResponse,
    InstanceUpdateRequest,
)
from app.services.instances import (
    InstanceAlreadyExistsError,
    InstanceError,
    InstanceNotFoundError,
    InstanceRegistryService,
    normalize_instance_name,
)
from app.services.instance_runtime_context import instance_runtime_context_registry

router = APIRouter()


def _service() -> InstanceRegistryService:
    return InstanceRegistryService()


def _response(instance: SentinelInstance) -> InstanceResponse:
    return InstanceResponse(
        name=instance.name,
        database_name=instance.database_name,
        display_name=instance.display_name,
        runtime_target_id=instance.runtime_target_id,
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )


def _raise_instance_error(error: InstanceError) -> None:
    if isinstance(error, InstanceNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, InstanceAlreadyExistsError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))


@router.get("", response_model=list[InstanceResponse])
async def list_instances(
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
    service: InstanceRegistryService = Depends(_service),
) -> list[InstanceResponse]:
    instances = await service.list_instances(db)
    return [_response(instance) for instance in instances]


@router.post("", response_model=InstanceResponse, status_code=status.HTTP_201_CREATED)
async def create_instance(
    payload: InstanceCreateRequest,
    request: Request,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
    service: InstanceRegistryService = Depends(_service),
) -> InstanceResponse:
    try:
        instance = await service.create_instance(
            db,
            name=payload.name,
            display_name=payload.display_name,
        )
    except InstanceError as error:
        _raise_instance_error(error)
    if hasattr(request.app.state, "instance_stop_event"):
        await instance_runtime_context_registry.get_or_create(
            app_state=request.app.state,
            instance=instance,
            session_factory=instance_session_registry.session_factory(instance.database_name),
        )
    return _response(instance)


@router.get("/{name}", response_model=InstanceResponse)
async def get_instance(
    name: str,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
    service: InstanceRegistryService = Depends(_service),
) -> InstanceResponse:
    try:
        instance = await service.get_instance(db, name)
    except InstanceError as error:
        _raise_instance_error(error)
    return _response(instance)


@router.patch("/{name}", response_model=InstanceResponse)
async def update_instance(
    name: str,
    payload: InstanceUpdateRequest,
    request: Request,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
    service: InstanceRegistryService = Depends(_service),
) -> InstanceResponse:
    try:
        instance = await service.update_instance(
            db,
            name,
            display_name=payload.display_name,
        )
    except InstanceError as error:
        _raise_instance_error(error)
    if hasattr(request.app.state, "instance_stop_event"):
        await instance_runtime_context_registry.rebuild(
            app_state=request.app.state,
            instance=instance,
            session_factory=instance_session_registry.session_factory(instance.database_name),
        )
    return _response(instance)


@router.post("/{name}/rename", response_model=InstanceResponse)
async def rename_instance(
    name: str,
    payload: InstanceRenameRequest,
    request: Request,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
    service: InstanceRegistryService = Depends(_service),
) -> InstanceResponse:
    try:
        old_name = normalize_instance_name(name)
        instance = await service.rename_instance(db, name, payload.name)
    except InstanceError as error:
        _raise_instance_error(error)
    if hasattr(request.app.state, "instance_stop_event"):
        await instance_runtime_context_registry.remove(old_name)
        await instance_runtime_context_registry.get_or_create(
            app_state=request.app.state,
            instance=instance,
            session_factory=instance_session_registry.session_factory(instance.database_name),
        )
    return _response(instance)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_instance(
    name: str,
    request: Request,
    _user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_manager_db),
    service: InstanceRegistryService = Depends(_service),
) -> None:
    try:
        normalized_name = normalize_instance_name(name)
        if hasattr(request.app.state, "instance_stop_event"):
            await instance_runtime_context_registry.remove(normalized_name)
        await service.delete_instance(db, normalized_name)
    except InstanceError as error:
        _raise_instance_error(error)
