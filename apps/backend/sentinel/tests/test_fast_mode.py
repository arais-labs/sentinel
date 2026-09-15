"""Premium speed is request-local and reaches provider wire formats."""

import json
from unittest.mock import AsyncMock
import pytest
from app.services.llm.tier import TierConfig, TierModelConfig, TierProvider
from sentral.llm.generic.types import ReasoningConfig
from sentral.llm.ids import TierName
from sentral.llm.providers.anthropic import AnthropicProvider
from sentral.llm.providers.codex import CodexProvider
from sentral.llm.providers.gemini import GeminiProvider
from sentral.llm.providers.openai import OpenAIProvider
from app.services.llm.session_selection import selection_model
from app.services.messages.ingress import build_generation_metadata, normalize_generation_metadata
from app.services.ws.ws_stream_parser import parse_ws_message
from tests.test_llm_providers import _FakeAsyncClient, _FakeResponse, _FakeStreamResponse


@pytest.mark.parametrize(
    "provider,model,supported",
    [
        (OpenAIProvider("test"), "gpt-6-astra", True),
        (OpenAIProvider("test"), "gpt-5.6-sol-2026-06-30", True),
        (OpenAIProvider("test"), "gpt-5.4-pro", False),
        (OpenAIProvider("test"), "gpt-5.6-unknown", False),
        (OpenAIProvider("test", base_url="https://example.test/v1"), "gpt-6-astra", False),
        (CodexProvider("test"), "gpt-5.5", True),
        (CodexProvider("test"), "gpt-5.3-codex-spark", False),
        (AnthropicProvider("test"), "claude-opus-5", True),
        (AnthropicProvider("test"), "claude-opus-4-8", True),
        (AnthropicProvider("test"), "claude-opus-4-7", False),
        (AnthropicProvider("test"), "claude-opus-4-6", False),
        (AnthropicProvider("test"), "claude-sonnet-5", False),
        (GeminiProvider("test"), "gemini-3.1-pro-preview", False),
    ],
)
def test_capabilities(provider, model, supported):
    assert provider.supports_fast_mode(model) is supported


@pytest.mark.asyncio
async def test_routing_isolation_and_unsupported_fallback():
    claude = AnthropicProvider("test")
    claude.chat = AsyncMock(return_value="ok")
    standard = TierModelConfig(claude, "claude-opus-5", ReasoningConfig(reasoning_effort="high"))
    unsupported = TierModelConfig(claude, "claude-sonnet-5", ReasoningConfig())
    tier = TierProvider({TierName.NORMAL: TierConfig(standard, [unsupported])})
    route = selection_model("normal", "anthropic", "low", True)
    assert not tier._resolve_tier(route).fallbacks
    await tier.chat(
        [],
        route,
        reasoning_config=ReasoningConfig(
            max_tokens=1024, override_fields=frozenset({"max_tokens"})
        ),
    )
    rc = claude.chat.call_args.kwargs["reasoning_config"]
    assert rc.fast_mode and rc.reasoning_effort == "low" and rc.max_tokens == 1024
    await tier.chat([], "normal")
    assert not claude.chat.call_args.kwargs["reasoning_config"].fast_mode
    assert not standard.reasoning_config.fast_mode
    assert tier.available_tiers()[0].provider_options[0]["supports_fast_mode"]
    rejected = TierProvider({TierName.NORMAL: TierConfig(unsupported, [standard])})
    with pytest.raises(ValueError, match="not supported"):
        rejected.model_context(route)


@pytest.mark.parametrize("value", ["false", 1, None, {}, []])
def test_ws_rejects_non_boolean_speed(value):
    assert (
        parse_ws_message(json.dumps({"type": "message", "content": "hi", "fast_mode": value}))
        is None
    )


def test_speed_survives_ws_and_retry_metadata():
    assert parse_ws_message('{"type":"message","content":"hi","fast_mode":true}').fast_mode
    assert not parse_ws_message('{"type":"message","content":"hi"}').fast_mode
    metadata = build_generation_metadata(
        requested_tier=selection_model("normal", "openai", None, True),
        resolved_model="gpt-6-astra",
        provider="openai",
        temperature=0.7,
        max_iterations=0,
    )
    assert metadata["model_selection"]["fast_mode"]
    assert normalize_generation_metadata(metadata) == metadata


@pytest.mark.parametrize("oauth", [False, True])
@pytest.mark.asyncio
async def test_claude_payload_beta_and_actual_speed(oauth):
    client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {"content": [], "usage": {"input_tokens": 1, "output_tokens": 1, "speed": "fast"}}
        )
    )
    provider = AnthropicProvider(
        "sk-ant-oat-test" if oauth else "test", client_factory=lambda: client
    )
    message = await provider.chat(
        [], model="claude-opus-5", reasoning_config=ReasoningConfig(fast_mode=True)
    )
    request = client.post_calls[0]
    assert request["json"]["speed"] == "fast"
    assert "fast-mode-2026-02-01" in request["headers"]["anthropic-beta"]
    if oauth:
        assert "oauth" in request["headers"]["anthropic-beta"]
    assert message.provider_usage["speed"] == "fast"
    assert message.provider_usage["price"] is None
    await provider.chat([], model="claude-opus-5")
    assert "speed" not in client.post_calls[-1]["json"]
    assert "fast-mode" not in client.post_calls[-1]["headers"].get("anthropic-beta", "")


@pytest.mark.parametrize("kind", ["responses", "codex", "chat"])
@pytest.mark.parametrize("fast_mode", [True, False])
@pytest.mark.asyncio
async def test_openai_wire_payload(kind, fast_mode):
    payload = {
        "id": "test",
        "model": "gpt-5.5",
        "output": [],
        "usage": {},
        "service_tier": "default",
    }
    client = _FakeAsyncClient(
        stream_response=_FakeStreamResponse(
            ["data: " + json.dumps({"type": "response.completed", "response": payload})]
        ),
        post_response=_FakeResponse(
            {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}
        ),
    )
    provider = (
        CodexProvider("test", transport="sse", client_factory=lambda: client)
        if kind == "codex"
        else OpenAIProvider("test", client_factory=lambda: client)
    )
    if kind == "chat":
        provider._uses_responses = lambda _: False
    await provider.chat([], model="gpt-5.5", reasoning_config=ReasoningConfig(fast_mode=fast_mode))
    request = (client.post_calls if kind == "chat" else client.stream_calls)[-1]
    if kind == "codex":
        if fast_mode:
            assert request["json"]["service_tier"] == "priority"
        else:
            assert "service_tier" not in request["json"]
    else:
        assert request["json"]["service_tier"] == ("fast" if fast_mode else "default")
