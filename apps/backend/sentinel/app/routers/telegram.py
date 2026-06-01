from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_request_instance_runtime_context
from app.middleware.auth import TokenPayload, require_auth
from app.services.instance_runtime_context import instance_runtime_context_registry
from app.services.settings.system_settings import delete_system_setting, upsert_system_setting
from app.services.telegram.lifecycle import mask_telegram_token

if TYPE_CHECKING:
    from app.services.telegram.bridge import TelegramBridge

router = APIRouter()


# ── helpers ──────────────────────────────────────────────────────────────────


async def _rebuild(request: Request) -> None:
    """Rebuild the acting instance's runtime context so its Telegram bridge
    picks up the freshly persisted settings."""
    try:
        context = get_request_instance_runtime_context(request)
    except RuntimeError:
        return
    await instance_runtime_context_registry.rebuild_context(
        app_state=request.app.state,
        context=context,
    )


def _assert_token_unique(request: Request, token: str) -> None:
    """Reject a bot token already bound to another instance's runtime context."""
    try:
        current = get_request_instance_runtime_context(request)
    except RuntimeError:
        current = None
    exclude_name = current.name if current is not None else None
    if instance_runtime_context_registry.telegram_token_in_use_by_other(
        token, exclude_name=exclude_name
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Telegram bot token already used by another instance",
        )


# ── endpoints ────────────────────────────────────────────────────────────────


@router.get("/status")
async def get_status(
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    context = get_request_instance_runtime_context(request)
    instance_settings = context.instance_settings
    bridge: TelegramBridge | None = context.telegram_bridge
    return {
        "running": bridge.is_running if bridge else False,
        "bot_username": bridge.bot_username if bridge else None,
        "can_read_all_group_messages": bridge.can_read_all_group_messages if bridge else None,
        "connected_chats": bridge.connected_chats if bridge else {},
        "token_configured": bool(instance_settings.telegram_bot_token),
        "masked_token": mask_telegram_token(instance_settings.telegram_bot_token),
        "owner_user_id": instance_settings.telegram_owner_user_id or instance_settings.dev_user_id,
        "owner_chat_id": instance_settings.telegram_owner_chat_id,
        "owner_telegram_user_id": instance_settings.telegram_owner_telegram_user_id,
    }


class ConfigureRequest(BaseModel):
    bot_token: str


class OwnerBindingRequest(BaseModel):
    chat_id: int
    telegram_user_id: str | None = None


@router.post("/configure")
async def configure(
    payload: ConfigureRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    bot_token = payload.bot_token.strip()
    _assert_token_unique(request, bot_token)
    instance_settings = get_request_instance_runtime_context(request).instance_settings
    owner_changed = bool(
        instance_settings.telegram_owner_user_id
        and instance_settings.telegram_owner_user_id != user.sub
    )
    await upsert_system_setting(db, key="telegram_bot_token", value=bot_token)
    await upsert_system_setting(db, key="telegram_owner_user_id", value=user.sub)
    if owner_changed:
        await delete_system_setting(db, key="telegram_owner_chat_id")
        await delete_system_setting(db, key="telegram_owner_telegram_user_id")
    await _rebuild(request)
    return {"success": True}


@router.post("/start")
async def start_bridge(
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    instance_settings = get_request_instance_runtime_context(request).instance_settings
    if not instance_settings.telegram_bot_token:
        return {"success": False, "error": "No bot token configured"}
    owner_changed = bool(
        instance_settings.telegram_owner_user_id
        and instance_settings.telegram_owner_user_id != user.sub
    )
    await upsert_system_setting(db, key="telegram_owner_user_id", value=user.sub)
    if owner_changed:
        await delete_system_setting(db, key="telegram_owner_chat_id")
        await delete_system_setting(db, key="telegram_owner_telegram_user_id")
    await _rebuild(request)
    return {"success": True}


@router.post("/owner")
async def bind_owner_telegram_identity(
    payload: OwnerBindingRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    bridge: TelegramBridge | None = get_request_instance_runtime_context(request).telegram_bridge
    connected = bridge.connected_chats if bridge else {}
    chat_info = connected.get(payload.chat_id)
    if chat_info is None:
        return {
            "success": False,
            "error": "Chat not connected. Send /start from owner DM first.",
        }
    if str(chat_info.get("chat_type", "")).lower() != "private":
        return {"success": False, "error": "Owner binding requires a private DM chat"}

    inferred_tg_user_id = chat_info.get("user_id")
    owner_tg_user_id = payload.telegram_user_id or (
        str(inferred_tg_user_id) if inferred_tg_user_id is not None else None
    )
    owner_chat_id = str(payload.chat_id)

    await upsert_system_setting(db, key="telegram_owner_user_id", value=user.sub)
    await upsert_system_setting(db, key="telegram_owner_chat_id", value=owner_chat_id)
    if owner_tg_user_id:
        await upsert_system_setting(
            db, key="telegram_owner_telegram_user_id", value=owner_tg_user_id
        )
    else:
        await delete_system_setting(db, key="telegram_owner_telegram_user_id")
    await _rebuild(request)

    return {
        "success": True,
        "owner_chat_id": owner_chat_id,
        "owner_telegram_user_id": owner_tg_user_id,
    }


@router.delete("/owner")
async def clear_owner_telegram_identity(
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await delete_system_setting(db, key="telegram_owner_chat_id")
    await delete_system_setting(db, key="telegram_owner_telegram_user_id")
    await _rebuild(request)
    return {"success": True}


@router.post("/stop")
async def stop_bridge(
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await delete_system_setting(db, key="telegram_bot_token")
    await _rebuild(request)
    return {"success": True}


@router.delete("/configure")
async def delete_config(
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await delete_system_setting(db, key="telegram_bot_token")
    await delete_system_setting(db, key="telegram_owner_user_id")
    await delete_system_setting(db, key="telegram_owner_chat_id")
    await delete_system_setting(db, key="telegram_owner_telegram_user_id")
    await _rebuild(request)
    return {"success": True}
