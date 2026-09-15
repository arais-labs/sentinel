from __future__ import annotations

from fastapi import HTTPException
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import (
    get_db,
    get_request_instance_runtime_context,
    get_settings_service,
)
from app.logging_context import (
    clear_all_runtime_logger_overrides,
    clear_runtime_logger_override,
    get_logging_config_snapshot,
    set_runtime_logger_override,
)
from app.services.instance_runtime_context import instance_runtime_context_registry
from sentral.llm.ids import ProviderChoice
from app.services.settings.settings_service import SettingsService

router = APIRouter()


class SetApiKeysRequest(BaseModel):
    anthropic_api_key: str | None = None
    anthropic_oauth_token: str | None = None
    openai_api_key: str | None = None
    openai_oauth_token: str | None = None
    gemini_api_key: str | None = None
    gemini_oauth_credentials: str | None = None


async def _rebuild_current_instance_runtime_context(request: Request) -> None:
    try:
        context = get_request_instance_runtime_context(request)
    except RuntimeError:
        return
    await instance_runtime_context_registry.rebuild_context(
        app_state=request.app.state,
        context=context,
    )


@router.post("/api-keys")
async def set_api_keys(
    payload: SetApiKeysRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings_service: SettingsService = Depends(get_settings_service),
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
    await _rebuild_current_instance_runtime_context(request)
    return {"success": True}


@router.get("/api-keys/status")
async def get_api_keys_status(
    db: AsyncSession = Depends(get_db),
    settings_service: SettingsService = Depends(get_settings_service),
) -> dict:
    instance_settings = await settings_service.build_instance_settings(db)
    status = settings_service.get_api_keys_status(instance_settings)
    providers = {
        provider.value: {
            "configured": item.configured,
            "auth_method": item.auth_method,
            "auth_source": item.auth_source,
            "masked_key": item.masked_key,
        }
        for provider, item in status.providers.items()
    }
    return {
        "primary_provider": status.primary_provider.value,
        "providers": providers,
    }


@router.get("/desktop-codex-oauth/status")
async def get_desktop_codex_oauth_status(
    settings_service: SettingsService = Depends(get_settings_service),
) -> dict[str, bool]:
    status_result = settings_service.get_desktop_codex_oauth_status()
    return {
        "enabled": status_result.enabled,
        "auth_file_found": status_result.auth_file_found,
    }


@router.post("/desktop-codex-oauth/connect")
async def connect_desktop_codex_oauth(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings_service: SettingsService = Depends(get_settings_service),
) -> dict[str, str | bool]:
    result = await settings_service.connect_desktop_codex_oauth(db)
    await _rebuild_current_instance_runtime_context(request)
    return {"success": True, "masked_key": result.masked_key}


@router.post("/desktop-claude-oauth/connect")
async def connect_desktop_claude_oauth(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings_service: SettingsService = Depends(get_settings_service),
) -> dict[str, str | bool]:
    result = await settings_service.connect_desktop_claude_oauth(db)
    await _rebuild_current_instance_runtime_context(request)
    return {"success": True, "masked_key": result.masked_key}


@router.post("/desktop-gemini-oauth/connect")
async def connect_desktop_gemini_oauth(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings_service: SettingsService = Depends(get_settings_service),
) -> dict[str, str | bool]:
    result = await settings_service.connect_desktop_gemini_oauth(db)
    await _rebuild_current_instance_runtime_context(request)
    return {"success": True, "masked_key": result.masked_key}


class DeleteProviderRequest(BaseModel):
    provider: ProviderChoice


@router.delete("/api-keys")
async def delete_api_keys(
    payload: DeleteProviderRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings_service: SettingsService = Depends(get_settings_service),
) -> dict[str, bool]:
    await settings_service.delete_api_keys(db, provider=payload.provider)
    await _rebuild_current_instance_runtime_context(request)
    return {"success": True}


class SetPrimaryProviderRequest(BaseModel):
    provider: ProviderChoice


@router.post("/primary-provider")
async def set_primary_provider(
    payload: SetPrimaryProviderRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings_service: SettingsService = Depends(get_settings_service),
) -> dict[str, str | bool]:
    await settings_service.set_primary_provider(db, provider=payload.provider)
    await _rebuild_current_instance_runtime_context(request)
    return {"success": True, "primary_provider": payload.provider.value}


class SetLoggerLevelRequest(BaseModel):
    logger: str
    level: str


@router.get("/logging")
async def get_logging_config() -> dict:
    return get_logging_config_snapshot()


@router.post("/logging/levels")
async def set_logging_level(
    payload: SetLoggerLevelRequest,
) -> dict:
    try:
        return set_runtime_logger_override(payload.logger, payload.level)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/logging/levels")
async def delete_logging_level(
    logger: str,
) -> dict:
    try:
        return clear_runtime_logger_override(logger)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/logging/reset")
async def reset_logging_overrides() -> dict:
    return clear_all_runtime_logger_overrides()
