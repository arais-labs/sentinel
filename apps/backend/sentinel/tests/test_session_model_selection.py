import json
from unittest.mock import AsyncMock

import pytest
from app.services.llm.tier import TierProvider, TierConfig, TierModelConfig
from sentral.llm.generic.types import ReasoningConfig, UserMessage
from sentral.llm.providers.anthropic import AnthropicProvider
from sentral.llm.providers.openai import OpenAIProvider
from sentral.llm.ids import TierName
from app.services.llm.session_selection import selection_model, with_reasoning
from app.services.ws.ws_stream_parser import parse_ws_message


def provider():
    a = TierModelConfig(
        AnthropicProvider("test"),
        "claude-opus-5",
        ReasoningConfig(reasoning_effort="medium"),
    )
    b = TierModelConfig(
        OpenAIProvider("test"),
        "gpt-6-astra",
        ReasoningConfig(reasoning_effort="medium"),
    )
    return TierProvider({TierName.NORMAL: TierConfig(a, [b])})


@pytest.mark.asyncio
async def test_selection_routes_generation_and_count_without_mutating_defaults():
    p = provider()
    original = p._tiers[TierName.NORMAL]
    selected = original.fallbacks[0].provider
    selected.chat = AsyncMock(return_value="response")
    selected.count_input_tokens = AsyncMock(return_value=123)
    route = selection_model("normal", "openai", "high")
    assert await p.chat([UserMessage(content="hello")], route) == "response"
    assert selected.chat.call_args.kwargs["reasoning_config"].reasoning_effort == "high"
    assert await p.count_input_tokens([], route) == 123
    assert selected.count_input_tokens.call_args.args[1] == "gpt-6-astra"
    assert selected.count_input_tokens.call_args.args[3].reasoning_effort == "high"
    assert p.model_context(route)["model"] == "gpt-6-astra"
    assert original.primary.provider.name == "anthropic"
    assert original.fallbacks[0].reasoning_config.reasoning_effort == "medium"
    assert p._resolve_tier(route).fallbacks == []


def test_catalog_exposes_configured_providers_and_levels():
    options = provider().available_tiers()[0].provider_options
    assert [o["provider_id"] for o in options] == ["anthropic", "openai"]
    assert options[0]["reasoning_levels"] == ["low", "medium", "high", "xhigh", "max"]


def test_unavailable_provider_rejected():
    with pytest.raises(ValueError, match="not configured"):
        provider().model_context(selection_model("normal", "gemini", "low"))


def test_budget_mapping_keeps_output_room():
    config = TierModelConfig(
        AnthropicProvider("test"), "claude-sonnet-4-5", ReasoningConfig(max_tokens=8192)
    )
    selected = with_reasoning(config, "high")
    assert selected.reasoning_config.thinking_budget == 16384
    assert selected.reasoning_config.max_tokens == 20480
    assert config.reasoning_config.max_tokens == 8192


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider_id", "unknown"),
        ("reasoning_level", "extreme"),
        ("provider_id", {}),
        ("reasoning_level", []),
    ],
)
def test_invalid_selection_rejected(field, value):
    assert (
        parse_ws_message(json.dumps({"type": "message", "content": "hello", field: value})) is None
    )


def test_ws_preserves_choices_for_form_submission():
    parsed = parse_ws_message(
        json.dumps(
            {
                "type": "message",
                "content": "Form answers",
                "provider_id": "anthropic",
                "reasoning_level": "low",
                "form_response": {},
            }
        )
    )
    assert parsed.provider_id == "anthropic"
    assert parsed.reasoning_level == "low"


def test_gemini_three_uses_levels_instead_of_budget():
    from sentral.llm.providers.gemini import GeminiProvider

    provider = GeminiProvider("test")
    cfg = TierModelConfig(
        provider, "gemini-3.1-pro-preview", ReasoningConfig(thinking_budget=32000)
    )
    rc = with_reasoning(cfg, "medium").reasoning_config
    payload = provider._build_payload(
        [],
        cfg.model,
        None,
        0.7,
        rc.thinking_budget or 0,
        reasoning_effort=rc.reasoning_effort,
    )
    assert payload["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "medium"}


def test_selection_survives_generation_metadata_normalization():
    from app.services.messages.ingress import (
        build_generation_metadata,
        normalize_generation_metadata,
    )

    metadata = build_generation_metadata(
        requested_tier=selection_model("hard", "anthropic", "low"),
        resolved_model="claude-fable-5-1",
        provider="anthropic",
        temperature=0.7,
        max_iterations=50,
    )
    assert metadata["requested_tier"] == "hard"
    assert metadata["model_selection"] == {
        "provider_id": "anthropic",
        "reasoning_level": "low",
    }
    assert normalize_generation_metadata(metadata) == metadata


@pytest.mark.parametrize("level", ["xhigh", "max"])
@pytest.mark.asyncio
async def test_extended_effort_reaches_provider(level):
    p = provider()
    selected = p._tiers[TierName.NORMAL].fallbacks[0].provider
    selected.chat = AsyncMock(return_value="response")
    route = selection_model("normal", "openai", level)
    await p.chat([UserMessage(content="hello")], route)
    assert selected.chat.call_args.kwargs["reasoning_config"].reasoning_effort == level
    parsed = parse_ws_message(
        json.dumps({"type": "message", "content": "hi", "reasoning_level": level})
    )
    assert parsed.reasoning_level == level


def test_unsupported_extended_effort_rejected():
    config = TierModelConfig(AnthropicProvider("test"), "claude-sonnet-4-6", ReasoningConfig())
    with pytest.raises(ValueError, match="not supported"):
        with_reasoning(config, "max")


@pytest.mark.parametrize("model", ["gemini-3.8-flash", "gemini-3.5-flash-lite"])
def test_current_gemini_models_support_reasoning_and_context(model):
    from app.services.llm.session_selection import reasoning_kind, reasoning_levels
    from sentral.llm.model_limits import model_context

    assert reasoning_kind(model) == "effort"
    assert reasoning_levels(model) == ["low", "medium", "high"]
    assert model_context(model)["context_window_tokens"] == 1_048_576
