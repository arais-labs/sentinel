from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

import httpx

from app.services.llm.anthropic_provider import AnthropicProvider
from app.services.llm.base import LLMProvider
from app.services.llm.gemini_provider import GeminiProvider
from app.services.llm.gemini_schema_cleaner import clean_schema_for_gemini
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.reliable_provider import ReliableProvider
from app.services.llm.router_provider import RouterProvider
from app.services.llm.tier_provider import TierConfig, TierModelConfig, TierProvider
from app.services.llm.types import (
    AgentEvent,
    AssistantMessage,
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
    def __init__(self, *, post_response: _FakeResponse | None = None, stream_response: _FakeStreamResponse | None = None):
        self.post_response = post_response or _FakeResponse({})
        self.stream_response = stream_response or _FakeStreamResponse([])
        self.post_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, *, json: dict, headers: dict[str, str]):
        self.post_calls.append({"url": url, "json": json, "headers": headers})
        return self.post_response

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
        'data: {"type":"error","error":{"message":"boom"}}',
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
        "error",
    }


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


def test_reliable_provider_fallback_on_429():
    class _RetryProvider(LLMProvider):
        @property
        def name(self) -> str:
            return "retry"

        async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None):
            request = httpx.Request("POST", "https://retry.example")
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)

        async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None):
            if False:
                yield
            return

    class _OkProvider(LLMProvider):
        def __init__(self) -> None:
            self.called = 0

        @property
        def name(self) -> str:
            return "ok"

        async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None):
            self.called += 1
            return AssistantMessage(content=[TextContent(text="ok")], model=model, provider="ok")

        async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None):
            yield AgentEvent(type="start")
            yield AgentEvent(type="done", stop_reason="stop")

    ok = _OkProvider()
    provider = ReliableProvider([_RetryProvider(), ok], max_retries=2, sleep_func=lambda _: asyncio.sleep(0))
    result = _run(provider.chat([UserMessage(content="hello")], model="test-model"))
    assert result.content[0].text == "ok"
    assert ok.called == 1


def test_router_provider_routes_hint_and_passthrough_model():
    class _CaptureProvider(LLMProvider):
        def __init__(self, provider_name: str) -> None:
            self._provider_name = provider_name
            self.calls: list[str] = []

        @property
        def name(self) -> str:
            return self._provider_name

        async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None):
            self.calls.append(model)
            return AssistantMessage(content=[TextContent(text=model)], model=model, provider=self._provider_name)

        async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None):
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
    assert payload["max_tokens"] == 16384
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

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None):
        self.chat_calls.append((model, reasoning_config))
        if self._fail_with:
            raise self._fail_with
        return AssistantMessage(content=[TextContent(text=f"{self._provider_name}:{model}")], model=model, provider=self._provider_name)

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None):
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
        "fast": TierConfig(
            primary=TierModelConfig(provider=primary, model="claude-haiku", reasoning_config=fast_rc, temperature=0.3),
            fallbacks=[TierModelConfig(provider=fallback, model="gpt-4o-mini", reasoning_config=fast_rc, temperature=0.3)] if fallback else [],
        ),
        "normal": TierConfig(
            primary=TierModelConfig(provider=primary, model="claude-sonnet", reasoning_config=normal_rc),
            fallbacks=[TierModelConfig(provider=fallback, model="gpt-4o", reasoning_config=normal_rc)] if fallback else [],
        ),
        "hard": TierConfig(
            primary=TierModelConfig(provider=primary, model="claude-sonnet", reasoning_config=hard_rc),
            fallbacks=[TierModelConfig(provider=fallback, model="o3", reasoning_config=hard_rc)] if fallback else [],
        ),
    }
    return TierProvider(tiers=tiers, default_tier="normal", max_retries=1, sleep_func=lambda _: asyncio.sleep(0))


def test_tier_provider_routes_hint_fast_to_fast_tier():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)
    result = _run(tp.chat([UserMessage(content="x")], model="hint:fast"))
    assert result.model == "claude-haiku"
    assert primary.chat_calls[0][0] == "claude-haiku"
    assert primary.chat_calls[0][1].max_tokens == 4096


