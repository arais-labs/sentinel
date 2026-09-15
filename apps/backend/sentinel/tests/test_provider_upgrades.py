"""Provider protocol regressions: cache accounting and lossless continuations."""

import json
import pytest
from sentral.llm.providers.anthropic import (
    AnthropicProvider,
    _map_anthropic_stop_reason,
)
from sentral.llm.generic.types import (
    SystemMessage,
    UserMessage,
    ToolSchema,
    ToolResultMessage,
)
from sentral.llm.runtime_adapter import SentinelProviderAdapter
from sentral import GenerationConfig
from tests.test_llm_providers import (
    _FakeAsyncClient,
    _FakeResponse,
    _FakeStreamResponse,
)


def test_claude_cache_prefix_is_stable_and_does_not_mutate_history():
    provider = AnthropicProvider("test-key")
    history = [SystemMessage(content="Stable system"), UserMessage(content="First")]
    first = provider._payload(history, "claude-opus-5", [], None)
    second = provider._payload(history + [UserMessage(content="Second")], "claude-opus-5", [], None)
    assert first["cache_control"] == second["cache_control"] == {"type": "ephemeral"}
    assert first["system"] == second["system"]
    assert second["messages"][: len(first["messages"])] == first["messages"]
    assert len(history) == 2


@pytest.mark.asyncio
async def test_cache_write_then_read_usage_is_preserved():
    usages = [
        dict(input_tokens=7, output_tokens=2, cache_creation_input_tokens=4096),
        dict(input_tokens=7, output_tokens=2, cache_read_input_tokens=4096),
    ]
    for usage in usages:
        client = _FakeAsyncClient(post_response=_FakeResponse({"content": [], "usage": usage}))
        provider = AnthropicProvider("test-key", client_factory=lambda: client)
        message = await provider.chat([UserMessage(content="Repeat")])
        assert client.post_calls[0]["json"]["cache_control"] == {"type": "ephemeral"}
        assert message.usage.input_tokens == 4103
        assert message.provider_usage["raw_usage"] == usage
        assert message.provider_usage["usage"]["total_tokens"] == 4105


@pytest.mark.parametrize(
    "choice,expected",
    [
        ("none", {"type": "none"}),
        ("auto", {"type": "auto"}),
        ("required", {"type": "any"}),
        ("lookup", {"type": "tool", "name": "lookup"}),
    ],
)
def test_claude_tool_choice_and_thinking_compatibility(choice, expected):
    payload = AnthropicProvider("test-key")._payload(
        [UserMessage(content="Hi")],
        "claude-opus-5",
        [ToolSchema("lookup", "Find", {"type": "object"})],
        None,
        choice,
    )
    assert payload["tool_choice"] == expected
    assert ("thinking" in payload) == (choice in {"none", "auto"})


def test_oauth_does_not_discard_system_instructions():
    payload = AnthropicProvider("sk-ant-oat-test")._payload(
        [SystemMessage(content="Preserve these rules"), UserMessage(content="Hi")],
        "claude-opus-5",
        [],
        None,
    )
    assert payload["system"] == [
        {
            "type": "text",
            "text": "You are Claude Code, Anthropic's official CLI for Claude.",
        },
        {"type": "text", "text": "Preserve these rules"},
    ]


def test_opaque_claude_blocks_survive_replay_and_are_copied():
    provider = AnthropicProvider("test-key")
    raw = [
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "text", "text": "Hello"},
    ]
    message = provider._message({"content": raw}, "claude-opus-5")
    replay = provider._to_anthropic_messages([message])
    assert replay[0]["content"] == raw
    replay[0]["content"][0]["data"] = "changed"
    assert message.responses_output[0]["data"] == "opaque"


def test_parallel_tool_results_are_grouped():
    messages = [
        ToolResultMessage(tool_call_id="a", content="A"),
        ToolResultMessage(tool_call_id="b", content="B"),
    ]
    output = AnthropicProvider("test-key")._to_anthropic_messages(messages)
    assert len(output) == 1
    assert [b["tool_use_id"] for b in output[0]["content"]] == ["a", "b"]


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("pause_turn", "pause_turn"),
        ("refusal", "refusal"),
        ("model_context_window_exceeded", "length"),
    ],
)
def test_distinct_claude_stop_reasons(reason, expected):
    assert _map_anthropic_stop_reason(reason) == expected


def test_reasoning_overrides_are_explicit():
    assert SentinelProviderAdapter._reasoning(GenerationConfig(model="normal")) is None
    result = SentinelProviderAdapter._reasoning(
        GenerationConfig(
            model="normal",
            max_output_tokens=1024,
            provider_metadata={"reasoning_effort": "low"},
        )
    )
    assert result.max_tokens == 1024
    assert result.reasoning_effort == "low"
    assert result.override_fields == {"max_tokens", "reasoning_effort"}


