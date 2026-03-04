from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import (
    get_db,
    get_runtime_rebuild_service,
    get_settings_service,
)
from app.middleware.auth import TokenPayload, require_auth
from app.services.llm.ids import ProviderChoice
from app.services.runtime_rebuild import RuntimeRebuildService
from app.services.settings_service import SettingsService

router = APIRouter()


class SetAraiOSIntegrationRequest(BaseModel):
    enabled: bool = True
    araios_frontend_url: str | None = None
    araios_backend_url: str | None = None
    agent_api_key: str | None = None


@router.get("/araios")
async def get_araios_integration(
    _: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    settings_service: SettingsService = Depends(get_settings_service),
) -> dict[str, str | bool | None]:
    status = await settings_service.get_araios_integration(db)
    return {
        "configured": status.configured,
        "araios_frontend_url": status.araios_frontend_url,
        "araios_backend_url": status.araios_backend_url,
        "masked_agent_api_key": status.masked_agent_api_key,
    }


@router.post("/araios")
async def set_araios_integration(
    payload: SetAraiOSIntegrationRequest,
    _: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    settings_service: SettingsService = Depends(get_settings_service),
) -> dict[str, str | bool | None]:
    status = await settings_service.set_araios_integration(
        db,
        enabled=payload.enabled,
        araios_frontend_url=payload.araios_frontend_url,
        araios_backend_url=payload.araios_backend_url,
        agent_api_key=payload.agent_api_key,
    )
    return {
        "success": True,
        "configured": status.configured,
        "araios_frontend_url": status.araios_frontend_url,
        "araios_backend_url": status.araios_backend_url,
        "masked_agent_api_key": status.masked_agent_api_key,
    }


class SetApiKeysRequest(BaseModel):
    anthropic_api_key: str | None = None
    anthropic_oauth_token: str | None = None
    openai_api_key: str | None = None
    openai_oauth_token: str | None = None
    gemini_api_key: str | None = None


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
    )
    runtime_rebuild_service.rebuild_agent_loop(request.app.state)
    return {"success": True}


@router.get("/api-keys/status")
async def get_api_keys_status(
    _: TokenPayload = Depends(require_auth),
    settings_service: SettingsService = Depends(get_settings_service),
) -> dict:
    status = settings_service.get_api_keys_status()
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
    runtime_rebuild_service.rebuild_agent_loop(request.app.state)
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
    runtime_rebuild_service.rebuild_agent_loop(request.app.state)
    return {"success": True, "primary_provider": payload.provider.value}
