from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

import httpx
import pytest

from app.services.llm.providers.anthropic import AnthropicProvider
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.errors import TransientProviderError
from app.services.llm.providers.codex import CodexProvider
from app.services.llm.providers.gemini import GeminiProvider
from app.services.llm.providers.gemini_schema_cleaner import clean_schema_for_gemini
from app.services.llm.providers.openai import OpenAIProvider
from app.services.llm.generic.reliable import ReliableProvider
from app.services.llm.generic.router import RouterProvider
from app.services.llm.generic.tier import TierConfig, TierModelConfig, TierProvider
from app.services.llm.ids import ProviderId, TierName
from app.services.llm.generic.types import (
    AgentEvent,
    AssistantMessage,
    ImageContent,
    ReasoningConfig,
    SystemMessage,
    TextContent,
    TokenUsage,
    ToolCallContent,
    ToolResultMessage,
    ToolSchema,
    UserMessage,
)


def _run(coro):
    return asyncio.run(coro)


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("request failed", request=request, response=response)

    def json(self) -> dict:
        return self._payload


class _FakeStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200):
        self._lines = lines
        self.status_code = status_code
        self.is_error = status_code >= 400

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("request failed", request=request, response=response)

    async def aread(self) -> bytes:
        return b""

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    def __init__(
        self,
        *,
        post_response: _FakeResponse | None = None,
        stream_response: _FakeStreamResponse | None = None,
        get_response: _FakeResponse | None = None,
    ):
        self.post_response = post_response or _FakeResponse({})
        self.stream_response = stream_response or _FakeStreamResponse([])
        self.get_response = get_response or _FakeResponse({})
        self.post_calls: list[dict] = []
        self.stream_calls: list[dict] = []
        self.get_calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, *, json: dict, headers: dict[str, str]):
        self.post_calls.append({"url": url, "json": json, "headers": headers})
        return self.post_response

    async def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.get_calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        return self.get_response

    def stream(self, method: str, url: str, *, json: dict, headers: dict[str, str]):
        self.stream_calls.append(
            {"method": method, "url": url, "json": json, "headers": headers}
        )
        return self.stream_response


def test_llm_types_are_json_serializable():
    message = AssistantMessage(
        content=[
            TextContent(text="hello"),
            ToolCallContent(id="tool-1", name="lookup", arguments={"q": "abc"}),
        ],
        model="m",
        provider="p",
        usage=TokenUsage(input_tokens=1, output_tokens=2),
        stop_reason="tool_use",
    )
    payload = asdict(message)
    encoded = json.dumps(payload)
    assert "tool-1" in encoded
    assert payload["usage"]["input_tokens"] == 1


def test_anthropic_chat_formats_and_parses_tool_calls():
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {"type": "text", "text": "Done"},
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "search_web",
                        "input": {"query": "arais"},
                    },
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 12, "output_tokens": 9},
            }
        )
    )
    provider = AnthropicProvider(
        api_key="test-key",
        base_url="https://anthropic.example",
        client_factory=lambda: fake_client,
    )
    tools = [ToolSchema(name="search_web", description="Search", parameters={"type": "object"})]
    result = _run(
        provider.chat(
            [
                {"role": "system", "content": "You are a helpful assistant."},
                UserMessage(content="find info"),
            ],
            model="claude-sonnet-4-20250514",
            tools=tools,
        )
    )

    post_call = fake_client.post_calls[0]
    assert post_call["url"] == "https://anthropic.example/v1/messages"
    assert post_call["json"]["system"] == "You are a helpful assistant."
    assert post_call["json"]["tools"][0]["input_schema"] == {"type": "object"}
    assert result.stop_reason == "tool_use"
    assert isinstance(result.content[1], ToolCallContent)
    assert result.content[1].arguments["query"] == "arais"


def test_anthropic_stream_emits_all_event_types():
    stream_lines = [
        'data: {"type":"message_start"}',
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hello"}}',
        'data: {"type":"content_block_stop","index":0,"content_block":{"type":"text"}}',
        'data: {"type":"content_block_start","index":1,"content_block":{"type":"thinking"}}',
        'data: {"type":"content_block_delta","index":1,"delta":{"type":"thinking_delta","thinking":"hmm"}}',
        'data: {"type":"content_block_stop","index":1,"content_block":{"type":"thinking"}}',
        'data: {"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"call_1","name":"lookup","input":{}}}',
        'data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{\\"a\\":1}"}}',
        'data: {"type":"content_block_stop","index":2,"content_block":{"type":"tool_use"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = AnthropicProvider(
        api_key="test-key",
        base_url="https://anthropic.example",
        client_factory=lambda: fake_client,
    )

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream([UserMessage(content="hello")], model="claude-sonnet-4-20250514"):
            events.append(event)
        return events

    events = _run(_collect())
    event_types = {item.type for item in events}
    assert event_types == {
        "start",
        "text_start",
        "text_delta",
        "text_end",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_end",
        "done",
    }


def test_anthropic_stream_raises_on_sse_error_event():
    stream_lines = [
        'data: {"type":"message_start"}',
        'data: {"type":"error","error":{"message":"boom"}}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = AnthropicProvider(
        api_key="test-key",
        base_url="https://anthropic.example",
        client_factory=lambda: fake_client,
    )

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream(
            [UserMessage(content="hello")],
            model="claude-sonnet-4-20250514",
        ):
            events.append(event)
        return events

    with pytest.raises(RuntimeError, match="Anthropic stream sse_error: boom"):
        _run(_collect())


def test_openai_chat_with_custom_base_url():
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": "working",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "search", "arguments": '{"q":"abc"}'},
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 6},
            }
        )
    )
    provider = OpenAIProvider(
        api_key="test-key",
        base_url="https://openai.example/v1",
        client_factory=lambda: fake_client,
    )
    result = _run(provider.chat([UserMessage(content="hello")], model="gpt-4o-mini"))
    post_call = fake_client.post_calls[0]
    assert post_call["url"] == "https://openai.example/v1/chat/completions"
    assert result.stop_reason == "tool_use"
    assert isinstance(result.content[1], ToolCallContent)
    assert result.content[1].arguments["q"] == "abc"


def test_openai_formats_user_image_blocks():
    provider = OpenAIProvider(api_key="test-key")
    converted = provider._to_openai_messages(  # type: ignore[attr-defined]
        [
            UserMessage(
                content=[
                    TextContent(text="Describe this"),
                    ImageContent(media_type="image/png", data="AAAABBBB"),
                ]
            )
        ]
    )
    assert converted[0]["role"] == "user"
    parts = converted[0]["content"]
    assert isinstance(parts, list)
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,AAAABBBB"


