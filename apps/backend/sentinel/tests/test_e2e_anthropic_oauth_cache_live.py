from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest

from app.services.llm.generic.types import ReasoningConfig, SystemMessage, UserMessage
from app.services.llm.providers.anthropic import AnthropicProvider

_LIVE_TEST_FLAG = "RUN_LIVE_LLM_TESTS"
_TOKEN_ENV_KEYS = ("ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN")
_DEFAULT_MODEL = "claude-3-haiku-20240307"


def _read_candidate_token() -> str:
    for key in _TOKEN_ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _run(coro):
    return asyncio.run(coro)


def _is_oauth_token(token: str) -> bool:
    trimmed = token.strip()
    return trimmed.startswith("sk-ant-oat") or trimmed.count(".") >= 2


def _build_messages(stable: str, user_text: str) -> list:
    return [
        SystemMessage(content=stable, metadata={"kind": "base_prompt"}),
        SystemMessage(
            content=f"runtime {int(time.time())}",
            metadata={"kind": "runtime_info"},
        ),
        UserMessage(content=user_text),
    ]


def test_live_anthropic_oauth_cache_roundtrip():
    if os.getenv(_LIVE_TEST_FLAG, "").strip() != "1":
        pytest.skip(f"WARN: skipped live cache e2e test because {_LIVE_TEST_FLAG}=1 is not set.")

    token = _read_candidate_token()
    if not token:
        pytest.skip(
            "WARN: skipped live cache e2e test because ANTHROPIC_OAUTH_TOKEN/ANTHROPIC_API_KEY/ANTHROPIC_TOKEN is not set."
        )

    provider = AnthropicProvider(api_key=token)
    model = os.getenv("ANTHROPIC_E2E_MODEL", _DEFAULT_MODEL)

    run_id = uuid.uuid4().hex[:10]
    stable = (f"SENTINEL LIVE CACHE STABLE BLOCK {run_id} " * 220).strip()

    result1 = _run(
        provider.chat(
            _build_messages(stable, "reply with 1"),
            model=model,
            tools=[],
            temperature=0.0,
            reasoning_config=ReasoningConfig(max_tokens=96),
        )
    )
    result2 = _run(
        provider.chat(
            _build_messages(stable, "reply with 2"),
            model=model,
            tools=[],
            temperature=0.0,
            reasoning_config=ReasoningConfig(max_tokens=96),
        )
    )

    # Non-oauth API keys should still pass the smoke path; oauth path should cache.
    if _is_oauth_token(token):
        created_1 = int(result1.usage.cache_creation_input_tokens or 0)
        read_1 = int(result1.usage.cache_read_input_tokens or 0)
        created_2 = int(result2.usage.cache_creation_input_tokens or 0)
        read_2 = int(result2.usage.cache_read_input_tokens or 0)

        # Warm cache can already exist for identical prefixes in long-lived test environments,
        # so we assert at least one creation across the pair and at least one read on second run.
        assert (created_1 > 0 or created_2 > 0), (
            "Expected at least one cache creation event across the two OAuth runs"
        )
        assert read_2 > 0, (
            "Expected cache read tokens on second OAuth run with identical stable cached system blocks"
        )
    else:
        assert result1.usage.input_tokens > 0
        assert result2.usage.input_tokens > 0
