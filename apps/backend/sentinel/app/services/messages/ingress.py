from __future__ import annotations

from typing import Any
from uuid import UUID

from app.services.llm.ids import TierName, parse_tier_name


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


def build_generation_metadata(
    *,
    requested_tier: TierName | str | None,
    resolved_model: str | None,
    provider: str | None,
    temperature: float | None,
    max_iterations: int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    tier = parse_tier_name(requested_tier)
    if tier is not None:
        payload["requested_tier"] = tier.value
    resolved_model_value = resolved_model.strip() if isinstance(resolved_model, str) else ""
    if resolved_model_value and parse_tier_name(resolved_model_value) is None:
        payload["resolved_model"] = resolved_model_value
    provider_value = provider.strip() if isinstance(provider, str) else ""
    if provider_value and provider_value.lower() != "tier":
        payload["provider"] = provider_value
    if isinstance(temperature, (int, float)):
        payload["temperature"] = float(temperature)
    if isinstance(max_iterations, int) and max_iterations > 0:
        payload["max_iterations"] = int(max_iterations)
    return payload


def normalize_generation_metadata(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return build_generation_metadata(
        requested_tier=raw.get("requested_tier"),
        resolved_model=raw.get("resolved_model") if isinstance(raw.get("resolved_model"), str) else None,
        provider=raw.get("provider") if isinstance(raw.get("provider"), str) else None,
        temperature=raw.get("temperature") if isinstance(raw.get("temperature"), (int, float)) else None,
        max_iterations=raw.get("max_iterations") if isinstance(raw.get("max_iterations"), int) else None,
    )


def with_generation_metadata(
    metadata: dict[str, Any] | None,
    *,
    generation: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(metadata or {})
    if generation:
        normalized["generation"] = generation
    return normalized
