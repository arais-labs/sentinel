from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import (
    handle_bind_owner,
    handle_clear_owner,
    handle_configure,
    handle_delete_config,
    handle_send,
    handle_start,
    handle_status,
    handle_stop,
)
def _chat_id_prop() -> dict:
    return {"type": "integer", "description": "Telegram chat ID."}


def _send_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["message"],
        "properties": {
            "chat_id": _chat_id_prop(),
            "message": {"type": "string", "description": "Message text to send."},
            "allow_owner_chat": {"type": "boolean", "description": "Allow sending to the owner DM chat."},
        },
    }


def _status_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
        },
    }


def _configure_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["bot_token"],
        "properties": {
            "bot_token": {"type": "string", "description": "Telegram bot token."},
        },
    }


def _session_manage_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {
        },
    }


def _bind_owner_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["chat_id"],
        "properties": {
            "chat_id": _chat_id_prop(),
            "telegram_user_id": {"type": "string", "description": "Optional Telegram user ID override."},
        },
    }


MODULE = ModuleDefinition(
    name="telegram",
    label="Telegram",
    description=(
        "Send messages to connected Telegram chats and manage the Telegram bot integration "
        "from one unified entry point."
    ),
    icon="send",
    pinned=False,
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="send",
            label="Send Telegram Message",
            description="Send a message to a connected Telegram chat.",
            handler=handle_send,
            parameters_schema=_send_parameters_schema(),
        ),
        ActionDefinition(
            id="status",
            label="Telegram Status",
            description="Read Telegram bridge status and owner binding state.",
            handler=handle_status,
            requires_runtime_context=True,
            parameters_schema=_status_parameters_schema(),
        ),
        ActionDefinition(
            id="configure",
            label="Configure Telegram",
            description="Configure the bot token and start the Telegram bridge.",
            handler=handle_configure,
            requires_runtime_context=True,
            parameters_schema=_configure_parameters_schema(),
        ),
        ActionDefinition(
            id="start",
            label="Start Telegram",
            description="Start the Telegram bridge for the acting user.",
            handler=handle_start,
            requires_runtime_context=True,
            parameters_schema=_session_manage_parameters_schema(),
        ),
        ActionDefinition(
            id="stop",
            label="Stop Telegram",
            description="Stop the Telegram bridge.",
            handler=handle_stop,
            requires_runtime_context=True,
            parameters_schema=_session_manage_parameters_schema(),
        ),
        ActionDefinition(
            id="delete_config",
            label="Delete Telegram Config",
            description="Delete Telegram bot configuration and owner binding.",
            handler=handle_delete_config,
            requires_runtime_context=True,
            parameters_schema=_session_manage_parameters_schema(),
        ),
        ActionDefinition(
            id="bind_owner",
            label="Bind Telegram Owner",
            description="Bind the owner to a connected private Telegram chat.",
            handler=handle_bind_owner,
            requires_runtime_context=True,
            parameters_schema=_bind_owner_parameters_schema(),
        ),
        ActionDefinition(
            id="clear_owner",
            label="Clear Telegram Owner",
            description="Clear the current Telegram owner chat binding.",
            handler=handle_clear_owner,
            requires_runtime_context=True,
            parameters_schema=_session_manage_parameters_schema(),
        ),
    ],
)