def test_anthropic_formats_user_image_blocks():
    provider = AnthropicProvider(api_key="test-key", base_url="https://anthropic.example")
    converted = provider._to_anthropic_messages(  # type: ignore[attr-defined]
        [
            UserMessage(
                content=[
                    TextContent(text="Look at this"),
                    ImageContent(media_type="image/jpeg", data="CCCCDDDD"),
                ]
            )
        ]
    )
    assert converted[0]["role"] == "user"
    blocks = converted[0]["content"]
    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"]["media_type"] == "image/jpeg"
    assert blocks[1]["source"]["data"] == "CCCCDDDD"


def test_reliable_provider_fallback_on_429():
    class _RetryProvider(LLMProvider):
        @property
        def name(self) -> str:
            return "retry"

        async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
            request = httpx.Request("POST", "https://retry.example")
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)

        async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
            if False:
                yield
            return

    class _OkProvider(LLMProvider):
        def __init__(self) -> None:
            self.called = 0

        @property
        def name(self) -> str:
            return "ok"

        async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
            self.called += 1
            return AssistantMessage(content=[TextContent(text="ok")], model=model, provider="ok")

        async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
            yield AgentEvent(type="start")
            yield AgentEvent(type="done", stop_reason="stop")

    ok = _OkProvider()
    provider = ReliableProvider([_RetryProvider(), ok], max_retries=2, sleep_func=lambda _: asyncio.sleep(0))
    result = _run(provider.chat([UserMessage(content="hello")], model="test-model"))
    assert result.content[0].text == "ok"
    assert ok.called == 1


def test_reliable_provider_stream_attaches_generation_hint_on_first_event():
    class _OkProvider(LLMProvider):
        @property
        def name(self) -> str:
            return "ok"

        async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
            return AssistantMessage(content=[TextContent(text="ok")], model=model, provider="ok")

        async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
            yield AgentEvent(type="start")
            yield AgentEvent(type="done", stop_reason="stop")

    provider = ReliableProvider([_OkProvider()], max_retries=1, sleep_func=lambda _: asyncio.sleep(0))

    async def _collect():
        events = []
        async for event in provider.stream([UserMessage(content="hello")], model="gpt-4.1-mini"):
            events.append(event)
        return events

    events = _run(_collect())
    assert events
    assert events[0].message is not None
    assert events[0].message.provider == "ok"
    assert events[0].message.model == "gpt-4.1-mini"


def test_router_provider_routes_hint_and_passthrough_model():
    class _CaptureProvider(LLMProvider):
        def __init__(self, provider_name: str) -> None:
            self._provider_name = provider_name
            self.calls: list[str] = []

        @property
        def name(self) -> str:
            return self._provider_name

        async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
            self.calls.append(model)
            return AssistantMessage(content=[TextContent(text=model)], model=model, provider=self._provider_name)

        async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
            self.calls.append(model)
            yield AgentEvent(type="start")
            yield AgentEvent(type="done", stop_reason="stop")

    reasoning = _CaptureProvider("reasoning")
    default = _CaptureProvider("default")
    router = RouterProvider(
        {"reasoning": (reasoning, "claude-reasoning"), "fast": (default, "gpt-4o-mini")},
        default=(default, "claude-default"),
    )

    hinted = _run(router.chat([UserMessage(content="x")], model="hint:reasoning"))
    passthrough = _run(router.chat([UserMessage(content="y")], model="gpt-4.1"))
    fallback = _run(router.chat([UserMessage(content="z")], model="hint:unknown"))

    assert hinted.model == "claude-reasoning"
    assert passthrough.model == "gpt-4.1"
    assert fallback.model == "claude-default"
    assert reasoning.calls == ["claude-reasoning"]
    assert default.calls == ["gpt-4.1", "claude-default"]


def test_router_provider_stream_attaches_resolved_generation_metadata():
    class _CaptureProvider(LLMProvider):
        def __init__(self, provider_name: str) -> None:
            self._provider_name = provider_name

        @property
        def name(self) -> str:
            return self._provider_name

        async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
            return AssistantMessage(content=[TextContent(text=model)], model=model, provider=self._provider_name)

        async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
            yield AgentEvent(type="start")
            yield AgentEvent(type="done", stop_reason="stop")

    reasoning = _CaptureProvider("anthropic")
    default = _CaptureProvider("openai")
    router = RouterProvider(
        {"reasoning": (reasoning, "claude-sonnet-4-20250514")},
        default=(default, "gpt-4.1-mini"),
    )

    async def _collect():
        events = []
        async for event in router.stream([UserMessage(content="x")], model="hint:reasoning"):
            events.append(event)
        return events

    events = _run(_collect())
    assert events
    assert events[0].message is not None
    assert events[0].message.provider == "anthropic"
    assert events[0].message.model == "claude-sonnet-4-20250514"


# --- OAuth Detection & Header Tests ---


def test_anthropic_detect_oauth_sk_ant_oat_prefix():
    """sk-ant-oat* tokens should be detected as OAuth."""
    assert AnthropicProvider._detect_oauth("sk-ant-oat01-zF7_HH03KyFWn7D8yZqO") is True
    assert AnthropicProvider._detect_oauth("sk-ant-oat01-abc") is True
    assert AnthropicProvider._detect_oauth("  sk-ant-oat01-padded  ") is True


def test_anthropic_detect_oauth_jwt_like_shape():
    """Tokens with 2+ dots (JWT-like) should be detected as OAuth."""
    assert AnthropicProvider._detect_oauth("header.payload.signature") is True
    assert AnthropicProvider._detect_oauth("a.b.c.d") is True


def test_anthropic_detect_oauth_regular_api_key():
    """Regular API keys should NOT be detected as OAuth."""
    assert AnthropicProvider._detect_oauth("sk-ant-api03-abc123def456") is False
    assert AnthropicProvider._detect_oauth("sk-proj-abc123") is False
    assert AnthropicProvider._detect_oauth("test-key") is False
    assert AnthropicProvider._detect_oauth("") is False


def test_anthropic_oauth_headers_use_bearer_and_beta():
    """OAuth tokens should produce Authorization: Bearer + anthropic-beta header."""
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }
        )
    )
    provider = AnthropicProvider(
        api_key="sk-ant-oat01-zF7_HH03KyFWn7D8yZqOWNW7_test",
        base_url="https://anthropic.example",
        client_factory=lambda: fake_client,
    )
    _run(provider.chat([UserMessage(content="hi")], model="claude-sonnet-4-20250514"))

    headers = fake_client.post_calls[0]["headers"]
    assert headers["authorization"] == "Bearer sk-ant-oat01-zF7_HH03KyFWn7D8yZqOWNW7_test"
    assert headers["anthropic-beta"] == "oauth-2025-04-20"
    assert "x-api-key" not in headers


def test_anthropic_regular_key_headers_use_x_api_key():
    """Regular API keys should use x-api-key header, no Bearer or beta."""
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }
        )
    )
    provider = AnthropicProvider(
        api_key="sk-ant-api03-regular-key-not-oauth",
        base_url="https://anthropic.example",
        client_factory=lambda: fake_client,
    )
    _run(provider.chat([UserMessage(content="hi")], model="claude-sonnet-4-20250514"))

    headers = fake_client.post_calls[0]["headers"]
    assert headers["x-api-key"] == "sk-ant-api03-regular-key-not-oauth"
    assert "authorization" not in headers
    assert "anthropic-beta" not in headers


