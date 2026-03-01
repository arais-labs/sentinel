from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.models.system import SystemSetting
from app.services import session_bindings
from app.services.telegram_bridge import TelegramBridge

router = APIRouter()


# ── helpers ──────────────────────────────────────────────────────────────────


async def _upsert(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalars().first()
    if setting is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        setting.value = value
    await db.commit()


async def _delete_setting(db: AsyncSession, key: str) -> None:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalars().first()
    if setting is not None:
        await db.delete(setting)
        await db.commit()


async def _resolve_main_session_id(db: AsyncSession, user_id: str) -> str | None:
    session = await session_bindings.resolve_or_create_main_session(
        db,
        user_id=user_id,
        agent_id=None,
    )
    await db.commit()
    await db.refresh(session)
    return str(session.id)


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "****"
    return value[:4] + "..." + value[-4:]


async def _start_bridge(app_state: object) -> None:
    """Start the Telegram bridge if a token is available."""
    token = settings.telegram_bot_token
    if not token:
        return

    # Stop existing bridge
    await _stop_bridge(app_state)

    ws_manager = getattr(app_state, "ws_manager", None)
    run_registry = getattr(app_state, "agent_run_registry", None)
    agent_loop = getattr(app_state, "agent_loop", None)

    bridge = TelegramBridge(
        bot_token=token,
        user_id=settings.telegram_owner_user_id or settings.dev_user_id,
        agent_loop=agent_loop,
        run_registry=run_registry,
        ws_manager=ws_manager,
        db_factory=AsyncSessionLocal,
    )

    stop_event = asyncio.Event()
    task = asyncio.create_task(bridge.start(stop_event))

    app_state.telegram_bridge = bridge
    app_state.telegram_stop_event = stop_event
    app_state.telegram_task = task


async def _stop_bridge(app_state: object) -> None:
    """Stop the Telegram bridge if running."""
    stop_event = getattr(app_state, "telegram_stop_event", None)
    bridge = getattr(app_state, "telegram_bridge", None)
    task = getattr(app_state, "telegram_task", None)

    if stop_event is not None:
        stop_event.set()

    if bridge is not None:
        await bridge.stop()

    # Wait for the task to fully finish before allowing a new bridge
    if task is not None and not task.done():
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            pass

    app_state.telegram_bridge = None
    app_state.telegram_stop_event = None
    app_state.telegram_task = None


# ── endpoints ────────────────────────────────────────────────────────────────


@router.get("/status")
async def get_status(
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    bridge: TelegramBridge | None = getattr(request.app.state, "telegram_bridge", None)
    main_session_id = await _resolve_main_session_id(db, user.sub)
    return {
        "running": bridge.is_running if bridge else False,
        "bot_username": bridge.bot_username if bridge else None,
        "can_read_all_group_messages": bridge.can_read_all_group_messages if bridge else None,
        "connected_chats": bridge.connected_chats if bridge else {},
        "token_configured": bool(settings.telegram_bot_token),
        "masked_token": _mask(settings.telegram_bot_token),
        "owner_user_id": settings.telegram_owner_user_id or settings.dev_user_id,
        "main_session_id": main_session_id,
        "owner_chat_id": settings.telegram_owner_chat_id,
        "owner_telegram_user_id": settings.telegram_owner_telegram_user_id,
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
    owner_changed = bool(settings.telegram_owner_user_id and settings.telegram_owner_user_id != user.sub)
    settings.telegram_bot_token = payload.bot_token
    settings.telegram_owner_user_id = user.sub
    main_session_id = await _resolve_main_session_id(db, user.sub)
    await _upsert(db, "telegram_bot_token", payload.bot_token)
    await _upsert(db, "telegram_owner_user_id", user.sub)
    if owner_changed:
        settings.telegram_owner_chat_id = None
        settings.telegram_owner_telegram_user_id = None
        await _delete_setting(db, "telegram_owner_chat_id")
        await _delete_setting(db, "telegram_owner_telegram_user_id")
    await _start_bridge(request.app.state)
    return {"success": True, "main_session_id": main_session_id}


@router.post("/start")
async def start_bridge(
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not settings.telegram_bot_token:
        return {"success": False, "error": "No bot token configured"}
    owner_changed = bool(settings.telegram_owner_user_id and settings.telegram_owner_user_id != user.sub)
    settings.telegram_owner_user_id = user.sub
    main_session_id = await _resolve_main_session_id(db, user.sub)
    await _upsert(db, "telegram_owner_user_id", user.sub)
    if owner_changed:
        settings.telegram_owner_chat_id = None
        settings.telegram_owner_telegram_user_id = None
        await _delete_setting(db, "telegram_owner_chat_id")
        await _delete_setting(db, "telegram_owner_telegram_user_id")
    await _start_bridge(request.app.state)
    return {"success": True, "main_session_id": main_session_id}


@router.post("/owner")
async def bind_owner_telegram_identity(
    payload: OwnerBindingRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    bridge: TelegramBridge | None = getattr(request.app.state, "telegram_bridge", None)
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

    settings.telegram_owner_user_id = user.sub
    settings.telegram_owner_chat_id = str(payload.chat_id)
    settings.telegram_owner_telegram_user_id = owner_tg_user_id
    await _upsert(db, "telegram_owner_user_id", user.sub)
    await _upsert(db, "telegram_owner_chat_id", settings.telegram_owner_chat_id)
    if owner_tg_user_id:
        await _upsert(db, "telegram_owner_telegram_user_id", owner_tg_user_id)
    else:
        await _delete_setting(db, "telegram_owner_telegram_user_id")

    return {
        "success": True,
        "owner_chat_id": settings.telegram_owner_chat_id,
        "owner_telegram_user_id": settings.telegram_owner_telegram_user_id,
    }


@router.delete("/owner")
async def clear_owner_telegram_identity(
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    settings.telegram_owner_chat_id = None
    settings.telegram_owner_telegram_user_id = None
    await _delete_setting(db, "telegram_owner_chat_id")
    await _delete_setting(db, "telegram_owner_telegram_user_id")
    return {"success": True}


@router.post("/stop")
async def stop_bridge(
    request: Request,
    user: TokenPayload = Depends(require_auth),
) -> dict:
    await _stop_bridge(request.app.state)
    return {"success": True}


@router.delete("/configure")
async def delete_config(
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _stop_bridge(request.app.state)
    settings.telegram_bot_token = None
    settings.telegram_owner_user_id = None
    settings.telegram_owner_chat_id = None
    settings.telegram_owner_telegram_user_id = None
    await _delete_setting(db, "telegram_bot_token")
    await _delete_setting(db, "telegram_owner_user_id")
    await _delete_setting(db, "telegram_owner_chat_id")
    await _delete_setting(db, "telegram_owner_telegram_user_id")
    return {"success": True}
