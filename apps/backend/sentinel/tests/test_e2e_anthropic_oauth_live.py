from __future__ import annotations

import asyncio
import os

import pytest

from app.services.llm.providers.anthropic import AnthropicProvider
from app.services.llm.generic.types import TextContent, UserMessage

_TOKEN_ENV_KEYS = ("ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
_LIVE_TEST_FLAG = "RUN_LIVE_LLM_TESTS"


def _read_candidate_token() -> str:
    for key in _TOKEN_ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _run(coro):
    return asyncio.run(coro)


def test_live_anthropic_oauth_token_chat_roundtrip():
    if os.getenv(_LIVE_TEST_FLAG, "").strip() != "1":
        pytest.skip(f"WARN: skipped live Anthropic OAuth e2e test because {_LIVE_TEST_FLAG}=1 is not set.")

    token = _read_candidate_token()
    if not token:
        pytest.skip(
            "WARN: skipped live Anthropic OAuth e2e test because ANTHROPIC_OAUTH_TOKEN/ANTHROPIC_API_KEY is not set."
        )

    provider = AnthropicProvider(api_key=token)
    result = _run(
        provider.chat(
            [UserMessage(content="Reply with exactly: OK")],
            model=os.getenv("ANTHROPIC_E2E_MODEL", "claude-sonnet-4-6"),
            tools=[],
            temperature=0.0,
        )
    )

    text = "\n".join(
        block.text for block in result.content if isinstance(block, TextContent) and block.text
    ).strip()
    assert text
    assert "ok" in text.lower()