def test_anthropic_oauth_stream_headers():
    """OAuth should also work for streaming calls."""
    stream_lines = [
        'data: {"type":"message_start"}',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = AnthropicProvider(
        api_key="sk-ant-oat01-stream-test-token",
        base_url="https://anthropic.example",
        client_factory=lambda: fake_client,
    )

    async def _collect():
        events = []
        async for event in provider.stream([UserMessage(content="hi")], model="claude-sonnet-4-20250514"):
            events.append(event)
        return events

    _run(_collect())

    headers = fake_client.stream_calls[0]["headers"]
    assert headers["authorization"] == "Bearer sk-ant-oat01-stream-test-token"
    assert headers["anthropic-beta"] == "oauth-2025-04-20"
    assert "x-api-key" not in headers


def test_openai_message_format_supports_tool_result_message():
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "model": "gpt-4o-mini",
                "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
                "usage": {},
            }
        )
    )
    provider = OpenAIProvider(api_key="k", client_factory=lambda: fake_client)
    _run(
        provider.chat(
            [
                UserMessage(content="first"),
                ToolResultMessage(tool_call_id="call_1", tool_name="lookup", content="done"),
            ],
            model="gpt-4o-mini",
        )
    )
    sent_messages = fake_client.post_calls[0]["json"]["messages"]
    assert sent_messages[1]["role"] == "tool"
    assert sent_messages[1]["tool_call_id"] == "call_1"


# --- Anthropic Thinking Payload Tests ---


def test_anthropic_chat_sends_thinking_payload_when_budget_set():
    """Extended thinking should add thinking block and force temperature=1.0."""
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "thought"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }
        )
    )
    provider = AnthropicProvider(
        api_key="test-key",
        base_url="https://anthropic.example",
        client_factory=lambda: fake_client,
    )
    rc = ReasoningConfig(max_tokens=16384, thinking_budget=32000)
    _run(provider.chat([UserMessage(content="think hard")], model="claude-sonnet-4-20250514", reasoning_config=rc))

    payload = fake_client.post_calls[0]["json"]
    assert payload["max_tokens"] == 40192
    assert payload["temperature"] == 1.0
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 32000}

    headers = fake_client.post_calls[0]["headers"]
    assert "interleaved-thinking-2025-05-14" in headers["anthropic-beta"]


def test_anthropic_chat_no_thinking_when_budget_zero():
    """No thinking block or beta header when budget is 0."""
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "model": "claude-haiku-4-5-20251001",
                "content": [{"type": "text", "text": "fast"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 1},
            }
        )
    )
    provider = AnthropicProvider(
        api_key="test-key",
        base_url="https://anthropic.example",
        client_factory=lambda: fake_client,
    )
    rc = ReasoningConfig(max_tokens=4096, thinking_budget=None)
    _run(provider.chat([UserMessage(content="be fast")], model="claude-haiku-4-5-20251001", reasoning_config=rc, temperature=0.3))

    payload = fake_client.post_calls[0]["json"]
    assert payload["max_tokens"] == 4096
    assert payload["temperature"] == 0.3
    assert "thinking" not in payload

    headers = fake_client.post_calls[0]["headers"]
    assert "anthropic-beta" not in headers


def test_anthropic_oauth_with_thinking_combines_betas():
    """OAuth + thinking should produce both betas comma-separated."""
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }
        )
    )
    provider = AnthropicProvider(
        api_key="sk-ant-oat01-test-token",
        base_url="https://anthropic.example",
        client_factory=lambda: fake_client,
    )
    rc = ReasoningConfig(max_tokens=8192, thinking_budget=5000)
    _run(provider.chat([UserMessage(content="hi")], model="claude-sonnet-4-20250514", reasoning_config=rc))

    headers = fake_client.post_calls[0]["headers"]
    beta = headers["anthropic-beta"]
    assert "oauth-2025-04-20" in beta
    assert "interleaved-thinking-2025-05-14" in beta


# --- OpenAI Reasoning Effort Tests ---


def test_openai_chat_sends_reasoning_effort_and_max_tokens():
    """reasoning_effort and max_completion_tokens should appear in payload."""
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "model": "o3",
                "choices": [{"finish_reason": "stop", "message": {"content": "deep"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }
        )
    )
    provider = OpenAIProvider(
        api_key="test-key",
        base_url="https://openai.example/v1",
        client_factory=lambda: fake_client,
    )
    rc = ReasoningConfig(max_tokens=16384, reasoning_effort="high")
    _run(provider.chat([UserMessage(content="think")], model="o3", reasoning_config=rc))

    payload = fake_client.post_calls[0]["json"]
    assert payload["max_completion_tokens"] == 16384
    assert payload["reasoning_effort"] == "high"


def test_openai_chat_no_reasoning_effort_when_none():
    """No reasoning_effort key when not set."""
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "model": "gpt-4o-mini",
                "choices": [{"finish_reason": "stop", "message": {"content": "fast"}}],
                "usage": {},
            }
        )
    )
    provider = OpenAIProvider(
        api_key="test-key",
        client_factory=lambda: fake_client,
    )
    rc = ReasoningConfig(max_tokens=4096)
    _run(provider.chat([UserMessage(content="hi")], model="gpt-4o-mini", reasoning_config=rc))

    payload = fake_client.post_calls[0]["json"]
    assert payload["max_completion_tokens"] == 4096
    assert "reasoning_effort" not in payload


# --- TierProvider Tests ---


class _TierCaptureProvider(LLMProvider):
    """Captures model and reasoning_config for assertions."""
    def __init__(self, provider_name: str, *, fail_with: Exception | None = None) -> None:
        self._provider_name = provider_name
        self._fail_with = fail_with
        self.chat_calls: list[tuple[str, ReasoningConfig | None]] = []
        self.stream_calls: list[tuple[str, ReasoningConfig | None]] = []

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def provider_id(self) -> ProviderId | None:
        try:
            return ProviderId(self._provider_name)
        except ValueError:
            return None

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        self.chat_calls.append((model, reasoning_config))
        if self._fail_with:
            raise self._fail_with
        return AssistantMessage(content=[TextContent(text=f"{self._provider_name}:{model}")], model=model, provider=self._provider_name)

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        self.stream_calls.append((model, reasoning_config))
        if self._fail_with:
            raise self._fail_with
        yield AgentEvent(type="start")
        yield AgentEvent(type="done", stop_reason="stop")


