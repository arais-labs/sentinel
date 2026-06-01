"""Native module: telegram — send messages and manage Telegram integration."""

from __future__ import annotations

import logging
from typing import Any

from app.services.instance_runtime_context import (
    InstanceRuntimeContext,
    instance_runtime_context_registry,
)
from app.services.tools.executor import ToolExecutionError, ToolValidationError
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.runtime_context import optional_session_id

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers (imported lazily from telegram package to avoid circular deps)
# ---------------------------------------------------------------------------


def _resolve_instance_context(runtime: ToolRuntimeContext) -> InstanceRuntimeContext:
    """Resolve the acting instance's runtime context, or fail loudly."""
    instance_name = getattr(runtime, "instance_name", None)
    context = instance_runtime_context_registry.get(instance_name) if instance_name else None
    if context is None:
        raise ToolValidationError("No active instance runtime for Telegram")
    return context


async def _rebuild_instance_context(context: InstanceRuntimeContext) -> InstanceRuntimeContext:
    """Rebuild the instance context (picks up new telegram settings / bridge)."""
    from app.services.araios.runtime_services import get_app_state

    return await instance_runtime_context_registry.rebuild_context(
        app_state=get_app_state(),
        context=context,
    )


async def _resolve_owner_user_id_from_session(
    context: InstanceRuntimeContext, session_id: str | None
) -> str | None:
    """Resolve owner user_id from an explicit Sentinel session id."""
    from app.services.telegram import resolve_owner_user_id_from_session

    return await resolve_owner_user_id_from_session(context.session_factory, session_id)


async def _persist_telegram_settings(context: InstanceRuntimeContext, **kwargs: Any) -> None:
    from app.services.telegram import persist_telegram_settings

    await persist_telegram_settings(context.session_factory, **kwargs)


def _mask_telegram_token(value: str | None) -> str | None:
    from app.services.telegram import mask_telegram_token

    return mask_telegram_token(value)


async def _upsert_setting(context: InstanceRuntimeContext, key: str, value: str) -> None:
    from app.services.settings.system_settings import upsert_system_setting

    async with context.session_factory() as db:
        await upsert_system_setting(db, key=key, value=value)


async def _delete_setting(context: InstanceRuntimeContext, key: str) -> None:
    from app.services.settings.system_settings import delete_system_setting

    async with context.session_factory() as db:
        await delete_system_setting(db, key=key)


# ---------------------------------------------------------------------------
# Handler functions (module-level)
# ---------------------------------------------------------------------------