def test_tier_provider_routes_hint_hard_to_hard_tier():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)
    result = _run(tp.chat([UserMessage(content="x")], model="hint:hard"))
    assert result.model == "claude-sonnet"
    rc = primary.chat_calls[0][1]
    assert rc.thinking_budget == 32000
    assert rc.max_tokens == 16384


def test_tier_provider_backward_compat_reasoning_maps_to_normal():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)
    result = _run(tp.chat([UserMessage(content="x")], model="hint:reasoning"))
    assert result.model == "claude-sonnet"
    assert primary.chat_calls[0][1].thinking_budget == 5000


def test_tier_provider_backward_compat_anthropic_maps_to_normal():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)
    _run(tp.chat([UserMessage(content="x")], model="hint:anthropic"))
    assert primary.chat_calls[0][0] == "claude-sonnet"


def test_tier_provider_unknown_hint_falls_back_to_default_tier():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)
    _run(tp.chat([UserMessage(content="x")], model="hint:unknown"))
    assert primary.chat_calls[0][0] == "claude-sonnet"


def test_tier_provider_non_hint_model_uses_default_tier():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)
    _run(tp.chat([UserMessage(content="x")], model="some-random-model"))
    # Uses default tier (normal), so model resolved is claude-sonnet
    assert primary.chat_calls[0][0] == "claude-sonnet"


def test_tier_provider_fallback_on_primary_failure():
    """When primary 429s, fallback should be tried with ITS OWN model."""
    request = httpx.Request("POST", "https://primary.example")
    response = httpx.Response(429, request=request)
    err = httpx.HTTPStatusError("rate limited", request=request, response=response)

    primary = _TierCaptureProvider("anthropic", fail_with=err)
    fallback = _TierCaptureProvider("openai")
    tp = _make_tier_provider(primary, fallback)

    result = _run(tp.chat([UserMessage(content="x")], model="hint:fast"))
    assert result.provider == "openai"
    assert result.model == "gpt-4o-mini"
    assert fallback.chat_calls[0][0] == "gpt-4o-mini"


def test_tier_provider_available_tiers_returns_all_tiers():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)
    tiers = tp.available_tiers()
    tier_ids = {t["id"] for t in tiers}
    assert tier_ids == {"hint:fast", "hint:normal", "hint:hard"}
    normal = next(t for t in tiers if t["id"] == "hint:normal")
    assert normal["primary_provider"] == "anthropic"
    assert normal["primary_model"] == "claude-sonnet"


def test_tier_provider_stream_routes_correctly():
    primary = _TierCaptureProvider("anthropic")
    tp = _make_tier_provider(primary)

    async def _collect():
        events = []
        async for event in tp.stream([UserMessage(content="x")], model="hint:hard"):
            events.append(event)
        return events

    events = _run(_collect())
    assert any(e.type == "done" for e in events)
    assert primary.stream_calls[0][0] == "claude-sonnet"
    assert primary.stream_calls[0][1].thinking_budget == 32000


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
                                    }
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

    # Verify response parsing
    assert result.stop_reason == "tool_use"  # overridden from STOP
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "Let me search."
    assert isinstance(result.content[1], ToolCallContent)
    assert result.content[1].name == "search_web"
    assert result.content[1].arguments == {"query": "arais"}
    assert result.content[1].id.startswith("gemini_")
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 20


def test_gemini_stream_events():
    stream_lines = [
        'data: {"candidates":[{"content":{"parts":[{"thought":true,"text":"hmm"}]},"finishReason":null}]}',
        'data: {"candidates":[{"content":{"parts":[{"text":"hello "}]},"finishReason":null}]}',
        'data: {"candidates":[{"content":{"parts":[{"text":"world"}]},"finishReason":null}]}',
        'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"lookup","args":{"q":"test"}}}]},"finishReason":"STOP"}]}',
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
                    content=[ToolCallContent(id="gemini_abc", name="search_web", arguments={"q": "arais"})],
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