def _make_tier_provider(
    primary: _TierCaptureProvider,
    fallback: _TierCaptureProvider | None = None,
) -> TierProvider:
    fast_rc = ReasoningConfig(max_tokens=4096)
    normal_rc = ReasoningConfig(max_tokens=8192, thinking_budget=5000)
    hard_rc = ReasoningConfig(max_tokens=16384, thinking_budget=32000)

    tiers = {
        TierName.FAST: TierConfig(
            primary=TierModelConfig(provider=primary, model="claude-haiku", reasoning_config=fast_rc, temperature=0.3),
            fallbacks=[TierModelConfig(provider=fallback, model="gpt-4o-mini", reasoning_config=fast_rc, temperature=0.3)] if fallback else [],
        ),
        TierName.NORMAL: TierConfig(
            primary=TierModelConfig(provider=primary, model="claude-sonnet", reasoning_config=normal_rc),
            fallbacks=[TierModelConfig(provider=fallback, model="gpt-4o", reasoning_config=normal_rc)] if fallback else [],
        ),
        TierName.HARD: TierConfig(
            primary=TierModelConfig(provider=primary, model="claude-sonnet", reasoning_config=hard_rc),
            fallbacks=[TierModelConfig(provider=fallback, model="o3", reasoning_config=hard_rc)] if fallback else [],
        ),
    }
    return TierProvider(
        tiers=tiers,
        default_tier=TierName.NORMAL,
        max_retries=1,
        sleep_func=lambda _: asyncio.sleep(0),
    )


def test_tier_provider_routes_fast_tier_to_fast_config():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)
    result = _run(tp.chat([UserMessage(content="x")], model="fast"))
    assert result.model == "claude-haiku"
    assert primary.chat_calls[0][0] == "claude-haiku"
    assert primary.chat_calls[0][1].max_tokens == 4096


def test_tier_provider_routes_hard_tier_to_hard_config():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)
    result = _run(tp.chat([UserMessage(content="x")], model="hard"))
    assert result.model == "claude-sonnet"
    rc = primary.chat_calls[0][1]
    assert rc.thinking_budget == 32000
    assert rc.max_tokens == 16384


def test_tier_provider_routes_normal_tier_to_default_config():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)
    _run(tp.chat([UserMessage(content="x")], model="normal"))
    assert primary.chat_calls[0][0] == "claude-sonnet"


def test_tier_provider_non_hint_model_passes_raw_model_to_default_provider():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)
    _run(tp.chat([UserMessage(content="x")], model="some-random-model"))
    assert primary.chat_calls[0][0] == "some-random-model"


def test_tier_provider_non_hint_model_routes_to_matching_provider_model():
    primary = _TierCaptureProvider("anthropic")
    fallback = _TierCaptureProvider("openai")
    tp = _make_tier_provider(primary, fallback)

    _run(tp.chat([UserMessage(content="x")], model="gpt-4o-mini"))
    assert len(primary.chat_calls) == 0
    assert fallback.chat_calls[0][0] == "gpt-4o-mini"


def test_tier_provider_non_hint_model_does_not_fallback_to_different_model():
    request = httpx.Request("POST", "https://primary.example")
    response = httpx.Response(429, request=request)
    err = httpx.HTTPStatusError("rate limited", request=request, response=response)

    primary = _TierCaptureProvider("anthropic", fail_with=err)
    fallback = _TierCaptureProvider("openai")
    tp = _make_tier_provider(primary, fallback)

    with pytest.raises(RuntimeError, match="All providers failed"):
        _run(tp.chat([UserMessage(content="x")], model="some-random-model"))
    assert len(fallback.chat_calls) == 0


def test_tier_provider_fallback_on_primary_failure():
    """When primary 429s, fallback should be tried with ITS OWN model."""
    request = httpx.Request("POST", "https://primary.example")
    response = httpx.Response(429, request=request)
    err = httpx.HTTPStatusError("rate limited", request=request, response=response)

    primary = _TierCaptureProvider("anthropic", fail_with=err)
    fallback = _TierCaptureProvider("openai")
    tp = _make_tier_provider(primary, fallback)

    result = _run(tp.chat([UserMessage(content="x")], model="fast"))
    assert result.provider == "openai"
    assert result.model == "gpt-4o-mini"
    assert fallback.chat_calls[0][0] == "gpt-4o-mini"


def test_tier_provider_available_tiers_returns_all_tiers():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)
    tiers = tp.available_tiers()
    tier_ids = {t.tier for t in tiers}
    assert tier_ids == {TierName.FAST, TierName.NORMAL, TierName.HARD}
    normal = next(t for t in tiers if t.tier == TierName.NORMAL)
    assert normal.primary_provider_id == ProviderId.ANTHROPIC
    assert normal.primary_model_id == "claude-sonnet"


def test_tier_provider_stream_routes_correctly():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)

    async def _collect():
        events = []
        async for event in tp.stream([UserMessage(content="x")], model="hard"):
            events.append(event)
        return events

    events = _run(_collect())
    assert any(e.type == "done" for e in events)
    assert primary.stream_calls[0][0] == "claude-sonnet"
    assert primary.stream_calls[0][1].thinking_budget == 32000


def test_tier_provider_stream_attaches_resolved_generation_metadata():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)

    async def _collect():
        events = []
        async for event in tp.stream([UserMessage(content="x")], model="normal"):
            events.append(event)
        return events

    events = _run(_collect())
    assert events
    assert events[0].message is not None
    assert events[0].message.provider == "anthropic"
    assert events[0].message.model == "claude-sonnet"


# --- Gemini Schema Cleaner Tests ---


def test_gemini_schema_cleaner_strips_keys():
    schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "format": "email",
                "pattern": "^[a-z]+$",
            },
            "count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 999,
                "exclusiveMinimum": 0,
                "multipleOf": 5,
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 10,
                "uniqueItems": True,
            },
        },
        "additionalProperties": False,
        "required": ["name"],
    }
    result = clean_schema_for_gemini(schema)
    assert "additionalProperties" not in result
    name_schema = result["properties"]["name"]
    for key in ("minLength", "maxLength", "format", "pattern"):
        assert key not in name_schema
    count_schema = result["properties"]["count"]
    for key in ("minimum", "maximum", "exclusiveMinimum", "multipleOf"):
        assert key not in count_schema
    tags_schema = result["properties"]["tags"]
    for key in ("minItems", "maxItems", "uniqueItems"):
        assert key not in tags_schema
    assert result["required"] == ["name"]


def test_gemini_schema_cleaner_resolves_refs():
    schema = {
        "type": "object",
        "$defs": {
            "Address": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
            }
        },
        "properties": {
            "home": {"$ref": "#/$defs/Address"},
        },
    }
    result = clean_schema_for_gemini(schema)
    home = result["properties"]["home"]
    assert home["type"] == "object"
    assert "city" in home["properties"]
    assert "$ref" not in home


def test_gemini_schema_cleaner_resolves_refs_cycle_detection():
    schema = {
        "type": "object",
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {
                    "child": {"$ref": "#/$defs/Node"},
                },
            },
        },
        "properties": {
            "root": {"$ref": "#/$defs/Node"},
        },
    }
    result = clean_schema_for_gemini(schema)
    root = result["properties"]["root"]
    assert root["type"] == "object"
    # The cyclic child should be broken (empty dict)
    assert root["properties"]["child"] == {}


