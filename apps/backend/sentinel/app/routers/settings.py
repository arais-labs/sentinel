from __future__ import annotations

from fastapi import HTTPException
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import (
    get_db,
    get_runtime_rebuild_service,
    get_settings_service,
)
from app.logging_context import (
    clear_all_runtime_logger_overrides,
    clear_runtime_logger_override,
    get_logging_config_snapshot,
    set_runtime_logger_override,
)
from app.middleware.auth import TokenPayload, require_auth
from app.services.llm.ids import ProviderChoice
from app.services.runtime.runtime_rebuild import RuntimeRebuildService
from app.services.settings.settings_service import SettingsService

router = APIRouter()


class SetApiKeysRequest(BaseModel):
    anthropic_api_key: str | None = None
    anthropic_oauth_token: str | None = None
    openai_api_key: str | None = None
    openai_oauth_token: str | None = None
    gemini_api_key: str | None = None
    gemini_oauth_credentials: str | None = None


@router.post("/api-keys")
async def set_api_keys(
    payload: SetApiKeysRequest,
    request: Request,
    _: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    settings_service: SettingsService = Depends(get_settings_service),
    runtime_rebuild_service: RuntimeRebuildService = Depends(get_runtime_rebuild_service),
) -> dict[str, bool]:
    await settings_service.set_api_keys(
        db,
        anthropic_api_key=payload.anthropic_api_key,
        anthropic_oauth_token=payload.anthropic_oauth_token,
        openai_api_key=payload.openai_api_key,
        openai_oauth_token=payload.openai_oauth_token,
        gemini_api_key=payload.gemini_api_key,
        gemini_oauth_credentials=payload.gemini_oauth_credentials,
    )
    await runtime_rebuild_service.rebuild_request_runtime_support(request)
    return {"success": True}


@router.get("/api-keys/status")
async def get_api_keys_status(
    _: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    settings_service: SettingsService = Depends(get_settings_service),
) -> dict:
    instance_settings = await settings_service.build_instance_settings(db)
    status = settings_service.get_api_keys_status(instance_settings)
    providers = {
        provider.value: {
            "configured": item.configured,
            "auth_method": item.auth_method,
            "masked_key": item.masked_key,
        }
        for provider, item in status.providers.items()
    }
    return {
        "primary_provider": status.primary_provider.value,
        "providers": providers,
    }


class DeleteProviderRequest(BaseModel):
    provider: ProviderChoice


@router.delete("/api-keys")
async def delete_api_keys(
    payload: DeleteProviderRequest,
    request: Request,
    _: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    settings_service: SettingsService = Depends(get_settings_service),
    runtime_rebuild_service: RuntimeRebuildService = Depends(get_runtime_rebuild_service),
) -> dict[str, bool]:
    await settings_service.delete_api_keys(db, provider=payload.provider)
    await runtime_rebuild_service.rebuild_request_runtime_support(request)
    return {"success": True}


class SetPrimaryProviderRequest(BaseModel):
    provider: ProviderChoice


@router.post("/primary-provider")
async def set_primary_provider(
    payload: SetPrimaryProviderRequest,
    request: Request,
    _: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    settings_service: SettingsService = Depends(get_settings_service),
    runtime_rebuild_service: RuntimeRebuildService = Depends(get_runtime_rebuild_service),
) -> dict[str, str | bool]:
    await settings_service.set_primary_provider(db, provider=payload.provider)
    await runtime_rebuild_service.rebuild_request_runtime_support(request)
    return {"success": True, "primary_provider": payload.provider.value}


class SetLoggerLevelRequest(BaseModel):
    logger: str
    level: str


@router.get("/logging")
async def get_logging_config(
    _: TokenPayload = Depends(require_auth),
) -> dict:
    return get_logging_config_snapshot()


@router.post("/logging/levels")
async def set_logging_level(
    payload: SetLoggerLevelRequest,
    _: TokenPayload = Depends(require_auth),
) -> dict:
    try:
        return set_runtime_logger_override(payload.logger, payload.level)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/logging/levels")
async def delete_logging_level(
    logger: str,
    _: TokenPayload = Depends(require_auth),
) -> dict:
    try:
        return clear_runtime_logger_override(logger)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/logging/reset")
async def reset_logging_overrides(
    _: TokenPayload = Depends(require_auth),
) -> dict:
    return clear_all_runtime_logger_overrides()
