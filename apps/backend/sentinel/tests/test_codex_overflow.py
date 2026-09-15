"""Native Codex overflow recovery and lossless continuation regressions."""

import copy
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from sentral.llm.providers.codex import CodexProvider
from sentral.llm.generic.types import UserMessage

COMPACTED = [
    {"type": "compaction", "encrypted_content": "opaque"},
    {"role": "user", "content": "Latest request"},
]
FAIL = {
    "type": "response.failed",
    "response": {"error": {"code": "context_length_exceeded"}},
}
DONE = {"type": "response.completed", "response": {"status": "completed", "output": []}}


@pytest.mark.asyncio
@pytest.mark.parametrize("http_error", [False, True])
async def test_overflow_compacts_once_and_retries_same_configuration(http_error):
    provider = CodexProvider("test")
    calls = []

    async def stream(client, payload, headers):
        calls.append(copy.deepcopy(payload))
        if len(calls) == 1:
            if http_error:
                response = httpx.Response(
                    400,
                    json=FAIL,
                    request=httpx.Request("POST", "https://example.test"),
                )
                response.raise_for_status()
            yield "data: " + json.dumps(FAIL)
        else:
            yield "data: " + json.dumps(DONE)

    provider._stream_lines_once = stream
    provider._compact_input = AsyncMock(return_value=COMPACTED)
    original = {
        "model": "gpt-6-astra",
        "input": [{"role": "user", "content": "Long history"}],
        "tools": [{"type": "function", "name": "lookup"}],
        "reasoning": {"effort": "high"},
    }
    lines = [line async for line in provider._stream_lines(None, original, {})]
    assert len(calls) == 2
    assert calls[1] == {**original, "input": COMPACTED}
    assert original["input"] != COMPACTED
    provider._compact_input.assert_awaited_once()
    assert json.loads(lines[-1][6:])["response"]["sentinel_compacted_input"] == COMPACTED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first",
    [
        {"type": "response.output_text.delta", "delta": "Already displayed"},
        {"type": "response.output_item.added", "item": {"type": "function_call"}},
        {"type": "error", "error": {"code": "rate_limit_exceeded"}},
        {"type": "error", "error": {"code": "invalid_api_key"}},
    ],
)
async def test_never_replays_output_or_compacts_unrelated_errors(first):
    provider = CodexProvider("test")

    async def stream(*args):
        yield "data: " + json.dumps(first)
        if first["type"].startswith("response.output"):
            yield "data: " + json.dumps(FAIL)

    provider._stream_lines_once = stream
    provider._compact_input = AsyncMock()
    assert [line async for line in provider._stream_lines(None, {}, {})]
    provider._compact_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_overflow_is_returned_without_infinite_retry():
    provider = CodexProvider("test")

    async def stream(*args):
        yield "data: " + json.dumps(FAIL)

    provider._stream_lines_once = stream
    provider._compact_input = AsyncMock(return_value=COMPACTED)
    lines = [line async for line in provider._stream_lines(None, {}, {})]
    assert json.loads(lines[-1][6:]) == FAIL
    provider._compact_input.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_compaction_contract_and_failure():
    provider = CodexProvider("test")
    client = AsyncMock()
    client.post.return_value = httpx.Response(200, json={"output": COMPACTED})
    client.post.return_value.request = httpx.Request("POST", "https://example.test")
    assert (
        await provider._compact_input(
            client, {"model": "m", "input": [], "stream": True, "store": False}, {}
        )
        == COMPACTED
    )
    assert client.post.call_args.kwargs["json"] == {"model": "m", "input": []}
    client.post.return_value = httpx.Response(
        200, json={"output": []}, request=httpx.Request("POST", "https://example.test")
    )
    with pytest.raises(RuntimeError, match="no compacted context"):
        await provider._compact_input(client, {}, {})


def test_compacted_history_replaces_prefix_but_preserves_new_answer():
    provider = CodexProvider("test")
    output = [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "New answer"}],
        }
    ]
    message = provider._responses_message(
        {"status": "completed", "sentinel_compacted_input": COMPACTED},
        "gpt-6-astra",
        output,
    )
    assert [part.text for part in message.content] == ["New answer"]
    # JSON roundtrip mirrors persisted native metadata; old history stays stored.
    message.responses_output = json.loads(json.dumps(message.responses_output))
    history = [
        UserMessage(content="Old overflowing history"),
        message,
        UserMessage(content="Next request"),
    ]
    _, items = provider._to_responses_input(history)
    assert items[: len(COMPACTED)] == COMPACTED
    assert "Old overflowing history" not in json.dumps(items)
    assert "Next request" in json.dumps(items)
    assert history[0].content == "Old overflowing history"


