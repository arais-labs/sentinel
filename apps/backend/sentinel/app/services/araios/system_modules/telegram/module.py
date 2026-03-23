from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import ALLOWED_TELEGRAM_COMMANDS, handle_run


def _telegram_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["command"],
        "properties": {
            "command": {
                "type": "string",
                "enum": list(ALLOWED_TELEGRAM_COMMANDS),
                "description": "Telegram command to run.",
            },
            "chat_id": {"type": "integer"},
            "message": {"type": "string"},
            "allow_owner_chat": {"type": "boolean"},
            "bot_token": {"type": "string"},
            "telegram_user_id": {"type": "string"},
            "session_id": {"type": "string"},
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
    actions=[
        ActionDefinition(
            id="run",
            label="Telegram",
            description="Unified Telegram entry point.",
            handler=handle_run,
            parameters_schema=_telegram_parameters_schema(),
        )
    ],
)
