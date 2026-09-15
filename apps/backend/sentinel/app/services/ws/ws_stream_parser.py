from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any

from app.config import (
    CHAT_DEFAULT_ITERATIONS,
    CHAT_MAX_ITERATIONS,
)
from app.services.agent.agent_modes import (
    AgentMode,
    get_default_agent_mode,
    parse_agent_mode,
)
from app.services.llm.session_selection import LEVELS
from sentral.llm.ids import ProviderId, TierName, parse_tier_name

_ALLOWED_IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
}
_MAX_MESSAGE_ATTACHMENTS = 4
_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ParsedWsMessage:
    content: str
    tier: TierName | None
    max_iterations: int
    attachments: list[dict[str, Any]]
    agent_mode: AgentMode
    form_response: dict[str, Any] | None = None
    provider_id: str | None = None
    reasoning_level: str | None = None
    fast_mode: bool = False


def parse_ws_message(payload: str) -> ParsedWsMessage | None:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None
    if parsed.get("type") != "message":
        return None
    form_response = parsed.get("form_response")
    if form_response is not None and not isinstance(form_response, dict):
        return None
    content = parsed.get("content")
    if not isinstance(content, str):
        return None
    trimmed = content.strip()
    attachments = _normalize_attachments(parsed.get("attachments"))
    if attachments is None:
        return None
    if not trimmed and not attachments:
        return None

    provider_id = parsed.get("provider_id")
    reasoning_level = parsed.get("reasoning_level")
    fast_mode = parsed.get("fast_mode", False)
    if not isinstance(fast_mode, bool):
        return None
    if provider_id is not None and provider_id not in list(ProviderId):
        return None
    if reasoning_level is not None and reasoning_level not in LEVELS:
        return None
    tier = parse_tier_name(parsed.get("tier"))
    raw_iters = parsed.get("max_iterations")
    max_iterations = (
        int(raw_iters)
        if isinstance(raw_iters, int) and 0 <= raw_iters <= CHAT_MAX_ITERATIONS
        else CHAT_DEFAULT_ITERATIONS
    )
    raw_agent_mode = parsed.get("agent_mode")
    parsed_agent_mode = parse_agent_mode(raw_agent_mode)
    if raw_agent_mode is not None and parsed_agent_mode is None:
        return None
    agent_mode = parsed_agent_mode or get_default_agent_mode()
    return ParsedWsMessage(
        content=trimmed,
        tier=tier,
        max_iterations=max_iterations,
        attachments=attachments,
        agent_mode=agent_mode,
        form_response=form_response,
        provider_id=provider_id,
        reasoning_level=reasoning_level,
        fast_mode=fast_mode,
    )


def _normalize_attachments(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    if len(value) > _MAX_MESSAGE_ATTACHMENTS:
        return None

    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        mime_type = str(item.get("mime_type") or "").strip().lower()
        if mime_type not in _ALLOWED_IMAGE_MIME_TYPES:
            return None
        raw_base64 = item.get("base64")
        if not isinstance(raw_base64, str):
            return None
        base64_data = raw_base64.strip()
        if ";base64," in base64_data:
            _, _, base64_data = base64_data.partition(";base64,")
        if not base64_data:
            return None
        try:
            decoded = base64.b64decode(base64_data, validate=True)
        except (binascii.Error, ValueError):
            return None
        if len(decoded) > _MAX_ATTACHMENT_BYTES:
            return None
        filename_raw = item.get("filename")
        filename = filename_raw.strip() if isinstance(filename_raw, str) else None
        normalized.append(
            {
                "mime_type": mime_type,
                "base64": base64_data,
                "filename": filename[:200] if filename else None,
                "size_bytes": len(decoded),
            }
        )
    return normalized