@pytest.mark.asyncio
async def test_fallback_folds_bounded_chunks_and_keeps_current_tool_exchange():
    provider = CodexProvider("test")
    calls = []

    async def stream(client, payload, headers):
        calls.append(payload)
        assert len(payload["input"][0]["content"]) < 49000
        assert payload["tools"] == []
        yield "data: " + json.dumps(
            {
                "type": "response.output_text.delta",
                "delta": "Remember sapphire and completed lookup.",
            }
        )
        yield "data: " + json.dumps(
            {"type": "response.completed", "response": {"status": "completed"}}
        )

    provider._stream_lines_once = stream
    tail = [
        {"role": "user", "content": "Latest request"},
        {"type": "function_call", "call_id": "c1", "name": "lookup", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "sapphire"},
    ]
    payload = {
        "model": "gpt-6-astra",
        "input": [{"role": "user", "content": "x" * 96000}] + tail,
        "tools": [{"name": "lookup"}],
    }
    compacted = await provider._summarize_input(None, payload, {})
    assert len(calls) == 3
    assert compacted[1:] == tail
    assert "Remember sapphire" in calls[1]["input"][0]["content"]
    assert len(payload["input"][0]["content"]) == 96000


@pytest.mark.asyncio
async def test_missing_native_endpoint_uses_summary_fallback():
    provider = CodexProvider("test")
    provider._summarize_input = AsyncMock(return_value=COMPACTED)
    client = AsyncMock()
    client.post.return_value = httpx.Response(
        404,
        json={"detail": "Not found"},
        request=httpx.Request("POST", "https://example.test"),
    )
    assert await provider._compact_input(client, {"model": "m", "input": []}, {}) == COMPACTED
    provider._summarize_input.assert_awaited_once()


def test_summary_reset_survives_runtime_conversion_and_preserves_next_tool_result():
    from sentral.llm.runtime_conversions import (
        sentinel_message_to_runtime_item,
        runtime_item_to_sentinel_message,
    )
    from sentral.llm.generic.types import ToolResultMessage

    provider = CodexProvider("test")
    compacted = [
        {"role": "user", "content": "Earlier summary"},
        {"role": "user", "content": "Latest request"},
    ]
    output = [{"type": "function_call", "name": "lookup", "call_id": "c1", "arguments": "{}"}]
    message = provider._responses_message(
        {"status": "completed", "sentinel_compacted_input": compacted},
        "gpt-6-astra",
        output,
    )
    item = sentinel_message_to_runtime_item(message, item_id="assistant")
    item.metadata = json.loads(json.dumps(item.metadata))
    restored = runtime_item_to_sentinel_message(item)
    assert restored.responses_context_reset
    _, replay = provider._to_responses_input(
        [
            UserMessage(content="Old overflowing history"),
            restored,
            ToolResultMessage(tool_call_id="c1", tool_name="lookup", content="Found"),
        ]
    )
    assert replay[:2] == compacted
    assert replay[-1]["type"] == "function_call_output"
    assert replay[-1]["call_id"] == "c1"
    assert "Old overflowing history" not in json.dumps(replay)


@pytest.mark.asyncio
async def test_sse_http_overflow_body_reaches_recovery():
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/compact"):
            return httpx.Response(200, json={"output": COMPACTED})
        if len(calls) == 1:
            return httpx.Response(400, json={"error": {"code": "context_length_exceeded"}})
        return httpx.Response(200, text="data: " + json.dumps(DONE) + "\n\n")

    provider = CodexProvider("test", transport="sse")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        lines = [
            line async for line in provider._stream_lines(client, {"model": "m", "input": []}, {})
        ]
    assert len(calls) == 3
    assert any("sentinel_compacted_input" in line for line in lines)


@pytest.mark.asyncio
async def test_compaction_failure_does_not_retry_original_request():
    provider = CodexProvider("test")
    calls = []

    async def stream(*args):
        calls.append(True)
        yield "data: " + json.dumps(FAIL)

    provider._stream_lines_once = stream
    provider._compact_input = AsyncMock(side_effect=RuntimeError("Unavailable"))
    with pytest.raises(RuntimeError, match="Unavailable"):
        _ = [line async for line in provider._stream_lines(None, {}, {})]
    assert len(calls) == 1
