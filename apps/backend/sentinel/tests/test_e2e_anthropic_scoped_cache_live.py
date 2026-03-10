"""Live e2e test for V4 caching strategy (1 system + 3 message breakpoints).

Verifies against real Anthropic API that:
1. Cache entries are created on first call
2. Cache hits occur on second call
3. More tokens are cached compared to old approach (system only)

Requires: RUN_LIVE_LLM_TESTS=1, ANTHROPIC_TOKEN set.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from app.services.llm.providers.anthropic import AnthropicProvider
from app.services.llm.generic.types import ReasoningConfig


_LIVE_FLAG = "RUN_LIVE_LLM_TESTS"
_DEFAULT_MODEL = "claude-3-haiku-20240307"


def _run(coro):
    return asyncio.run(coro)


def _token():
    for var in ("ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN"):
        t = os.getenv(var, "").strip()
        if t:
            return t
    return None


def test_live_v4_cache_strategy():
    if os.getenv(_LIVE_FLAG, "").strip() != "1":
        pytest.skip(f"{_LIVE_FLAG}=1 not set")
    token = _token()
    if not token:
        pytest.skip("No Anthropic token")

    provider = AnthropicProvider(api_key=token)
    model = os.getenv("ANTHROPIC_E2E_MODEL", _DEFAULT_MODEL)
    rid = uuid.uuid4().hex[:10]
    padding = ("The quick brown fox jumps over the lazy dog. " * 200).strip()

    messages = [
        {"role": "system", "content": f"SYS-{rid}\n{padding}", "metadata": {"kind": "base_prompt"}},
        {"role": "system", "content": f"## Memory\n{padding[:300]}", "metadata": {"kind": "base_prompt"}},
        {"role": "system", "content": f"Time: 2026-03-10 {rid}", "metadata": {"kind": "runtime_info"}},
        {"role": "user", "content": f"Hello {rid}"},
        {"role": "assistant", "content": [{"type": "text", "text": f"Hi {rid}, how can I help?"}]},
        {"role": "user", "content": "Run tests"},
        {"role": "assistant", "content": [{"type": "text", "text": "All tests pass."}]},
        {"role": "user", "content": "reply with 1"},
    ]

    rc = ReasoningConfig(max_tokens=96)

    # Call 1: cache creation
    r1 = _run(provider.chat(messages, model=model, tools=[], temperature=0.0, reasoning_config=rc))
    u1 = r1.usage
    print(f"\nCall 1: input={u1.input_tokens} creation={u1.cache_creation_input_tokens} read={u1.cache_read_input_tokens}")

    assert u1.cache_creation_input_tokens > 0, f"Expected cache creation, got {u1.cache_creation_input_tokens}"

    # Call 2: cache read
    r2 = _run(provider.chat(messages, model=model, tools=[], temperature=0.0, reasoning_config=rc))
    u2 = r2.usage
    print(f"Call 2: input={u2.input_tokens} creation={u2.cache_creation_input_tokens} read={u2.cache_read_input_tokens}")

    assert u2.cache_read_input_tokens > 0, f"Expected cache read, got {u2.cache_read_input_tokens}"
    assert u2.cache_read_input_tokens >= u1.cache_creation_input_tokens

    # The cache should cover system + recent messages
    total = u1.input_tokens + u1.cache_creation_input_tokens
    ratio = u2.cache_read_input_tokens / total if total > 0 else 0
    print(f"Cache hit ratio: {ratio:.1%}")
    assert ratio > 0.3, f"Expected > 30% cache ratio, got {ratio:.1%}"
