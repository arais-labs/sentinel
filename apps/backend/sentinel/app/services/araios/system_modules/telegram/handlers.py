"""Native module: telegram — send messages and manage Telegram integration."""

from __future__ import annotations

import contextlib
import logging
from uuid import UUID

from app.config import settings
from app.database import AsyncSessionLocal
from app.services.araios.runtime_services import get_app_state
from app.services import session_bindings
from app.services.tools.executor import ToolExecutionError, ToolValidationError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers (imported lazily from telegram_bridge to avoid circular deps)
# ---------------------------------------------------------------------------

def _get_bridge():
    """Return the TelegramBridge instance from app_state, or None."""
    app_state = get_app_state()
    if app_state is None:
        return None
    return getattr(app_state, "telegram_bridge", None)


async def _resolve_owner_user_id_from_session(session_id: str | None) -> str | None:
    """Resolve owner user_id from an explicit Sentinel session id."""
    from app.services.telegram_bridge import resolve_owner_user_id_from_session
    return await resolve_owner_user_id_from_session(session_id)


async def _resolve_latest_active_root_session_id_for_user(user_id: str) -> str | None:
    from app.services.telegram_bridge import resolve_latest_active_root_session_id_for_user
    return await resolve_latest_active_root_session_id_for_user(user_id)


async def _stop_telegram_bridge() -> None:
    from app.services.telegram_bridge import stop_telegram_bridge
    await stop_telegram_bridge(get_app_state())


async def _start_telegram_bridge() -> bool:
    from app.services.telegram_bridge import start_telegram_bridge
    return await start_telegram_bridge(get_app_state())


async def _persist_telegram_settings(**kwargs: Any) -> None:
    from app.services.telegram_bridge import persist_telegram_settings
    await persist_telegram_settings(**kwargs)


def _mask_telegram_token(value: str | None) -> str | None:
    from app.services.telegram_bridge import mask_telegram_token
    return mask_telegram_token(value)


async def _upsert_setting(key: str, value: str) -> None:
    from app.services.system_settings import upsert_system_setting
    async with AsyncSessionLocal() as db:
        await upsert_system_setting(db, key=key, value=value)


async def _delete_setting(key: str) -> None:
    from app.services.system_settings import delete_system_setting
    async with AsyncSessionLocal() as db:
        await delete_system_setting(db, key=key)


# ---------------------------------------------------------------------------
# Handler functions (module-level)
# ---------------------------------------------------------------------------

async def handle_send(payload: dict[str, Any]) -> dict[str, Any]:
    bridge = _get_bridge()
    if bridge is None or not bridge.is_running:
        raise ToolExecutionError("Telegram bridge is not running")

    chat_id = payload.get("chat_id")
    message = payload.get("message")
    allow_owner_chat = bool(payload.get("allow_owner_chat", False))
    owner_chat_id_raw = settings.telegram_owner_chat_id
    owner_chat_id: int | None = None
    if isinstance(owner_chat_id_raw, str) and owner_chat_id_raw.strip():
        try:
            owner_chat_id = int(owner_chat_id_raw.strip())
        except ValueError:
            owner_chat_id = None

    if not isinstance(message, str) or not message.strip():
        raise ToolValidationError("Field 'message' must be a non-empty string")

    # If no chat_id, try to find a connected chat
    connected = bridge.connected_chats
    if chat_id is None:
        if len(connected) == 1:
            chat_id = next(iter(connected.keys()))
        elif len(connected) == 0:
            raise ToolExecutionError(
                "No Telegram chats connected. A user must send /start to the bot first."
            )
        else:
            chat_list = [
                f"  - {info.get('title', 'Unknown')} (chat_id: {cid}, type: {info.get('chat_type', '?')})"
                for cid, info in connected.items()
            ]
            raise ToolValidationError(
                "Multiple chats connected. Specify chat_id.\n" + "\n".join(chat_list)
            )

    if not isinstance(chat_id, int):
        try:
            chat_id = int(chat_id)
        except (ValueError, TypeError):
            raise ToolValidationError(f"Invalid chat_id: {chat_id}") from None

    if owner_chat_id is not None and chat_id == owner_chat_id and not allow_owner_chat:
        raise ToolExecutionError(
            "Refusing to send to owner Telegram DM by tool. "
            "Owner messages should flow through the shared session/UI bridge."
        )

    ok = await bridge.send_message(chat_id, message.strip())
    if ok:
        return {"success": True, "chat_id": chat_id, "message_sent": message.strip()[:200]}
    raise ToolExecutionError("Failed to send message")


