from __future__ import annotations

from typing import Any

from app.config import settings
from app.database import AsyncSessionLocal
from app.services.sessions import session_bindings
from app.services.tools.executor import ToolExecutionError, ToolValidationError
from app.services.tools.registry import ToolDefinition, ToolRuntimeContext
from app.services.tools.runtime_context import optional_session_id

from .bridge import TelegramBridge
from .lifecycle import (
    _delete_setting,
    _upsert_setting,
    mask_telegram_token,
    persist_telegram_settings,
    resolve_latest_active_root_session_id_for_user,
    resolve_owner_user_id_from_session,
    start_telegram_bridge,
    stop_telegram_bridge,
)


def send_telegram_message_tool(app_state_ref: object) -> ToolDefinition:
    """Factory for the send_telegram_message tool. Uses lazy app_state reference."""

    async def _execute(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
        del runtime
        bridge: TelegramBridge | None = getattr(app_state_ref, "telegram_bridge", None)
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

    return ToolDefinition(
        name="send_telegram_message",
        description=(
            "Send a message to a connected Telegram chat (group or DM). "
            "If only one chat is connected, chat_id can be omitted. "
            "Use this when asked to message someone on Telegram. "
            "By default this refuses owner DM chat to keep owner flow in shared session/UI."
        ),
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["message"],
            "properties": {
                "chat_id": {
                    "type": "integer",
                    "description": "Telegram chat ID to send to. Optional if only one chat is connected.",
                },
                "message": {
                    "type": "string",
                    "description": "The text message to send.",
                },
                "allow_owner_chat": {
                    "type": "boolean",
                    "description": "Optional override to allow sending directly to owner DM chat.",
                },
            },
        },
        execute=_execute,
    )


def telegram_manage_integration_tool(app_state_ref: object) -> ToolDefinition:
    """Tool for managing Telegram integration using UI-equivalent behavior."""

    async def _status_payload(*, status_user_id: str | None = None) -> dict[str, Any]:
        bridge: TelegramBridge | None = getattr(app_state_ref, "telegram_bridge", None)
        connected = bridge.connected_chats if bridge else {}
        owner_user_id = settings.telegram_owner_user_id or settings.dev_user_id
        effective_status_user_id = status_user_id or owner_user_id
        main_session_id = await resolve_latest_active_root_session_id_for_user(
            effective_status_user_id
        )
        return {
            "running": bool(bridge and bridge.is_running),
            "bot_username": bridge.bot_username if bridge else None,
            "can_read_all_group_messages": bridge.can_read_all_group_messages if bridge else None,
            "connected_chat_count": len(connected),
            "connected_chats": connected,
            "token_configured": bool(settings.telegram_bot_token),
            "masked_token": mask_telegram_token(settings.telegram_bot_token),
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
            await db.refresh(session)
            return str(session.id)

    async def _resolve_actor_user_id(
        runtime: ToolRuntimeContext, *, required: bool
    ) -> str | None:
        session_id = optional_session_id(runtime)
        if session_id is None:
            if required:
                raise ToolValidationError(
                    "This action requires an active session context"
                )
            return None
        resolved = await resolve_owner_user_id_from_session(str(session_id))
        if not resolved:
            raise ToolValidationError(f"session_id references unknown session: {session_id}")
        return resolved

    async def _execute(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
        action_raw = payload.get("action", "status")
        action = str(action_raw).strip().lower()
        mutating_actions = {
            "configure",
            "start",
            "stop",
            "delete_config",
            "disable",
            "bind_owner",
            "clear_owner",
        }
        actor_user_id = await _resolve_actor_user_id(
            runtime,
            required=action in mutating_actions,
        )

        if action == "status":
            return {
                "success": True,
                "action": action,
                **(await _status_payload(status_user_id=actor_user_id)),
            }

        if action == "stop":
            await stop_telegram_bridge(app_state_ref)
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
            await persist_telegram_settings(
                bot_token=settings.telegram_bot_token,
                owner_user_id=settings.telegram_owner_user_id or settings.dev_user_id,
                owner_chat_id=settings.telegram_owner_chat_id,
                owner_telegram_user_id=settings.telegram_owner_telegram_user_id,
            )
            started = await start_telegram_bridge(app_state_ref)
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
            started = await start_telegram_bridge(app_state_ref)
            if not started:
                raise ToolExecutionError("Failed to start Telegram bridge")
            return {
                "success": True,
                "action": action,
                "main_session_id": main_session_id,
                **(await _status_payload(status_user_id=actor_user_id)),
            }

        if action in {"delete_config", "disable"}:
            await stop_telegram_bridge(app_state_ref)
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

            bridge: TelegramBridge | None = getattr(app_state_ref, "telegram_bridge", None)
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

    return ToolDefinition(
        name="telegram_manage_integration",
        description=(
            "Manage Telegram integration for Sentinel. "
            "Actions: status, configure, start, stop, delete_config, bind_owner, clear_owner. "
            "Action disable remains as a backward-compatible alias of delete_config."
        ),
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "status",
                        "configure",
                        "start",
                        "stop",
                        "delete_config",
                        "bind_owner",
                        "clear_owner",
                        "disable",
                    ],
                    "description": "Integration action to run.",
                },
                "bot_token": {
                    "type": "string",
                    "description": "Telegram bot token. Required for action=configure.",
                },
                "chat_id": {
                    "type": "integer",
                    "description": "Required for action=bind_owner. Must be a connected private Telegram chat_id.",
                },
                "telegram_user_id": {
                    "type": "string",
                    "description": "Optional Telegram user id override for action=bind_owner.",
                },
            },
        },
        execute=_execute,
    )


__all__ = [
    "send_telegram_message_tool",
    "telegram_manage_integration_tool",
]
