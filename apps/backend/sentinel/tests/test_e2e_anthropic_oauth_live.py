from __future__ import annotations

import asyncio
import os

import pytest

from sentral.llm.providers.anthropic import AnthropicProvider
from sentral.llm.generic.types import TextContent, UserMessage


def _read_candidate_token() -> str:
    return os.getenv("SENTINEL_TEST_ANTHROPIC_TOKEN", "").strip()


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.live_provider("--live-claude")
def test_live_anthropic_oauth_token_chat_roundtrip(request):
    if not request.config.getoption("--live-claude"):
        pytest.skip("Opt in with --live-claude and SENTINEL_TEST_ANTHROPIC_TOKEN")

    token = _read_candidate_token()
    if not token:
        pytest.skip(
            "WARN: skipped live Anthropic OAuth e2e test because SENTINEL_TEST_ANTHROPIC_TOKEN is not set."
        )

    provider = AnthropicProvider(api_key=token)
    result = _run(
        provider.chat(
            [UserMessage(content="Reply with exactly: OK")],
            model=os.getenv("SENTINEL_TEST_ANTHROPIC_MODEL", "claude-sonnet-5"),
            tools=[],
            temperature=0.0,
        )
    )

    text = "\n".join(
        block.text for block in result.content if isinstance(block, TextContent) and block.text
    ).strip()
    assert text
    assert "ok" in text.lower()


@pytest.mark.live_provider("--live-claude")
def test_live_claude_usage_and_preflight(request):
    if not request.config.getoption("--live-claude"):
        pytest.skip("Opt in with --live-claude")
    from sentral.llm.generic.types import ReasoningConfig

    token = _read_candidate_token()
    if not token:
        pytest.skip("Explicit SENTINEL_TEST_ANTHROPIC_TOKEN required")
    provider = AnthropicProvider(token)

    async def run():
        messages = [UserMessage(content="Reply with only OK.")]
        rc = ReasoningConfig(max_tokens=1024, reasoning_effort="low")
        try:
            count = await provider.count_input_tokens(
                messages, "claude-sonnet-5", reasoning_config=rc
            )
            print("Claude provider input count:", count)
        except Exception as error:
            print("Claude count endpoint unavailable:", type(error).__name__)
        for streaming in (False, True):
            if streaming:
                events = [
                    event
                    async for event in provider.stream(
                        messages, "claude-sonnet-5", reasoning_config=rc
                    )
                ]
                message = next(event.message for event in events if event.type == "done")
                assert any(event.type == "text_delta" for event in events)
            else:
                message = await provider.chat(messages, "claude-sonnet-5", reasoning_config=rc)
            snapshot = message.provider_usage
            assert snapshot is not None
            raw = snapshot["raw_usage"]
            usage = snapshot["usage"]
            assert usage["input_tokens"] == sum(
                raw.get(key, 0)
                for key in (
                    "input_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                )
            )
            assert usage["output_tokens"] == raw["output_tokens"] > 0
            assert snapshot["price"] is not None
            print(
                "Claude",
                "stream" if streaming else "chat",
                "input",
                usage["input_tokens"],
                "output",
                usage["output_tokens"],
                "price",
                snapshot["price"]["usd"],
            )

    _run(run())
