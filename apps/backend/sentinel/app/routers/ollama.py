from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_settings_service, get_request_instance_runtime_context
from app.routers.settings import _rebuild_current_instance_runtime_context
from app.services.settings.settings_service import SettingsService
from app.services.llm.ollama_models import validate_model, endpoint_error
from sentral.llm.providers.ollama import OllamaProvider, normalize_endpoint

router = APIRouter()


class EndpointRequest(BaseModel):
    base_url: str = Field(max_length=2048)
    api_key: str | None = Field(default=None, max_length=4096)
    _url = field_validator("base_url")(normalize_endpoint)


class SaveEndpoint(EndpointRequest):
    model: str = Field(min_length=1, max_length=200)
    _model = field_validator("model")(validate_model)


async def endpoint_provider(payload, db, service):
    current = await service.build_instance_settings(db)
    # A saved credential is never forwarded to a new endpoint implicitly.
    key = (
        payload.api_key
        if payload.api_key is not None
        else (current.ollama_api_key if payload.base_url == current.ollama_base_url else None)
    )
    return OllamaProvider(payload.base_url, key), key


@router.get("")
async def get_config(
    db: AsyncSession = Depends(get_db), service: SettingsService = Depends(get_settings_service)
):
    config = await service.build_instance_settings(db)
    return {
        "base_url": config.ollama_base_url or "http://127.0.0.1:11434",
        "model": config.ollama_model or "",
        "configured": bool(config.ollama_base_url and config.ollama_model),
        "has_api_key": bool(config.ollama_api_key),
    }


@router.post("/discover")
async def discover(
    payload: EndpointRequest,
    db: AsyncSession = Depends(get_db),
    service: SettingsService = Depends(get_settings_service),
):
    provider, _ = await endpoint_provider(payload, db, service)
    try:
        models = await provider.discover_models()
        return {
            "models": [
                {"name": item["name"], "size": item.get("size", 0)}
                for item in models
                if item.get("name")
            ]
        }
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(502, endpoint_error(exc)) from exc


@router.post("")
async def save(
    payload: SaveEndpoint,
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: SettingsService = Depends(get_settings_service),
):
    provider, key = await endpoint_provider(payload, db, service)
    try:
        info = await provider.model_info(payload.model)
        if "tools" not in info.get("capabilities", []):
            raise HTTPException(
                422, "Choose an Ollama model that supports tools for Sentinel's agent loop."
            )
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, endpoint_error(exc)) from exc
    await service.set_ollama(db, base_url=payload.base_url, model=payload.model, api_key=key)
    await _rebuild_current_instance_runtime_context(request)
    return {"success": True, "has_api_key": bool(key)}


@router.post("/pull/status")
async def pull_status(
    payload: EndpointRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    context = get_request_instance_runtime_context(request)
    return request.app.state.ollama_pulls.status(context.database_name, payload.base_url)


@router.post("/pull")
async def pull(
    payload: SaveEndpoint,
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: SettingsService = Depends(get_settings_service),
):
    context = get_request_instance_runtime_context(request)
    provider, _ = await endpoint_provider(payload, db, service)
    try:
        return await request.app.state.ollama_pulls.start(
            context.database_name, provider, payload.model
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/models")
async def remove_model(
    payload: SaveEndpoint,
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: SettingsService = Depends(get_settings_service),
):
    context = get_request_instance_runtime_context(request)
    provider, _ = await endpoint_provider(payload, db, service)
    progress = request.app.state.ollama_pulls.status(context.database_name, payload.base_url)
    if progress["phase"] == "running":
        raise HTTPException(
            409, "Wait for the download to finish before removing models from this server."
        )
    try:
        await provider.delete_model(payload.model)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, endpoint_error(exc, "remove")) from exc
    current = await service.build_instance_settings(db)
    cleared_selection = (
        current.ollama_base_url == payload.base_url and current.ollama_model == payload.model
    )
    if cleared_selection:
        # Keep server/authentication settings so another model can be selected immediately.
        await service.set_ollama(
            db, base_url=current.ollama_base_url, model="", api_key=current.ollama_api_key
        )
        await _rebuild_current_instance_runtime_context(request)
    return {"success": True, "cleared_selection": cleared_selection}
