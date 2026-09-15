from __future__ import annotations

from app.config import settings
from app.models import Message


def normalize_context_budget(value: int | None = None) -> int:
    raw = int(value) if isinstance(value, int) else int(settings.context_token_budget)
    return max(1, raw)


def reported_input_tokens(snapshot: object) -> int | None:
    if not isinstance(snapshot, dict):
        return None
    usage = snapshot.get("usage")
    count = usage.get("input_tokens") if isinstance(usage, dict) else None
    return count if type(count) is int and count >= 0 else None


def latest_request_input_tokens(messages: list[Message]) -> int | None:
    for message in reversed(messages):
        if message.role == "assistant":
            return reported_input_tokens((message.metadata_json or {}).get("provider_usage"))
    return None