async def handle_send(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    context = _resolve_instance_context(runtime)
    bridge = context.telegram_bridge
    if bridge is None or not bridge.is_running:
        raise ToolExecutionError("Telegram bridge is not running")

    chat_id = payload.get("chat_id")
    message = payload.get("message")
    allow_owner_chat = bool(payload.get("allow_owner_chat", False))
    owner_chat_id_raw = context.instance_settings.telegram_owner_chat_id
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


async def _status_payload(context: InstanceRuntimeContext) -> dict[str, Any]:
    bridge = context.telegram_bridge
    instance_settings = context.instance_settings
    connected = bridge.connected_chats if bridge else {}
    owner_user_id = instance_settings.telegram_owner_user_id or instance_settings.dev_user_id
    return {
        "running": bool(bridge and bridge.is_running),
        "bot_username": bridge.bot_username if bridge else None,
        "can_read_all_group_messages": bridge.can_read_all_group_messages if bridge else None,
        "connected_chat_count": len(connected),
        "connected_chats": connected,
        "token_configured": bool(instance_settings.telegram_bot_token),
        "masked_token": _mask_telegram_token(instance_settings.telegram_bot_token),
        "owner_user_id": owner_user_id,
        "owner_chat_id": instance_settings.telegram_owner_chat_id,
        "owner_telegram_user_id": instance_settings.telegram_owner_telegram_user_id,
    }


async def _resolve_actor_user_id(
    context: InstanceRuntimeContext,
    runtime: ToolRuntimeContext,
    *,
    required: bool,
) -> str | None:
    session_id = optional_session_id(runtime)
    if session_id is None:
        if required:
            raise ToolValidationError("This action requires an active session context")
        return None
    resolved = await _resolve_owner_user_id_from_session(context, str(session_id))
    if not resolved:
        raise ToolValidationError(f"session_id references unknown session: {session_id}")
    return resolved


def _assert_token_unique(context: InstanceRuntimeContext, token: str) -> None:
    if instance_runtime_context_registry.telegram_token_in_use_by_other(
        token, exclude_name=context.name
    ):
        raise ToolValidationError("Telegram bot token already used by another instance")


async def _handle_manage_action(
    payload: dict[str, Any],
    runtime: ToolRuntimeContext,
    *,
    action: str,
) -> dict[str, Any]:
    context = _resolve_instance_context(runtime)
    mutating_actions = {
        "configure",
        "start",
        "stop",
        "delete_config",
        "bind_owner",
        "clear_owner",
    }
    actor_user_id = await _resolve_actor_user_id(
        context,
        runtime,
        required=action in mutating_actions,
    )

    if action == "status":
        return {
            "success": True,
            "action": action,
            **(await _status_payload(context)),
        }

    if action == "stop":
        await _delete_setting(context, "telegram_bot_token")
        context = await _rebuild_instance_context(context)
        return {
            "success": True,
            "action": action,
            **(await _status_payload(context)),
        }

    if action == "configure":
        bot_token = payload.get("bot_token")
        if not isinstance(bot_token, str) or not bot_token.strip():
            raise ToolValidationError(
                "Field 'bot_token' must be a non-empty string for action=configure"
            )
        if actor_user_id is None:
            raise ToolValidationError("Could not resolve actor from session_id")
        token = bot_token.strip()
        _assert_token_unique(context, token)

        instance_settings = context.instance_settings
        owner_changed = bool(
            instance_settings.telegram_owner_user_id
            and instance_settings.telegram_owner_user_id != actor_user_id
        )
        await _persist_telegram_settings(
            context,
            bot_token=token,
            owner_user_id=actor_user_id,
            owner_chat_id=None if owner_changed else instance_settings.telegram_owner_chat_id,
            owner_telegram_user_id=(
                None if owner_changed else instance_settings.telegram_owner_telegram_user_id
            ),
        )
        context = await _rebuild_instance_context(context)
        return {
            "success": True,
            "action": action,
            **(await _status_payload(context)),
        }

    if action == "start":
        if not context.instance_settings.telegram_bot_token:
            raise ToolExecutionError("No Telegram bot token configured")
        if actor_user_id is None:
            raise ToolValidationError("Could not resolve actor from session_id")
        instance_settings = context.instance_settings
        owner_changed = bool(
            instance_settings.telegram_owner_user_id
            and instance_settings.telegram_owner_user_id != actor_user_id
        )
        await _upsert_setting(context, "telegram_owner_user_id", actor_user_id)
        if owner_changed:
            await _delete_setting(context, "telegram_owner_chat_id")
            await _delete_setting(context, "telegram_owner_telegram_user_id")
        context = await _rebuild_instance_context(context)
        return {
            "success": True,
            "action": action,
            **(await _status_payload(context)),
        }

    if action == "delete_config":
        await _delete_setting(context, "telegram_bot_token")
        await _delete_setting(context, "telegram_owner_user_id")
        await _delete_setting(context, "telegram_owner_chat_id")
        await _delete_setting(context, "telegram_owner_telegram_user_id")
        context = await _rebuild_instance_context(context)
        return {
            "success": True,
            "action": action,
            **(await _status_payload(context)),
        }

    if action == "bind_owner":
        if actor_user_id is None:
            raise ToolValidationError("Could not resolve actor from session_id")
        chat_id = payload.get("chat_id")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            raise ToolValidationError("Field 'chat_id' must be an integer for action=bind_owner")

        bridge = context.telegram_bridge
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
            else (str(inferred_tg_user_id) if inferred_tg_user_id is not None else None)
        )

        owner_chat_id = str(chat_id)
        await _upsert_setting(context, "telegram_owner_user_id", actor_user_id)
        await _upsert_setting(context, "telegram_owner_chat_id", owner_chat_id)
        if owner_tg_user_id:
            await _upsert_setting(context, "telegram_owner_telegram_user_id", owner_tg_user_id)
        else:
            await _delete_setting(context, "telegram_owner_telegram_user_id")
        context = await _rebuild_instance_context(context)

        return {
            "success": True,
            "action": action,
            "owner_chat_id": owner_chat_id,
            "owner_telegram_user_id": owner_tg_user_id,
            **(await _status_payload(context)),
        }

    if action == "clear_owner":
        await _delete_setting(context, "telegram_owner_chat_id")
        await _delete_setting(context, "telegram_owner_telegram_user_id")
        context = await _rebuild_instance_context(context)
        return {
            "success": True,
            "action": action,
            **(await _status_payload(context)),
        }

    raise ToolValidationError(f"Unsupported action: {action}")


async def handle_status(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    return await _handle_manage_action(payload, runtime, action="status")


async def handle_configure(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    return await _handle_manage_action(payload, runtime, action="configure")


async def handle_start(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    return await _handle_manage_action(payload, runtime, action="start")


async def handle_stop(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    return await _handle_manage_action(payload, runtime, action="stop")


async def handle_delete_config(
    payload: dict[str, Any], runtime: ToolRuntimeContext
) -> dict[str, Any]:
    return await _handle_manage_action(payload, runtime, action="delete_config")


async def handle_bind_owner(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    return await _handle_manage_action(payload, runtime, action="bind_owner")


async def handle_clear_owner(
    payload: dict[str, Any], runtime: ToolRuntimeContext
) -> dict[str, Any]:
    return await _handle_manage_action(payload, runtime, action="clear_owner")
