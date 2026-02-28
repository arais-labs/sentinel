from __future__ import annotations

from enum import StrEnum


class TierName(StrEnum):
    FAST = "fast"
    NORMAL = "normal"
    HARD = "hard"


class ProviderChoice(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"


class ProviderId(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENAI_CODEX = "openai-codex"
    GEMINI = "gemini"


def parse_tier_name(value: str | TierName | None) -> TierName | None:
    if value is None:
        return None
    if isinstance(value, TierName):
        return value
    try:
        return TierName(value)
    except ValueError:
        return None


def parse_provider_choice(value: str | ProviderChoice | None) -> ProviderChoice | None:
    if value is None:
        return None
    if isinstance(value, ProviderChoice):
        return value
    try:
        return ProviderChoice(value)
    except ValueError:
        return None