async def _status_payload(*, status_user_id: str | None = None) -> dict[str, Any]:
    bridge = _get_bridge()
    connected = bridge.connected_chats if bridge else {}
    owner_user_id = settings.telegram_owner_user_id or settings.dev_user_id
    effective_status_user_id = status_user_id or owner_user_id
    main_session_id = await _resolve_latest_active_root_session_id_for_user(
        effective_status_user_id
    )
    return {
        "running": bool(bridge and bridge.is_running),
        "bot_username": bridge.bot_username if bridge else None,
        "can_read_all_group_messages": bridge.can_read_all_group_messages if bridge else None,
        "connected_chat_count": len(connected),
        "connected_chats": connected,
        "token_configured": bool(settings.telegram_bot_token),
        "masked_token": _mask_telegram_token(settings.telegram_bot_token),
        "owner_user_id": owner_user_id,
        "main_session_id": main_session_id,
        "owner_chat_id": settings.telegram_owner_chat_id,
        "owner_telegram_user_id": settings.telegram_owner_telegram_user_id,
    }


async def _ensure_owner_main_session(owner_user_id: str) -> str | None:
    async with AsyncSessionLocal() as db:
        session = await session_bindings.resolve_or_create_main_session(
            db,
            user_id=owner_user_id,
            agent_id=None,
        )
        await db.commit()
        with contextlib.suppress(Exception):
            await db.refresh(session)
        return str(session.id)


async def _resolve_actor_user_id(
    payload: dict[str, Any], *, required: bool
) -> str | None:
    session_id = payload.get("session_id")
    if session_id is None:
        if required:
            raise ToolValidationError(
                "Field 'session_id' is required for this action and must reference an active session"
            )
        return None
    if not isinstance(session_id, str) or not session_id.strip():
        raise ToolValidationError("Field 'session_id' must be a non-empty string")
    resolved = await _resolve_owner_user_id_from_session(session_id.strip())
    if not resolved:
        raise ToolValidationError(f"session_id references unknown session: {session_id}")
    return resolved