def test_gemini_schema_cleaner_flattens_anyof():
    schema = {
        "anyOf": [
            {"type": "string", "const": "a"},
            {"type": "string", "const": "b"},
        ]
    }
    result = clean_schema_for_gemini(schema)
    assert result["type"] == "string"
    assert result["enum"] == ["a", "b"]
    assert "anyOf" not in result


def test_gemini_schema_cleaner_nullable_anyof():
    """anyOf with a null variant and a single real type should simplify to just the type."""
    schema = {
        "anyOf": [
            {"type": "string"},
            {"type": "null"},
        ]
    }
    result = clean_schema_for_gemini(schema)
    assert result.get("type") == "string"
    assert "anyOf" not in result


def test_gemini_schema_cleaner_converts_const():
    schema = {"const": "fixed_value"}
    result = clean_schema_for_gemini(schema)
    assert result == {"enum": ["fixed_value"]}


# --- Gemini Provider Tests ---


def test_gemini_chat_parses_tool_calls():
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {"text": "Let me search."},
                                {
                                    "functionCall": {
                                        "name": "search_web",
                                        "args": {"query": "arais"},
                                    },
                                    "thoughtSignature": "sig-search-1",
                                },
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20},
            }
        )
    )
    provider = GeminiProvider(
        api_key="test-gemini-key",
        base_url="https://gemini.example/v1beta",
        client_factory=lambda: fake_client,
    )
    tools = [ToolSchema(name="search_web", description="Search", parameters={"type": "object"})]
    result = _run(
        provider.chat(
            [
                SystemMessage(content="You are helpful."),
                UserMessage(content="find info"),
            ],
            model="gemini-2.5-flash",
            tools=tools,
        )
    )

    # Verify URL and headers
    post_call = fake_client.post_calls[0]
    assert post_call["url"] == "https://gemini.example/v1beta/models/gemini-2.5-flash:generateContent"
    assert post_call["headers"]["x-goog-api-key"] == "test-gemini-key"

    # Verify system instruction extracted
    payload = post_call["json"]
    assert payload["systemInstruction"]["parts"][0]["text"] == "You are helpful."

    # Verify functionDeclarations format
    assert "tools" in payload
    func_decls = payload["tools"][0]["functionDeclarations"]
    assert func_decls[0]["name"] == "search_web"
    assert payload["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"

    # Verify response parsing
    assert result.stop_reason == "tool_use"  # overridden from STOP
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "Let me search."
    assert isinstance(result.content[1], ToolCallContent)
    assert result.content[1].name == "search_web"
    assert result.content[1].arguments == {"query": "arais"}
    assert result.content[1].thought_signature == "sig-search-1"
    assert result.content[1].id.startswith("gemini_")
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 20


def test_gemini_formats_user_image_blocks():
    provider = GeminiProvider(api_key="test-key", base_url="https://gemini.example/v1beta")
    _, contents = provider._to_gemini_contents(  # type: ignore[attr-defined]
        [
            UserMessage(
                content=[
                    TextContent(text="Analyze image"),
                    ImageContent(media_type="image/webp", data="EEEFFFF"),
                ]
            )
        ]
    )
    assert contents[0]["role"] == "user"
    parts = contents[0]["parts"]
    assert parts[0]["text"] == "Analyze image"
    assert parts[1]["inlineData"]["mimeType"] == "image/webp"
    assert parts[1]["inlineData"]["data"] == "EEEFFFF"


def test_gemini_stream_events():
    stream_lines = [
        'data: {"candidates":[{"content":{"parts":[{"thought":true,"text":"hmm"}]},"finishReason":null}]}',
        'data: {"candidates":[{"content":{"parts":[{"text":"hello "}]},"finishReason":null}]}',
        'data: {"candidates":[{"content":{"parts":[{"text":"world"}]},"finishReason":null}]}',
        'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"lookup","args":{"q":"test"}},"thoughtSignature":"sig-lookup-1"}]},"finishReason":"STOP"}]}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = GeminiProvider(
        api_key="test-key",
        base_url="https://gemini.example/v1beta",
        client_factory=lambda: fake_client,
    )

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream([UserMessage(content="hello")], model="gemini-2.5-flash"):
            events.append(event)
        return events

    events = _run(_collect())
    event_types = [e.type for e in events]

    assert "start" in event_types
    assert "thinking_start" in event_types
    assert "thinking_delta" in event_types
    assert "thinking_end" in event_types
    assert "text_start" in event_types
    assert "text_delta" in event_types
    assert "text_end" in event_types
    assert "toolcall_start" in event_types
    assert "toolcall_delta" in event_types
    assert "toolcall_end" in event_types
    assert "done" in event_types

    # Check stream URL
    stream_call = fake_client.stream_calls[0]
    assert "streamGenerateContent?alt=sse" in stream_call["url"]

    # The done event should have stop_reason = tool_use (override)
    done_event = next(e for e in events if e.type == "done")
    assert done_event.stop_reason == "tool_use"
    tool_start = next(e for e in events if e.type == "toolcall_start")
    assert tool_start.tool_call is not None
    assert tool_start.tool_call.thought_signature == "sig-lookup-1"


def test_gemini_stream_done_keeps_tool_use_across_chunks():
    stream_lines = [
        'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"lookup","args":{"q":"test"}}}]},"finishReason":null}]}',
        'data: {"candidates":[{"content":{"parts":[]},"finishReason":"STOP"}]}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = GeminiProvider(
        api_key="test-key",
        base_url="https://gemini.example/v1beta",
        client_factory=lambda: fake_client,
    )

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream([UserMessage(content="hello")], model="gemini-2.5-flash"):
            events.append(event)
        return events

    events = _run(_collect())
    done_events = [e for e in events if e.type == "done"]
    assert len(done_events) == 1
    assert done_events[0].stop_reason == "tool_use"


def test_gemini_stream_raises_on_empty_stop_chunk():
    stream_lines = [
        'data: {"candidates":[{"content":{"parts":[]},"finishReason":"STOP"}]}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = GeminiProvider(
        api_key="test-key",
        base_url="https://gemini.example/v1beta",
        client_factory=lambda: fake_client,
    )

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream([UserMessage(content="hello")], model="gemini-2.5-flash"):
            events.append(event)
        return events

    try:
        _run(_collect())
        assert False, "Expected TransientProviderError"
    except TransientProviderError as exc:
        assert "finished without content" in str(exc)


def test_gemini_stream_raises_when_no_candidates_or_terminal():
    stream_lines = [
        'data: {"not_candidates": true}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = GeminiProvider(
        api_key="test-key",
        base_url="https://gemini.example/v1beta",
        client_factory=lambda: fake_client,
    )

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream([UserMessage(content="hello")], model="gemini-2.5-flash"):
            events.append(event)
        return events

    try:
        _run(_collect())
        assert False, "Expected TransientProviderError"
    except TransientProviderError as exc:
        assert "without candidates" in str(exc) or "without terminal content" in str(exc)


def test_gemini_tool_choice_required_sets_any_mode():
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "candidates": [
                    {"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {},
            }
        )
    )
    provider = GeminiProvider(
        api_key="test-key",
        base_url="https://gemini.example/v1beta",
        client_factory=lambda: fake_client,
    )
    _run(
        provider.chat(
            [UserMessage(content="run")],
            model="gemini-2.5-flash",
            tools=[ToolSchema(name="doit", description="Do it", parameters={"type": "object"})],
            tool_choice="required",
        )
    )

    payload = fake_client.post_calls[0]["json"]
    assert payload["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"


def test_gemini_thinking_config():
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "deep thought"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {},
            }
        )
    )
    provider = GeminiProvider(
        api_key="test-key",
        base_url="https://gemini.example/v1beta",
        client_factory=lambda: fake_client,
    )
    rc = ReasoningConfig(max_tokens=16384, thinking_budget=32000)
    _run(provider.chat([UserMessage(content="think")], model="gemini-2.5-pro", reasoning_config=rc))

    payload = fake_client.post_calls[0]["json"]
    assert payload["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 32000}


def test_gemini_no_thinking_config_when_zero():
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "fast"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {},
            }
        )
    )
    provider = GeminiProvider(
        api_key="test-key",
        base_url="https://gemini.example/v1beta",
        client_factory=lambda: fake_client,
    )
    rc = ReasoningConfig(max_tokens=4096, thinking_budget=None)
    _run(provider.chat([UserMessage(content="fast")], model="gemini-2.0-flash", reasoning_config=rc))

    payload = fake_client.post_calls[0]["json"]
    assert "thinkingConfig" not in payload["generationConfig"]