@pytest.mark.asyncio
async def test_stream_preserves_signed_and_opaque_blocks():
    events = [
        {
            "type": "message_start",
            "message": {"content": [], "usage": {"input_tokens": 5}},
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "Thought"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "signed"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "redacted_thinking", "data": "opaque"},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 3, "cache_read_input_tokens": 4096},
        },
        {"type": "message_stop"},
    ]
    client = _FakeAsyncClient(
        stream_response=_FakeStreamResponse(["data: " + json.dumps(e) for e in events])
    )
    provider = AnthropicProvider("test-key", client_factory=lambda: client)
    output = [e async for e in provider.stream([UserMessage(content="Hi")])]
    message = output[-1].message
    assert message.responses_output == [
        {"type": "thinking", "thinking": "Thought", "signature": "signed"},
        {"type": "redacted_thinking", "data": "opaque"},
    ]
    assert message.usage.input_tokens == 4101


@pytest.mark.asyncio
async def test_public_openai_programmatic_tools_and_caller_replay():
    from sentral.llm.providers.openai import OpenAIProvider

    provider = OpenAIProvider("test")
    payload = {
        "model": "gpt-6-astra",
        "tool_choice": "auto",
        "tools": [provider._response_tool(ToolSchema("lookup", "Read", {"type": "object"}))],
    }
    await provider._prepare_response(payload, None, {})
    assert payload["tools"][0]["allowed_callers"] == ["direct", "programmatic"]
    assert payload["tools"][-1] == {"type": "programmatic_tool_calling"}
    caller = {"type": "program", "caller_id": "program-1"}
    raw = [
        {"type": "program", "id": "program-1"},
        {
            "type": "function_call",
            "name": "lookup",
            "call_id": "call-1",
            "arguments": "{}",
            "caller": caller,
        },
    ]
    message = provider._responses_message({"status": "completed"}, "gpt-6-astra", raw)
    _, items = provider._to_responses_input(
        [message, ToolResultMessage(tool_call_id="call-1", content="{}")]
    )
    assert items[:2] == raw
    assert items[-1]["caller"] == caller
    paused = provider._responses_message(
        {"status": "completed"},
        "gpt-6-astra",
        [{"type": "program_output", "output": "done"}],
    )
    assert paused.stop_reason == "pause_turn"


@pytest.mark.asyncio
async def test_compatible_endpoint_does_not_receive_public_hosted_tools():
    from sentral.llm.providers.openai import OpenAIProvider

    provider = OpenAIProvider("test", base_url="https://compatible.test/v1")
    payload = {
        "model": "gpt-6-astra",
        "tool_choice": "auto",
        "tools": [provider._response_tool(ToolSchema("lookup", "Read", {}))],
    }
    await provider._prepare_response(payload, None, {})
    assert len(payload["tools"]) == 1
    assert "allowed_callers" not in payload["tools"][0]


def test_claude_programmatic_container_survives_runtime_roundtrip():
    from sentral.llm.runtime_conversions import (
        sentinel_message_to_runtime_item,
        runtime_item_to_sentinel_message,
    )

    provider = AnthropicProvider("test")
    raw = [
        {
            "type": "server_tool_use",
            "id": "server-1",
            "name": "code_execution",
            "input": {"code": "..."},
        },
        {
            "type": "tool_use",
            "id": "call-1",
            "name": "lookup",
            "input": {},
            "caller": {"type": "code_execution_20260120", "tool_id": "server-1"},
        },
    ]
    message = provider._message(
        {
            "content": raw,
            "usage": {"input_tokens": 1, "output_tokens": 2},
            "container": {"id": "container-1"},
        },
        "claude-opus-5",
    )
    restored = runtime_item_to_sentinel_message(
        sentinel_message_to_runtime_item(message, item_id="one")
    )
    payload = provider._payload(
        [restored, ToolResultMessage(tool_call_id="call-1", content="{}")],
        "claude-opus-5",
        [ToolSchema("lookup", "Read", {})],
        None,
    )
    assert payload["container"] == "container-1"
    assert payload["messages"][0]["content"] == raw
    assert payload["tools"][-1]["type"] == "code_execution_20260120"
    oauth = AnthropicProvider("sk-ant-oat-test")._payload(
        [UserMessage(content="Hi")],
        "claude-opus-5",
        [ToolSchema("lookup", "Read", {})],
        None,
    )
    assert len(oauth["tools"]) == 1


@pytest.mark.asyncio
async def test_http_pool_reuses_connection_without_reusing_cookies():
    import asyncio
    from sentral.llm.http_pool import (
        provider_http_client,
        close_provider_http_pool,
    )

    connections = 0
    requests = []
    handlers = set()

    async def serve(reader, writer):
        nonlocal connections
        connections += 1
        task = asyncio.current_task()
        handlers.add(task)
        try:
            while True:
                try:
                    request = await reader.readuntil(b"\r\n\r\n")
                except asyncio.IncompleteReadError:
                    break
                requests.append(request)
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nSet-Cookie: private=value\r\n\r\n{}"
                )
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        for _ in range(2):
            async with provider_http_client() as client:
                assert (await client.get(url)).status_code == 200
        assert connections == 1
        assert all(b"Cookie:" not in request for request in requests)
    finally:
        await close_provider_http_pool()
        server.close()
        await server.wait_closed()
        if handlers:
            await asyncio.gather(*list(handlers))


