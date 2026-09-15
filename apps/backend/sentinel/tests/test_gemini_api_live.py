"""Opt-in Gemini API checks using SENTINEL_TEST_GEMINI_API_KEY from the environment."""

import json
import os

import pytest

from sentral.llm.generic.types import (
    AssistantMessage,
    ReasoningConfig,
    ToolResultMessage,
    ToolSchema,
    UserMessage,
)
from sentral.llm.providers.gemini import GeminiProvider

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.live_provider("--live-gemini"),
]


def provider():
    key = os.environ.get("SENTINEL_TEST_GEMINI_API_KEY")
    if not key:
        pytest.skip("Explicit SENTINEL_TEST_GEMINI_API_KEY required")
    return GeminiProvider(key)


@pytest.mark.parametrize("stream", [False, True], ids=["chat", "stream"])
@pytest.mark.parametrize(
    "model,reasoning",
    [
        ("gemini-3.5-flash-lite", ReasoningConfig(max_tokens=4096)),
        ("gemini-3.8-flash", ReasoningConfig(reasoning_effort="low")),
        ("gemini-3.1-pro-preview", ReasoningConfig(thinking_budget=32000, max_tokens=40000)),
    ],
)
async def test_live_api_tiers(model, reasoning, stream):
    client = provider()
    messages = [UserMessage(content="Reply with exactly API_OK")]
    if stream:
        events = [
            event
            async for event in client.stream(messages, model=model, reasoning_config=reasoning)
        ]
        assert "API_OK" in "".join(
            event.delta or "" for event in events if event.type == "text_delta"
        )
        assert sum(event.type == "done" for event in events) == 1
        assert not any(event.type == "error" for event in events)
    else:
        result = await client.chat(messages, model=model, reasoning_config=reasoning)
        assert result.model == model
        assert any("API_OK" in getattr(block, "text", "") for block in result.content)


@pytest.mark.parametrize("model", ["gemini-3.5-flash-lite", "gemini-3.8-flash"])
async def test_live_api_streamed_tool_round_trip(model):
    client = provider()
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
        async for event in client.stream(
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
    result = await client.chat(messages, model=model, tools=[tool])
    assert any("42" in getattr(block, "text", "") for block in result.content)
