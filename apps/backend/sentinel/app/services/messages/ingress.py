from __future__ import annotations

from typing import Any
from uuid import UUID


def web_ingress_metadata() -> dict[str, Any]:
    return {"source": "web"}


def trigger_ingress_metadata(
    *,
    trigger_id: UUID,
    trigger_name: str,
    trigger_type: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": "trigger",
        "trigger_id": str(trigger_id),
        "trigger_name": (trigger_name or "").strip() or "Trigger",
    }
    if isinstance(trigger_type, str) and trigger_type.strip():
        metadata["trigger_type"] = trigger_type.strip()
    return metadata


def telegram_ingress_metadata(
    *,
    chat_id: int,
    chat_type: str,
    is_owner: bool,
    chat_title: str | None = None,
    user_name: str | None = None,
    user_id: int | None = None,
    username: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": "telegram",
        "telegram_chat_id": chat_id,
        "telegram_chat_type": chat_type,
        "telegram_is_owner": is_owner,
    }
    if isinstance(chat_title, str) and chat_title.strip():
        metadata["telegram_chat_title"] = chat_title
    if isinstance(user_name, str) and user_name.strip():
        metadata["telegram_user_name"] = user_name
    if isinstance(user_id, int):
        metadata["telegram_user_id"] = user_id
    if isinstance(username, str) and username.strip():
        metadata["telegram_username"] = username
    return metadata