def test_gemini_tool_result_format():
    """ToolResultMessage should be converted to functionResponse."""
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Got it."}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {},
            }
        )
    )
    provider = GeminiProvider(
        api_key="test-key",
        base_url="https://gemini.example/v1beta",
        client_factory=lambda: fake_client,
    )
    _run(
        provider.chat(
            [
                UserMessage(content="search for arais"),
                AssistantMessage(
                    content=[
                        ToolCallContent(
                            id="gemini_abc",
                            name="search_web",
                            arguments={"q": "arais"},
                            thought_signature="sig-history-1",
                        )
                    ],
                    model="gemini-2.5-flash",
                    provider="gemini",
                    stop_reason="tool_use",
                ),
                ToolResultMessage(
                    tool_call_id="gemini_abc",
                    tool_name="search_web",
                    content="Found 3 results.",
                ),
            ],
            model="gemini-2.5-flash",
        )
    )

    payload = fake_client.post_calls[0]["json"]
    contents = payload["contents"]

    # User message
    assert contents[0]["role"] == "user"
    assert contents[0]["parts"][0]["text"] == "search for arais"

    # Assistant → model with functionCall
    assert contents[1]["role"] == "model"
    assert "functionCall" in contents[1]["parts"][0]
    assert contents[1]["parts"][0]["functionCall"]["name"] == "search_web"
    assert contents[1]["parts"][0]["thoughtSignature"] == "sig-history-1"

    # ToolResult → user with functionResponse
    assert contents[2]["role"] == "user"
    fr = contents[2]["parts"][0]["functionResponse"]
    assert fr["name"] == "search_web"
    assert fr["response"]["result"] == "Found 3 results."


def test_gemini_normalizes_dict_content_messages():
    """Compaction-style dict messages must be converted to Gemini parts format."""
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "ok"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {},
            }
        )
    )
    provider = GeminiProvider(
        api_key="test-key",
        base_url="https://gemini.example/v1beta",
        client_factory=lambda: fake_client,
    )
    _run(
        provider.chat(
            [
                {"role": "system", "content": "Return valid JSON only."},
                {"role": "user", "content": "Summarize this conversation."},
            ],
            model="gemini-2.0-flash",
        )
    )

    payload = fake_client.post_calls[0]["json"]
    assert payload["systemInstruction"]["parts"][0]["text"] == "Return valid JSON only."
    assert payload["contents"][0]["role"] == "user"
    assert payload["contents"][0]["parts"][0]["text"] == "Summarize this conversation."
    assert "content" not in payload["contents"][0]


def test_gemini_function_call_turn_serialization_is_strict():
    """Gemini payload should keep functionCall turns clean and skip orphan leading model calls."""
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "done"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {},
            }
        )
    )
    provider = GeminiProvider(
        api_key="test-key",
        base_url="https://gemini.example/v1beta",
        client_factory=lambda: fake_client,
    )
    _run(
        provider.chat(
            [
                # Orphan tool-call history from a truncated context window should be dropped.
                AssistantMessage(
                    content=[ToolCallContent(id="orphan", name="search_web", arguments={"q": "old"})],
                    model="gemini-2.5-flash",
                    provider="gemini",
                    stop_reason="tool_use",
                ),
                ToolResultMessage(
                    tool_call_id="orphan",
                    tool_name="search_web",
                    content="old result",
                ),
                UserMessage(content="Do a fresh lookup"),
                AssistantMessage(
                    # Mixed text + tool call should serialize as functionCall-only turn.
                    content=[
                        TextContent(text="I'll call a tool now."),
                        ToolCallContent(id="c1", name="search_web", arguments={"q": "fresh"}),
                    ],
                    model="gemini-2.5-flash",
                    provider="gemini",
                    stop_reason="tool_use",
                ),
                ToolResultMessage(
                    tool_call_id="c1",
                    tool_name="search_web",
                    content="fresh result",
                ),
            ],
            model="gemini-2.5-flash",
        )
    )

    contents = fake_client.post_calls[0]["json"]["contents"]
    assert contents[0]["role"] == "user"
    assert contents[0]["parts"][0]["text"] == "Do a fresh lookup"
    assert contents[1]["role"] == "model"
    assert "functionCall" in contents[1]["parts"][0]
    assert all("text" not in part for part in contents[1]["parts"])
    assert contents[2]["role"] == "user"
    assert "functionResponse" in contents[2]["parts"][0]


def test_gemini_keeps_all_function_responses_after_multi_tool_call():
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "done"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {},
            }
        )
    )
    provider = GeminiProvider(
        api_key="test-key",
        base_url="https://gemini.example/v1beta",
        client_factory=lambda: fake_client,
    )
    _run(
        provider.chat(
            [
                UserMessage(content="Do both"),
                AssistantMessage(
                    content=[
                        ToolCallContent(id="c1", name="tool_a", arguments={"a": 1}),
                        ToolCallContent(id="c2", name="tool_b", arguments={"b": 2}),
                    ],
                    model="gemini-2.5-flash",
                    provider="gemini",
                    stop_reason="tool_use",
                ),
                ToolResultMessage(tool_call_id="c1", tool_name="tool_a", content="result-a"),
                ToolResultMessage(tool_call_id="c2", tool_name="tool_b", content="result-b"),
            ],
            model="gemini-2.5-flash",
        )
    )

    contents = fake_client.post_calls[0]["json"]["contents"]
    assert contents[2]["role"] == "user"
    function_responses = [
        part["functionResponse"]["name"]
        for part in contents[2]["parts"]
        if isinstance(part, dict) and isinstance(part.get("functionResponse"), dict)
    ]
    assert function_responses == ["tool_a", "tool_b"]


