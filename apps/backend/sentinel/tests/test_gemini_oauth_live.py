"""Explicit --live-gemini checks with SENTINEL_TEST_GEMINI_OAUTH_JSON credentials."""

import json
import os

import pytest

from sentral.llm.providers.gemini_oauth import GeminiOAuthCredentials, GeminiOAuthProvider
from sentral.llm.generic.types import (
    AssistantMessage,
    ReasoningConfig,
    ToolResultMessage,
    ToolSchema,
    UserMessage,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.live_provider("--live-gemini"),
]


def oauth_credentials():
    raw = os.environ.get("SENTINEL_TEST_GEMINI_OAUTH_JSON")
    if not raw:
        pytest.skip("Explicit SENTINEL_TEST_GEMINI_OAUTH_JSON required")
    try:
        return GeminiOAuthCredentials.parse_input(raw)
    except (ValueError, TypeError):
        pytest.fail("Invalid dedicated Gemini OAuth test credentials", pytrace=False)


@pytest.mark.parametrize(
    "model,resolved,reasoning",
    [
        ("gemini-3.5-flash-lite", "gemini-3.5-flash-lite", ReasoningConfig()),
        ("gemini-3.8-flash", "gemini-3.8-flash-tiered", ReasoningConfig(reasoning_effort="low")),
        (
            "gemini-3.1-pro-preview",
            "gemini-pro-agent",
            ReasoningConfig(thinking_budget=32000, max_tokens=40000),
        ),
    ],
)
async def test_live_tier_chat_and_token_refresh(model, resolved, reasoning):
    credentials = oauth_credentials()
    assert credentials is not None
    credentials.expiry_date = 0
    provider = GeminiOAuthProvider(credentials)
    result = await provider.chat(
        [UserMessage(content="Reply with exactly OAUTH_OK")],
        model=model,
        reasoning_config=reasoning,
    )
    assert result.model == resolved, "The requested model must succeed without falling back"
    assert any("OAUTH_OK" in getattr(block, "text", "") for block in result.content)


async def test_live_stream_and_tool_result_round_trip():
    credentials = oauth_credentials()
    assert credentials is not None
    provider = GeminiOAuthProvider(credentials)
    model = "gemini-3.8-flash"
    events = [
        event
        async for event in provider.stream(
            [UserMessage(content="Reply with exactly STREAM_OK")],
            model=model,
        )
    ]
    assert "STREAM_OK" in "".join(
        event.delta or "" for event in events if event.type == "text_delta"
    )
    assert sum(event.type == "done" for event in events) == 1
    tool = ToolSchema(
        "add_numbers",
        "Add two integers.",
        {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    )
    messages = [UserMessage(content="Use add_numbers to add 17 and 25, then report the result.")]
    events = [
        event
        async for event in provider.stream(
            messages, model=model, tools=[tool], tool_choice="required"
        )
    ]
    calls = []
    arguments = ""
    for event in events:
        if event.type == "toolcall_start":
            calls.append(event.tool_call)
            arguments = ""
        elif event.type == "toolcall_delta":
            arguments += event.delta or ""
        elif event.type == "toolcall_end":
            calls[-1].arguments = json.loads(arguments)
    assert calls and all(call.name == "add_numbers" and call.thought_signature for call in calls)
    assert events[-1].stop_reason == "tool_use"
    messages.append(AssistantMessage(content=calls, model=model, provider="gemini"))
    for call in calls:
        messages.append(
            ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=str(call.arguments["a"] + call.arguments["b"]),
            )
        )
    result = await provider.chat(messages, model=model, tools=[tool])
    assert any("42" in getattr(block, "text", "") for block in result.content)


@pytest.mark.parametrize(
    "model,reasoning",
    [
        ("gemini-3.5-flash-lite", ReasoningConfig(max_tokens=4096)),
        ("gemini-3.1-pro-preview", ReasoningConfig(thinking_budget=32000, max_tokens=40000)),
    ],
)
async def test_live_fast_and_hard_stream(model, reasoning):
    credentials = oauth_credentials()
    assert credentials is not None
    provider = GeminiOAuthProvider(credentials)
    events = [
        event
        async for event in provider.stream(
            [UserMessage(content="Reply exactly STREAM_OK")],
            model=model,
            reasoning_config=reasoning,
        )
    ]
    assert "STREAM_OK" in "".join(
        event.delta or "" for event in events if event.type == "text_delta"
    )
    assert sum(event.type == "done" for event in events) == 1
    assert not any(event.type == "error" for event in events)
    assert (
        not provider._model_cooldowns
    ), "The selected tier must stream without a capacity fallback"
