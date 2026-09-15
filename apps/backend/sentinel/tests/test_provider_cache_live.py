"""Opt-in cache verification using dedicated test credentials only."""

import os
import asyncio
import pytest
from sentral.llm.generic.types import ReasoningConfig, SystemMessage, UserMessage
from sentral.llm.providers.anthropic import AnthropicProvider
from sentral.llm.providers.openai import OpenAIProvider
from sentral.llm.providers.codex import CodexProvider
from sentral.llm.http_pool import close_provider_http_pool


@pytest.mark.live_provider("--live-cache")
@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["openai", "anthropic", "codex"])
async def test_provider_reports_real_cache_hit(request, provider_name):
    if not request.config.getoption("--live-cache"):
        pytest.skip("Opt in with --live-cache; uses synthetic input and incurs provider charges")
    key = os.getenv(f"SENTINEL_TEST_{provider_name.upper()}_TOKEN")
    if not key:
        pytest.skip(f"Explicit SENTINEL_TEST_{provider_name.upper()}_TOKEN required")
    provider = {"openai": OpenAIProvider, "anthropic": AnthropicProvider, "codex": CodexProvider}[
        provider_name
    ](key)
    model = os.getenv(f"SENTINEL_TEST_{provider_name.upper()}_MODEL") or (
        "claude-opus-5" if provider_name == "anthropic" else "gpt-6-astra"
    )
    # Stable, synthetic prefix exceeds the cache minimum for these models.
    prefix = "\n".join(
        f"Reference entry {i}: The sample inventory contains blue notebooks and green pencils."
        for i in range(300)
    )
    messages = [SystemMessage(content=prefix), UserMessage(content="Reply with only OK.")]
    rc = ReasoningConfig(max_tokens=1024, reasoning_effort="low")
    try:
        for attempt in range(3):
            response = await provider.chat(messages, model=model, reasoning_config=rc)
            snapshot = response.provider_usage
            assert snapshot is not None, "Missing provider usage"
            usage = snapshot.get("raw_usage") or snapshot["usage"]
            cached = usage.get(
                "cache_read_input_tokens",
                (usage.get("input_tokens_details") or {}).get("cached_tokens", 0),
            )
            if attempt and cached > 0:
                return
            if attempt < 2:
                await asyncio.sleep(1)
        pytest.fail("Provider did not report a cache hit after three identical requests")
    finally:
        if isinstance(provider, CodexProvider):
            await provider.aclose()
        await close_provider_http_pool()