def test_gemini_schema_cleaner_applied_to_tool_parameters():
    """Tool schemas should have additionalProperties stripped when sent to Gemini."""
    fake_client = _FakeAsyncClient(
        post_response=_FakeResponse(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "ok"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {},
            }
        )
    )
    provider = GeminiProvider(
        api_key="test-key",
        base_url="https://gemini.example/v1beta",
        client_factory=lambda: fake_client,
    )
    tools = [
        ToolSchema(
            name="my_tool",
            description="A tool",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string", "minLength": 1}},
                "additionalProperties": False,
            },
        )
    ]
    _run(provider.chat([UserMessage(content="go")], model="gemini-2.0-flash", tools=tools))

    payload = fake_client.post_calls[0]["json"]
    params = payload["tools"][0]["functionDeclarations"][0]["parameters"]
    assert "additionalProperties" not in params
    assert "minLength" not in params["properties"]["x"]


def test_codex_formats_user_image_blocks():
    provider = CodexProvider(oauth_token="test-token")
    _, input_items = provider._to_codex_input(  # type: ignore[attr-defined]
        [
            UserMessage(
                content=[
                    TextContent(text="Read screenshot"),
                    ImageContent(media_type="image/png", data="GGGHHH"),
                ]
            )
        ]
    )
    assert input_items[0]["role"] == "user"
    content = input_items[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "input_text"
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"] == "data:image/png;base64,GGGHHH"


def test_codex_stream_emits_text_from_output_item_done_when_no_delta():
    stream_lines = [
        'data: {"type":"response.created"}',
        'data: {"type":"response.output_item.done","output_index":0,"item":{"type":"message","role":"assistant","id":"msg_1","content":[{"type":"output_text","text":"Hello from done."}]}}',
        'data: {"type":"response.completed","response":{"status":"completed","output":[]}}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = CodexProvider(oauth_token="test-token", client_factory=lambda: fake_client)

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream(
            [UserMessage(content="Say hello")],
            model="gpt-5.3-codex-spark",
        ):
            events.append(event)
        return events

    events = _run(_collect())
    assert [event.delta for event in events if event.type == "text_delta"] == ["Hello from done."]
    assert any(event.type == "text_start" for event in events)
    assert any(event.type == "text_end" for event in events)
    assert events[-1].type == "done"
    assert events[-1].stop_reason == "stop"


def test_codex_stream_does_not_duplicate_text_when_delta_and_output_item_done_both_present():
    stream_lines = [
        'data: {"type":"response.created"}',
        'data: {"type":"response.output_text.delta","content_index":0,"delta":"Hello"}',
        'data: {"type":"response.output_item.done","output_index":0,"item":{"type":"message","role":"assistant","id":"msg_2","content":[{"type":"output_text","text":"Hello world"}]}}',
        'data: {"type":"response.completed","response":{"status":"completed","output":[]}}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = CodexProvider(oauth_token="test-token", client_factory=lambda: fake_client)

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream(
            [UserMessage(content="Say hello")],
            model="gpt-5.3-codex-spark",
        ):
            events.append(event)
        return events

    events = _run(_collect())
    assert [event.delta for event in events if event.type == "text_delta"] == ["Hello"]
    assert len([event for event in events if event.type == "text_end"]) == 1
    assert events[-1].type == "done"
    assert events[-1].stop_reason == "stop"


def test_codex_stream_emits_tool_arguments_from_function_call_done():
    stream_lines = [
        'data: {"type":"response.created"}',
        'data: {"type":"response.output_item.added","output_index":1,"item":{"type":"function_call","id":"fc_1","call_id":"call_1","name":"runtime_exec","arguments":""}}',
        'data: {"type":"response.function_call_arguments.done","output_index":1,"item_id":"fc_1","arguments":"{\\"command\\":\\"echo hello\\"}"}',
        'data: {"type":"response.output_item.done","output_index":1,"item":{"type":"function_call","id":"fc_1","call_id":"call_1","name":"runtime_exec","arguments":"{\\"command\\":\\"echo hello\\"}"}}',
        'data: {"type":"response.completed","response":{"output":[{"type":"function_call"}]}}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = CodexProvider(oauth_token="test-token", client_factory=lambda: fake_client)

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream(
            [UserMessage(content="Run command")],
            model="gpt-5.3-codex-spark",
            tools=[ToolSchema(name="runtime_exec", description="Run shell", parameters={"type": "object"})],
        ):
            events.append(event)
        return events

    events = _run(_collect())
    deltas = [event.delta for event in events if event.type == "toolcall_delta"]
    assert deltas == ['{"command":"echo hello"}']
    assert events[-1].type == "done"
    assert events[-1].stop_reason == "tool_use"


def test_codex_stream_emits_tool_arguments_from_output_item_done_when_no_delta():
    stream_lines = [
        'data: {"type":"response.created"}',
        'data: {"type":"response.output_item.added","output_index":2,"item":{"type":"function_call","id":"fc_2","call_id":"call_2","name":"runtime_exec","arguments":""}}',
        'data: {"type":"response.function_call_arguments.done","output_index":2,"item_id":"fc_2"}',
        'data: {"type":"response.output_item.done","output_index":2,"item":{"type":"function_call","id":"fc_2","call_id":"call_2","name":"runtime_exec","arguments":"{\\"command\\":\\"pwd\\"}"}}',
        'data: {"type":"response.completed","response":{"output":[{"type":"function_call"}]}}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = CodexProvider(oauth_token="test-token", client_factory=lambda: fake_client)

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream(
            [UserMessage(content="Run command")],
            model="gpt-5.3-codex-spark",
            tools=[ToolSchema(name="runtime_exec", description="Run shell", parameters={"type": "object"})],
        ):
            events.append(event)
        return events

    events = _run(_collect())
    deltas = [event.delta for event in events if event.type == "toolcall_delta"]
    assert deltas == ['{"command":"pwd"}']
    assert events[-1].type == "done"
    assert events[-1].stop_reason == "tool_use"


def test_codex_stream_emits_tool_arguments_when_present_on_output_item_added():
    stream_lines = [
        'data: {"type":"response.created"}',
        'data: {"type":"response.output_item.added","output_index":0,"item":{"type":"function_call","id":"fc_3","call_id":"call_3","name":"runtime_exec","arguments":"{\\"command\\":\\"ls\\"}"}}',
        'data: {"type":"response.output_item.done","output_index":0,"item":{"type":"function_call","id":"fc_3","call_id":"call_3","name":"runtime_exec","arguments":"{\\"command\\":\\"ls\\"}"}}',
        'data: {"type":"response.completed","response":{"output":[{"type":"function_call"}]}}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = CodexProvider(oauth_token="test-token", client_factory=lambda: fake_client)

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream(
            [UserMessage(content="Run command")],
            model="gpt-5.3-codex-spark",
            tools=[ToolSchema(name="runtime_exec", description="Run shell", parameters={"type": "object"})],
        ):
            events.append(event)
        return events

    events = _run(_collect())
    deltas = [event.delta for event in events if event.type == "toolcall_delta"]
    assert deltas == ['{"command":"ls"}']
    assert events[-1].type == "done"
    assert events[-1].stop_reason == "tool_use"


def test_codex_stream_payload_includes_parity_fields_and_prompt_cache_key():
    stream_lines = [
        'data: {"type":"response.created"}',
        'data: {"type":"response.completed","response":{"status":"completed","output":[]}}',
    ]
    fake_client = _FakeAsyncClient(
        stream_response=_FakeStreamResponse(stream_lines),
        get_response=_FakeResponse(
            {
                "models": [
                    {"slug": "gpt-5.3-codex", "supports_parallel_tool_calls": False},
                ]
            }
        ),
    )
    provider = CodexProvider(oauth_token="test-token", client_factory=lambda: fake_client)
    tools = [ToolSchema(name="runtime_exec", description="Run shell", parameters={"type": "object"})]
    rc = ReasoningConfig(reasoning_effort="high")

    async def _run_once():
        async for _ in provider.stream(
            [UserMessage(content="Run command")],
            model="gpt-5.3-codex",
            tools=tools,
            reasoning_config=rc,
            tool_choice="required",
        ):
            pass

    _run(_run_once())
    _run(_run_once())

    first_payload = fake_client.stream_calls[0]["json"]
    second_payload = fake_client.stream_calls[1]["json"]

    assert first_payload["tool_choice"] == "required"
    assert first_payload["parallel_tool_calls"] is False
    assert "temperature" not in first_payload
    assert "max_output_tokens" not in first_payload
    assert "You are Codex, a coding agent running in Sentinel." in first_payload["instructions"]
    assert "Keep acting until the task is complete." in first_payload["instructions"]
    assert first_payload["reasoning"] == {"effort": "high"}
    assert first_payload["include"] == ["reasoning.encrypted_content"]
    assert isinstance(first_payload.get("prompt_cache_key"), str)
    assert first_payload["prompt_cache_key"] == second_payload["prompt_cache_key"]


def test_codex_stream_sanitizes_nested_object_tool_schemas():
    stream_lines = [
        'data: {"type":"response.created"}',
        'data: {"type":"response.completed","response":{"status":"completed","output":[]}}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = CodexProvider(oauth_token="test-token", client_factory=lambda: fake_client)
    raw_parameters = {
        "type": "object",
        "additionalProperties": False,
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
            "method": {"type": "string"},
            "query": {"type": "object"},
            "headers": {"type": "object"},
            "body": {"type": "object"},
        },
    }
    tools = [
        ToolSchema(
            name="araios_modules",
            description="Call AraiOS",
            parameters=raw_parameters,
        )
    ]

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream(
            [UserMessage(content="Create task")],
            model="gpt-5.3-codex",
            tools=tools,
        ):
            events.append(event)
        return events

    _run(_collect())
    payload = fake_client.stream_calls[0]["json"]
    params = payload["tools"][0]["parameters"]
    assert params["properties"]["query"]["properties"] == {}
    assert params["properties"]["headers"]["properties"] == {}
    assert params["properties"]["body"]["properties"] == {}
    # Original tool schema should not be mutated in place.
    assert "properties" not in raw_parameters["properties"]["body"]


def test_codex_stream_keeps_additional_properties_boolean_in_nested_object_schema():
    stream_lines = [
        'data: {"type":"response.created"}',
        'data: {"type":"response.completed","response":{"status":"completed","output":[]}}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = CodexProvider(oauth_token="test-token", client_factory=lambda: fake_client)
    tools = [
        ToolSchema(
            name="custom_tool",
            description="Custom tool",
            parameters={
                "type": "object",
                "properties": {
                    "config": {
                        "type": "object",
                        "additionalProperties": False,
                    }
                },
            },
        )
    ]

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream(
            [UserMessage(content="run")],
            model="gpt-5.3-codex",
            tools=tools,
        ):
            events.append(event)
        return events

    _run(_collect())
    payload = fake_client.stream_calls[0]["json"]
    params = payload["tools"][0]["parameters"]
    assert params["properties"]["config"]["additionalProperties"] is False
    assert params["properties"]["config"]["properties"] == {}


def test_codex_stream_raises_on_sse_error_event():
    stream_lines = [
        "event: error",
        'data: {"error":{"message":"Overloaded"}}',
    ]
    fake_client = _FakeAsyncClient(stream_response=_FakeStreamResponse(stream_lines))
    provider = CodexProvider(oauth_token="test-token", client_factory=lambda: fake_client)

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream(
            [UserMessage(content="Run command")],
            model="gpt-5.3-codex",
            tools=[ToolSchema(name="runtime_exec", description="Run shell", parameters={"type": "object"})],
        ):
            events.append(event)
        return events

    with pytest.raises(RuntimeError, match="Overloaded"):
        _run(_collect())


def test_codex_stream_keeps_requested_model_when_catalog_does_not_include_it():
    stream_lines = [
        'data: {"type":"response.created"}',
        'data: {"type":"response.completed","response":{"status":"completed","output":[]}}',
    ]
    fake_client = _FakeAsyncClient(
        stream_response=_FakeStreamResponse(stream_lines),
        get_response=_FakeResponse(
            {
                "models": [
                    {"slug": "gpt-5.2-codex", "supports_parallel_tool_calls": False},
                    {"slug": "gpt-5.1-codex", "supports_parallel_tool_calls": False},
                ]
            }
        ),
    )
    provider = CodexProvider(oauth_token="test-token", client_factory=lambda: fake_client)

    async def _collect():
        events: list[AgentEvent] = []
        async for event in provider.stream(
            [UserMessage(content="Run command")],
            model="gpt-5.3-codex",
            tools=[ToolSchema(name="runtime_exec", description="Run shell", parameters={"type": "object"})],
        ):
            events.append(event)
        return events

    _run(_collect())
    payload = fake_client.stream_calls[0]["json"]
    assert payload["model"] == "gpt-5.3-codex"
    assert payload["parallel_tool_calls"] is False


def test_codex_execution_prelude_not_duplicated_when_already_present():
    provider = CodexProvider(oauth_token="test-token")
    instructions = (
        "You are Codex, a coding agent running in Sentinel.\n\n"
        "Keep acting until the task is complete.\n\n"
        "User-specific system guidance."
    )
    rendered = provider._with_codex_execution_prelude(instructions)  # type: ignore[attr-defined]
    assert rendered == instructions