@pytest.mark.asyncio
async def test_codex_reuses_completed_socket_but_discards_cancelled_socket(monkeypatch):
    from sentral.llm.providers.codex import CodexProvider

    opened, closed = [], []

    class Socket:
        async def __aenter__(self):
            opened.append(self)
            return self

        async def __aexit__(self, *args):
            closed.append(self)

        async def send(self, raw):
            self.frame = {"type": "response.completed"}

        async def recv(self):
            return json.dumps(self.frame)

    monkeypatch.setattr("sentral.llm.providers.codex.connect", lambda *a, **k: Socket())
    provider = CodexProvider("test")
    try:
        for _ in range(2):
            assert len([line async for line in provider._stream_lines(None, {}, {})]) == 1
        assert len(opened) == 1
        assert closed == []
        stream = provider._stream_lines(None, {}, {"changed": "header"})
        await anext(stream)
        await stream.aclose()
        assert len(opened) == 2
    finally:
        await provider.aclose()
    assert len(closed) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_async_tool_overlaps_model_and_is_cleaned_up(cancel):
    import asyncio
    from sentral import (
        AgentRuntimeEngine,
        AssistantTurn,
        ConversationItem,
        RunTurnRequest,
        TextBlock,
        ToolCallBlock,
        ToolDefinition,
        ToolExecutionResult,
    )
    from tests.test_agent_runtime_contracts import _StaticToolRegistry

    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def execute(payload):
        started.set()
        try:
            await release.wait()
            return ToolExecutionResult(status="ok", content="Result")
        finally:
            finished.set()

    class Provider:
        name = "openai"
        calls = 0

        async def chat(self, messages, tools, config):
            self.calls += 1
            if self.calls == 1:
                return AssistantTurn(
                    ConversationItem(
                        id="a",
                        role="assistant",
                        content=[ToolCallBlock(id="call", name="http_request")],
                        metadata={
                            "responses_output": [
                                {
                                    "type": "function_call",
                                    "call_id": "call",
                                    "async": True,
                                }
                            ]
                        },
                    ),
                    "tool_use",
                )
            if self.calls == 2:
                await asyncio.wait_for(started.wait(), 1)
                assert not finished.is_set()
                if cancel:
                    await asyncio.Event().wait()
                release.set()
                return AssistantTurn(
                    ConversationItem(
                        id="b",
                        role="assistant",
                        content=[TextBlock(text="Independent work")],
                    ),
                    "stop",
                )
            assert finished.is_set()
            assert any(item.role == "tool" for item in messages)
            return AssistantTurn(
                ConversationItem(
                    id="c",
                    role="assistant",
                    content=[TextBlock(text="Final with result")],
                ),
                "stop",
            )

    provider = Provider()
    engine = AgentRuntimeEngine(
        provider=provider,
        tool_registry=_StaticToolRegistry([ToolDefinition("http_request", "Read", {}, execute)]),
    )
    task = asyncio.create_task(
        engine.run_turn(
            RunTurnRequest(
                conversation_id="test",
                new_items=[ConversationItem("u", "user", [TextBlock(text="Go")])],
                config=GenerationConfig(model="test", stream=False),
            )
        )
    )
    if cancel:
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
    result = await asyncio.wait_for(task, 2)
    assert finished.is_set()
    assert result.status == ("aborted" if cancel else "completed")
    if not cancel:
        assert provider.calls == 3


def test_legacy_claude_does_not_receive_adaptive_thinking():
    from sentral.llm.generic.types import ReasoningConfig

    provider = AnthropicProvider("test")
    options = provider._generation_options(ReasoningConfig(), "claude-haiku-4-5")
    assert options == {"max_tokens": 8192}
    options = provider._generation_options(
        ReasoningConfig(thinking_budget=2048), "claude-sonnet-4-5"
    )
    assert options["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    with pytest.raises(ValueError):
        provider._generation_options(
            ReasoningConfig(max_tokens=1024, thinking_budget=2048), "claude-sonnet-4-5"
        )


@pytest.mark.asyncio
async def test_tool_concurrency_is_bounded():
    import asyncio
    from sentral import (
        AgentRuntimeEngine,
        ToolCallBlock,
        ToolDefinition,
        ToolExecutionResult,
    )
    from tests.test_agent_runtime_contracts import _StaticToolRegistry

    running, peak = 0, 0

    async def execute(payload):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.001)
        running -= 1
        return ToolExecutionResult(status="ok", content="done")

    engine = AgentRuntimeEngine(
        provider=None,
        tool_registry=_StaticToolRegistry([ToolDefinition("read", "Read", {}, execute)]),
    )
    results = await engine._execute_tool_calls(
        [ToolCallBlock(id=str(i), name="read") for i in range(25)]
    )
    assert 1 < peak <= 8
    assert len(results) == 25
    assert [r.tool_call_id for r in results] == [str(i) for i in range(25)]
