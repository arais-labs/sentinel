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
        user_id=settings.dev_user_id,
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
) -> dict:
    bridge: TelegramBridge | None = getattr(request.app.state, "telegram_bridge", None)
    return {
        "running": bridge.is_running if bridge else False,
        "bot_username": bridge.bot_username if bridge else None,
        "connected_chats": bridge.connected_chats if bridge else {},
        "token_configured": bool(settings.telegram_bot_token),
        "masked_token": _mask(settings.telegram_bot_token),
    }


class ConfigureRequest(BaseModel):
    bot_token: str


@router.post("/configure")
async def configure(
    payload: ConfigureRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    settings.telegram_bot_token = payload.bot_token
    await _upsert(db, "telegram_bot_token", payload.bot_token)
    await _start_bridge(request.app.state)
    return {"success": True}


@router.post("/start")
async def start_bridge(
    request: Request,
    user: TokenPayload = Depends(require_auth),
) -> dict:
    if not settings.telegram_bot_token:
        return {"success": False, "error": "No bot token configured"}
    await _start_bridge(request.app.state)
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
    await _delete_setting(db, "telegram_bot_token")
    return {"success": True}
