"""Published OpenAI token rates, snapshotted with each measured response."""

from decimal import Decimal
from typing import Any

# USD per million tokens; https://developers.openai.com/api/docs/pricing
# Verified 2026-09-07. Sol promotional pricing runs at least through 2026-11-21.
OPENAI_RATES = {
    "gpt-6-astra": ("10", "1", "12.5", "50"),
    "gpt-5.6-sol": ("4", ".4", "5", "20"),
    "gpt-5.6-terra": ("2", ".2", "2.5", "12"),
    "gpt-5.6-luna": (".2", ".02", ".25", "1.2"),
}

# Base input, cache read, 5m write, 1h write, output; verified 2026-09-07.
CLAUDE_RATES = {
    "claude-sonnet-5": ("2", ".2", "2.5", "4", "10"),
    "claude-opus-5": ("5", ".5", "6.25", "10", "25"),
    "claude-fable-5-1": ("10", ".25", "12.5", "20", "50"),
}


def claude_token_price(model, usage):
    rates = CLAUDE_RATES.get(model)
    if (
        not rates
        or usage.get("speed", "standard") != "standard"
        or usage.get("service_tier", "standard") != "standard"
    ):
        return None
    writes = usage.get("cache_creation_input_tokens", 0)
    creation = usage.get("cache_creation") or {}
    short = creation.get("ephemeral_5m_input_tokens", 0)
    long = creation.get("ephemeral_1h_input_tokens", 0)
    if short + long != writes:
        return None
    counts = [
        usage.get("input_tokens"),
        usage.get("cache_read_input_tokens", 0),
        short,
        long,
        usage.get("output_tokens"),
    ]
    if any(type(count) is not int or count < 0 for count in counts):
        return None
    cost = sum(count * Decimal(rate) for count, rate in zip(counts, rates)) / 1_000_000
    return {
        "usd": str(cost),
        "rates_per_million": dict(
            zip(("input", "cached_input", "cache_write_5m", "cache_write_1h", "output"), rates)
        ),
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
        "rates_as_of": "2026-09-07",
    }


def openai_token_price(model: str, usage: dict[str, Any], service_tier: str) -> dict | None:
    """Token-only list price, never an invoice or a subscription charge."""
    rates = OPENAI_RATES.get("gpt-5.6-sol" if model == "gpt-5.6" else model)
    multiplier = {"default": "1", "flex": ".5", "priority": "2", "fast": "2"}.get(service_tier)
    if rates is None or multiplier is None:
        return None
    details = usage.get("input_tokens_details") or {}
    counts = [
        usage.get("input_tokens"),
        details.get("cached_tokens"),
        details.get("cache_write_tokens"),
        usage.get("output_tokens"),
    ]
    if any(type(count) is not int or count < 0 for count in counts):
        return None
    input_tokens, cached, written, output_tokens = counts
    if cached + written > input_tokens:
        return None
    counts = [input_tokens - cached - written, cached, written, output_tokens]
    applied = [Decimal(rate) * Decimal(multiplier) for rate in rates]
    if input_tokens > 272_000:
        applied = [rate * (Decimal("1.5") if i == 3 else 2) for i, rate in enumerate(applied)]
    cost = sum(count * rate for count, rate in zip(counts, applied)) / 1_000_000
    return {
        "usd": str(cost),
        "rates_per_million": dict(
            zip(("input", "cached_input", "cache_write", "output"), map(str, applied))
        ),
        "source": "https://developers.openai.com/api/docs/pricing",
        "rates_as_of": "2026-09-07",
    }
