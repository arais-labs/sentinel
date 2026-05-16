from collections.abc import AsyncGenerator
from typing import cast

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from app.config import Settings, settings
from app.database.database import ManagerSessionLocal, get_db_session
from app.database.instance_sessions import instance_session_registry
from app.models.manager import SentinelInstance
from app.services.instances import InvalidInstanceNameError, normalize_instance_name
from app.services.instance_runtime_context import (
    InstanceRuntimeContext,
    instance_runtime_context_registry,
)
from app.services.onboarding.onboarding_service import OnboardingService
from app.services.runtime.runtime_rebuild import RuntimeRebuildService
from app.services.settings.settings_service import SettingsService


def get_settings() -> Settings:
    """Dependency accessor to keep settings wiring centralized."""
    return settings


def get_onboarding_service() -> OnboardingService:
    return OnboardingService()


def get_settings_service() -> SettingsService:
    return SettingsService()


def get_runtime_rebuild_service() -> RuntimeRebuildService:
    return RuntimeRebuildService()


async def get_db(connection: HTTPConnection) -> AsyncGenerator[AsyncSession, None]:
    raw_instance_name = connection.path_params.get("instance_name")
    if not raw_instance_name:
        connection.state.db_session_factory = ManagerSessionLocal
        async with ManagerSessionLocal() as session:
            yield session
        return

    instance = await get_instance_record(str(raw_instance_name))
    factory = instance_session_registry.session_factory(instance.database_name)
    context = await instance_runtime_context_registry.get_or_create(
        app_state=connection.app.state,
        instance=instance,
        session_factory=factory,
    )
    connection.state.instance_name = instance.name
    connection.state.instance_database_name = instance.database_name
    connection.state.db_session_factory = factory
    connection.state.instance_runtime_context = context
    async with factory() as session:
        yield session


async def get_manager_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


async def get_instance_db(
    instance_name: str,
) -> AsyncGenerator[AsyncSession, None]:
    factory = await get_instance_session_factory(instance_name)
    async with factory() as session:
        yield session


async def get_instance_session_factory(instance_name: str) -> async_sessionmaker[AsyncSession]:
    instance = await get_instance_record(instance_name)
    return instance_session_registry.session_factory(instance.database_name)


async def get_instance_record(instance_name: str) -> SentinelInstance:
    try:
        normalized = normalize_instance_name(instance_name)
    except InvalidInstanceNameError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Instance not found: {instance_name}",
        )
    async for manager_db in get_db_session():
        result = await manager_db.execute(
            select(SentinelInstance).where(SentinelInstance.name == normalized)
        )
        instance = result.scalar_one_or_none()
        if instance is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Instance not found: {normalized}",
            )
        return instance
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Instance not found: {normalized}",
    )


def get_request_db_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return cast(
        async_sessionmaker[AsyncSession],
        getattr(request.state, "db_session_factory", ManagerSessionLocal),
    )


def get_request_instance_runtime_context(request: Request) -> InstanceRuntimeContext:
    context = getattr(request.state, "instance_runtime_context", None)
    if isinstance(context, InstanceRuntimeContext):
        return context
    raise RuntimeError("Instance runtime context is missing; route is not instance-scoped")


def get_connection_instance_runtime_context(connection: HTTPConnection) -> InstanceRuntimeContext:
    context = getattr(connection.state, "instance_runtime_context", None)
    if isinstance(context, InstanceRuntimeContext):
        return context
    raise RuntimeError("Instance runtime context is missing; route is not instance-scoped")