async def _handle_manage_action(payload: dict[str, Any], *, action: str) -> dict[str, Any]:
    mutating_actions = {
        "configure",
        "start",
        "stop",
        "delete_config",
        "bind_owner",
        "clear_owner",
    }
    actor_user_id = await _resolve_actor_user_id(
        payload,
        required=action in mutating_actions,
    )

    if action == "status":
        return {
            "success": True,
            "action": action,
            **(await _status_payload(status_user_id=actor_user_id)),
        }

    if action == "stop":
        await _stop_telegram_bridge()
        return {
            "success": True,
            "action": action,
            **(await _status_payload(status_user_id=actor_user_id)),
        }

    if action == "configure":
        bot_token = payload.get("bot_token")
        if not isinstance(bot_token, str) or not bot_token.strip():
            raise ToolValidationError(
                "Field 'bot_token' must be a non-empty string for action=configure"
            )
        if actor_user_id is None:
            raise ToolValidationError("Could not resolve actor from session_id")

        owner_changed = bool(
            settings.telegram_owner_user_id and settings.telegram_owner_user_id != actor_user_id
        )
        settings.telegram_bot_token = bot_token.strip()
        settings.telegram_owner_user_id = actor_user_id
        if owner_changed:
            settings.telegram_owner_chat_id = None
            settings.telegram_owner_telegram_user_id = None
        main_session_id = await _ensure_owner_main_session(actor_user_id)
        await _persist_telegram_settings(
            bot_token=settings.telegram_bot_token,
            owner_user_id=settings.telegram_owner_user_id or settings.dev_user_id,
            owner_chat_id=settings.telegram_owner_chat_id,
            owner_telegram_user_id=settings.telegram_owner_telegram_user_id,
        )
        started = await _start_telegram_bridge()
        if not started:
            raise ToolExecutionError("Failed to start Telegram bridge")
        return {
            "success": True,
            "action": action,
            "main_session_id": main_session_id,
            **(await _status_payload(status_user_id=actor_user_id)),
        }

    if action == "start":
        if not settings.telegram_bot_token:
            raise ToolExecutionError("No Telegram bot token configured")
        if actor_user_id is None:
            raise ToolValidationError("Could not resolve actor from session_id")
        owner_changed = bool(
            settings.telegram_owner_user_id and settings.telegram_owner_user_id != actor_user_id
        )
        settings.telegram_owner_user_id = actor_user_id
        await _upsert_setting("telegram_owner_user_id", actor_user_id)
        if owner_changed:
            settings.telegram_owner_chat_id = None
            settings.telegram_owner_telegram_user_id = None
            await _delete_setting("telegram_owner_chat_id")
            await _delete_setting("telegram_owner_telegram_user_id")
        main_session_id = await _ensure_owner_main_session(actor_user_id)
        started = await _start_telegram_bridge()
        if not started:
            raise ToolExecutionError("Failed to start Telegram bridge")
        return {
            "success": True,
            "action": action,
            "main_session_id": main_session_id,
            **(await _status_payload(status_user_id=actor_user_id)),
        }

    if action == "delete_config":
        await _stop_telegram_bridge()
        settings.telegram_bot_token = None
        settings.telegram_owner_user_id = None
        settings.telegram_owner_chat_id = None
        settings.telegram_owner_telegram_user_id = None
        await _delete_setting("telegram_bot_token")
        await _delete_setting("telegram_owner_user_id")
        await _delete_setting("telegram_owner_chat_id")
        await _delete_setting("telegram_owner_telegram_user_id")
        return {
            "success": True,
            "action": action,
            **(await _status_payload(status_user_id=actor_user_id)),
        }

    if action == "bind_owner":
        if actor_user_id is None:
            raise ToolValidationError("Could not resolve actor from session_id")
        chat_id = payload.get("chat_id")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            raise ToolValidationError("Field 'chat_id' must be an integer for action=bind_owner")

        bridge = _get_bridge()
        connected = bridge.connected_chats if bridge else {}
        chat_info = connected.get(chat_id)
        if chat_info is None:
            raise ToolExecutionError("Chat not connected. Send /start from owner DM first.")
        if str(chat_info.get("chat_type", "")).lower() != "private":
            raise ToolValidationError("Owner binding requires a private DM chat")

        requested_tg_user_id = payload.get("telegram_user_id")
        if requested_tg_user_id is not None and (
            not isinstance(requested_tg_user_id, str) or not requested_tg_user_id.strip()
        ):
            raise ToolValidationError("Field 'telegram_user_id' must be a non-empty string")
        inferred_tg_user_id = chat_info.get("user_id")
        owner_tg_user_id = (
            requested_tg_user_id.strip()
            if isinstance(requested_tg_user_id, str)
            else (
                str(inferred_tg_user_id)
                if inferred_tg_user_id is not None
                else None
            )
        )

        settings.telegram_owner_user_id = actor_user_id
        settings.telegram_owner_chat_id = str(chat_id)
        settings.telegram_owner_telegram_user_id = owner_tg_user_id
        await _upsert_setting("telegram_owner_user_id", actor_user_id)
        await _upsert_setting("telegram_owner_chat_id", settings.telegram_owner_chat_id)
        if owner_tg_user_id:
            await _upsert_setting("telegram_owner_telegram_user_id", owner_tg_user_id)
        else:
            await _delete_setting("telegram_owner_telegram_user_id")

        return {
            "success": True,
            "action": action,
            "owner_chat_id": settings.telegram_owner_chat_id,
            "owner_telegram_user_id": settings.telegram_owner_telegram_user_id,
            **(await _status_payload(status_user_id=actor_user_id)),
        }

    if action == "clear_owner":
        settings.telegram_owner_chat_id = None
        settings.telegram_owner_telegram_user_id = None
        await _delete_setting("telegram_owner_chat_id")
        await _delete_setting("telegram_owner_telegram_user_id")
        return {
            "success": True,
            "action": action,
            **(await _status_payload(status_user_id=actor_user_id)),
        }

    raise ToolValidationError(f"Unsupported action: {action}")


async def handle_status(payload: dict[str, Any]) -> dict[str, Any]:
    return await _handle_manage_action(payload, action="status")


async def handle_configure(payload: dict[str, Any]) -> dict[str, Any]:
    return await _handle_manage_action(payload, action="configure")


async def handle_start(payload: dict[str, Any]) -> dict[str, Any]:
    return await _handle_manage_action(payload, action="start")


async def handle_stop(payload: dict[str, Any]) -> dict[str, Any]:
    return await _handle_manage_action(payload, action="stop")


async def handle_delete_config(payload: dict[str, Any]) -> dict[str, Any]:
    return await _handle_manage_action(payload, action="delete_config")


async def handle_bind_owner(payload: dict[str, Any]) -> dict[str, Any]:
    return await _handle_manage_action(payload, action="bind_owner")


async def handle_clear_owner(payload: dict[str, Any]) -> dict[str, Any]:
    return await _handle_manage_action(payload, action="clear_owner")
