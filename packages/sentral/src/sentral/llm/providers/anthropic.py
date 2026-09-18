"""Anthropic Messages API provider implementation."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from copy import deepcopy
from typing import Any

import httpx

from sentral.llm.generic.base import LLMProvider
from sentral.llm.generic.types import (
    AgentEvent,
    AgentMessage,
    AssistantMessage,
    ImageContent,
    ReasoningConfig,
    TextContent,
    ThinkingContent,
    TokenUsage,
    ToolCallContent,
    ToolResultMessage,
    ToolSchema,
)
from sentral.llm.http_pool import provider_http_client
from sentral.llm.ids import ProviderId
from sentral.llm.pricing import claude_token_price
from sentral.llm.providers.fast_mode import matches_model

logger = logging.getLogger(__name__)

_OAUTH_BASE_BETAS = [
    "claude-code-20250219",
    "oauth-2025-04-20",
    "interleaved-thinking-2025-05-14",
    "prompt-caching-scope-2026-01-05",
    "context-management-2025-06-27",
]
_CLAUDE_CODE_VERSION = "2.1.263"
_CLAUDE_CODE_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API adapter with streaming event translation."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.anthropic.com",
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        renew_credentials: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        self._api_key = api_key
        self._credential_renewer = renew_credentials
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory or provider_http_client
        self._is_oauth = self._detect_oauth(api_key)

    @staticmethod
    def _detect_oauth(token: str) -> bool:
        trimmed = token.strip()
        if trimmed.startswith("sk-ant-oat"):
            return True
        if trimmed.count(".") >= 2:
            return True
        return False

    @property
    def name(self) -> str:
        return ProviderId.ANTHROPIC

    @property
    def provider_id(self) -> ProviderId:
        return ProviderId.ANTHROPIC

    async def get_account_usage(self) -> dict[str, Any]:
        """Read subscription quotas without making an inference request."""
        if not self._is_oauth:
            raise ValueError("Account usage requires OAuth.")
        async with self._client_factory() as client:
            response = await client.get(
                f"{self._base_url}/api/oauth/usage", headers=self._headers()
            )
        response.raise_for_status()
        return response.json()

    async def _renew_credentials(self):

        self._api_key = await self._credential_renewer(self._api_key)

    async def chat(
        self,
        messages,
        model="claude-opus-5",
        tools=None,
        temperature=0.7,
        reasoning_config=None,
        tool_choice=None,
    ):
        for attempt in range(2):
            try:
                return await self._chat_once(
                    messages, model, tools, temperature, reasoning_config, tool_choice
                )
            except httpx.HTTPStatusError as error:
                if (
                    attempt
                    or not self._is_oauth
                    or self._credential_renewer is None
                    or error.response.status_code != 401
                ):
                    raise
                await self._renew_credentials()

    async def stream(
        self,
        messages,
        model="claude-opus-5",
        tools=None,
        temperature=0.7,
        reasoning_config=None,
        tool_choice=None,
    ):
        for attempt in range(2):
            visible = False
            try:
                async for event in self._stream_once(
                    messages, model, tools, temperature, reasoning_config, tool_choice
                ):
                    visible = True
                    yield event
                return
            except httpx.HTTPStatusError as error:
                if (
                    visible
                    or attempt
                    or not self._is_oauth
                    or self._credential_renewer is None
                    or error.response.status_code != 401
                ):
                    raise
                await self._renew_credentials()

    async def _chat_once(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str = "claude-opus-5",
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AssistantMessage:
        payload = self._payload(messages, model, tools, reasoning_config, tool_choice)

        async with self._client_factory() as client:
            response = await client.post(
                f"{self._base_url}/v1/messages",
                json=payload,
                headers=self._headers(model=model, fast_mode=payload.get("speed") == "fast"),
            )
        response.raise_for_status()

        return self._message(response.json(), model)

    def supports_fast_mode(self, model: str) -> bool:

        # https://platform.claude.com/docs/en/build-with-claude/fast-mode
        return self._base_url == "https://api.anthropic.com" and matches_model(
            model, ("claude-opus-5", "claude-opus-4-8")
        )

    def _payload(self, messages, model, tools, reasoning_config, tool_choice=None):
        payload = {
            "model": model or "claude-opus-5",
            "messages": self._to_anthropic_messages(messages),
            **self._generation_options(reasoning_config, model or "claude-opus-5"),
            "cache_control": {"type": "ephemeral"},
        }
        system_prompt = self._extract_system_prompt(messages)
        if self._is_oauth:
            system_prompt = self._ensure_claude_code_system_prompt(system_prompt)
        if system_prompt:
            payload["system"] = system_prompt
        if tools:
            payload["tools"] = [self._tool_schema(tool) for tool in tools]

        # Subscription transports do not advertise the public hosted-tool contract.
        if (
            not self._is_oauth
            and self._base_url == "https://api.anthropic.com"
            and model.startswith(("claude-opus-5", "claude-sonnet-5", "claude-fable-5"))
            and tool_choice in {None, "auto"}
        ):
            eligible = [
                tool
                for tool in payload.get("tools", [])
                if tool["name"] not in {"form", "sub_agents"}
                and '"$ref"' not in json.dumps(tool.get("input_schema", {}))
            ]
            if eligible:
                for tool in eligible:
                    tool["allowed_callers"] = ["direct", "code_execution_20260120"]
                payload["tools"].append(
                    {"type": "code_execution_20260120", "name": "code_execution"}
                )
        for message in reversed(messages):
            if isinstance(message, AssistantMessage) and message.provider == self.name:
                container = (message.provider_usage or {}).get("container")
                if container and any(
                    block.get("caller", {}).get("type", "direct") != "direct"
                    for block in message.responses_output
                ):
                    payload["container"] = container["id"]
                break
        if tool_choice:
            choice = {"type": {"required": "any"}.get(tool_choice, tool_choice)}
            if tool_choice not in {"auto", "none", "required", "any"}:
                choice = {"type": "tool", "name": tool_choice}
            payload["tool_choice"] = choice
            # Forced selection cannot be combined with extended thinking.
            if choice["type"] in {"any", "tool"}:
                payload.pop("thinking", None)
                payload.pop("output_config", None)

        if reasoning_config and reasoning_config.fast_mode:
            if not self.supports_fast_mode(model):
                raise ValueError("Fast mode is not supported by this provider and model")
            payload["speed"] = "fast"
        return payload

    async def count_input_tokens(self, messages, model, tools=None, reasoning_config=None):
        for attempt in range(2):
            try:
                return await self._count_input_tokens_once(messages, model, tools, reasoning_config)
            except httpx.HTTPStatusError as error:
                if (
                    attempt
                    or not self._is_oauth
                    or self._credential_renewer is None
                    or error.response.status_code != 401
                ):
                    raise
                await self._renew_credentials()

    async def _count_input_tokens_once(self, messages, model, tools=None, reasoning_config=None):
        payload = self._payload(messages, model, tools, reasoning_config)
        payload.pop("max_tokens", None)
        payload.pop("speed", None)
        payload.pop("output_config", None)
        payload.pop("cache_control", None)
        async with self._client_factory() as client:
            response = await client.post(
                f"{self._base_url}/v1/messages/count_tokens",
                json=payload,
                headers=self._headers(model=model, fast_mode=payload.get("speed") == "fast"),
            )
            response.raise_for_status()
            return int(response.json()["input_tokens"])

    def _message(self, data, model):
        raw = data.get("usage")
        usage = raw or {}
        input_total = sum(
            int(usage.get(key) or 0)
            for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        )
        output_total = int(usage.get("output_tokens") or 0)
        snapshot = None
        if isinstance(raw, dict):
            normalized = {
                "input_tokens": input_total,
                "input_tokens_details": {
                    "cached_tokens": usage.get("cache_read_input_tokens"),
                    "cache_write_tokens": usage.get("cache_creation_input_tokens"),
                    "cache_creation": deepcopy(usage.get("cache_creation")),
                },
                "output_tokens": usage.get("output_tokens"),
                "total_tokens": input_total + output_total,
            }
            # Claude includes thinking in output but does not report a separate reasoning count.
            if "input_tokens" not in raw:
                normalized.pop("input_tokens")
            if "input_tokens" not in raw or "output_tokens" not in raw:
                normalized.pop("total_tokens")
            snapshot = {
                "response_id": data.get("id"),
                "model": data.get("model") or model,
                "provider": self.name,
                "auth_mode": "oauth" if self._is_oauth else "api_key",
                "service_tier": usage.get("service_tier", "standard"),
                "speed": usage.get("speed", "standard"),
                "usage": normalized,
                "raw_usage": deepcopy(raw),
                "container": deepcopy(data.get("container")),
                "price_kind": "api_equivalent" if self._is_oauth else "api_list_price",
                "price": (
                    claude_token_price(data.get("model") or model, raw)
                    if self._base_url == "https://api.anthropic.com"
                    else None
                ),
            }
        return AssistantMessage(
            content=self._parse_content_blocks(data.get("content") or []),
            model=data.get("model") or model,
            provider=self.name,
            usage=TokenUsage(input_tokens=input_total, output_tokens=output_total),
            provider_usage=snapshot,
            responses_output=deepcopy(data.get("content") or []),
            stop_reason=_map_anthropic_stop_reason(data.get("stop_reason")),
        )

    async def _stream_once(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str = "claude-opus-5",
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        payload = self._payload(messages, model, tools, reasoning_config, tool_choice)
        payload["stream"] = True
        accumulated = {}
        # Streaming argument fragments are parser state, never message content.
        pending_inputs: dict[int, str] = {}

        async with self._client_factory() as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/messages",
                json=payload,
                headers=self._headers(model=model, fast_mode=payload.get("speed") == "fast"),
            ) as response:
                if response.status_code == 401:
                    response.raise_for_status()
                if response.is_error:
                    body = await response.aread()
                    detail = body.decode("utf-8", errors="replace").strip()
                    snippet = detail[:500] if detail else "<no response body>"
                    raise RuntimeError(f"Anthropic stream http_{response.status_code}: {snippet}")
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_blob = line[5:].strip()
                    if not data_blob or data_blob == "[DONE]":
                        continue
                    try:
                        event = json.loads(data_blob)
                    except json.JSONDecodeError:
                        continue
                    kind = event.get("type")
                    if kind == "message_start":
                        accumulated = deepcopy(event.get("message") or {})
                    elif kind == "content_block_start":
                        blocks = accumulated.setdefault("content", [])
                        index = event["index"]
                        while len(blocks) <= index:
                            blocks.append({})
                        blocks[index] = deepcopy(event.get("content_block") or {})
                        if blocks[index].get("type") in {"tool_use", "server_tool_use"}:
                            pending_inputs[index] = ""
                    elif kind == "content_block_delta":
                        block = accumulated["content"][event["index"]]
                        delta = event.get("delta") or {}
                        field = {
                            "text_delta": "text",
                            "thinking_delta": "thinking",
                            "signature_delta": "signature",
                        }.get(delta.get("type"))
                        if field:
                            block[field] = block.get(field, "") + delta.get(field, "")
                        elif delta.get("type") == "input_json_delta":
                            index = event["index"]
                            if index not in pending_inputs:
                                raise RuntimeError(
                                    "Claude stream tool arguments have no open block."
                                )
                            pending_inputs[index] += delta.get("partial_json", "")
                    elif kind == "content_block_stop":
                        block = accumulated["content"][event["index"]]
                        index = event["index"]
                        if index in pending_inputs:
                            fragments = pending_inputs.pop(index)
                            try:
                                arguments = (
                                    json.loads(fragments) if fragments else block.get("input", {})
                                )
                            except json.JSONDecodeError as exc:
                                raise RuntimeError(
                                    "Claude stream ended a tool block with invalid JSON."
                                ) from exc
                            if not isinstance(arguments, dict):
                                raise RuntimeError(
                                    "Claude stream tool arguments must be an object."
                                )
                            block["input"] = arguments
                    elif kind == "message_delta":
                        accumulated.update(event.get("delta") or {})
                        accumulated.setdefault("usage", {}).update(event.get("usage") or {})
                        continue
                    elif kind == "message_stop":
                        if pending_inputs:
                            raise RuntimeError(
                                "Claude stream ended before tool content_block_stop."
                            )
                        message = self._message(accumulated, model)
                        yield AgentEvent(
                            type="done", stop_reason=message.stop_reason, message=message
                        )
                        return
                    for parsed in _parse_anthropic_stream_event(event):
                        yield parsed
                    if kind == "error":
                        raise RuntimeError(
                            f"Anthropic stream sse_error: {(event.get('error') or {}).get('message', 'Provider stream error')}"
                        )
        raise RuntimeError("Claude stream ended before message_stop; final usage is unavailable.")

    @staticmethod
    def _generation_options(
        reasoning_config: ReasoningConfig | None, model: str = "claude-opus-5"
    ) -> dict[str, Any]:
        rc = reasoning_config or ReasoningConfig()
        options = {"max_tokens": rc.max_tokens}
        adaptive = model.startswith(
            (
                "claude-opus-5",
                "claude-sonnet-5",
                "claude-fable-5",
                "claude-mythos-5",
                "claude-opus-4-6",
                "claude-sonnet-4-6",
                "claude-opus-4-7",
                "claude-opus-4-8",
            )
        )
        if adaptive:
            options.update(
                thinking={"type": "adaptive"},
                output_config={"effort": rc.reasoning_effort or "high"},
            )
        elif rc.thinking_budget:
            if not 1024 <= rc.thinking_budget < rc.max_tokens:
                raise ValueError(
                    "Claude thinking budget must be at least 1024 and less than max_tokens"
                )
            options["thinking"] = {"type": "enabled", "budget_tokens": rc.thinking_budget}
        return options

    def _headers(self, *, model: str = "", fast_mode: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if self._is_oauth:
            headers["authorization"] = f"Bearer {self._api_key}"
            headers["x-app"] = "cli"
            headers["user-agent"] = f"claude-cli/{_CLAUDE_CODE_VERSION} (external, cli)"
            headers["x-anthropic-billing-header"] = (
                f"cc_version={_CLAUDE_CODE_VERSION}.{model}; cc_entrypoint=cli; cch=00000;"
            )
            betas = list(_OAUTH_BASE_BETAS)
            headers["anthropic-beta"] = ",".join(betas)
        else:
            headers["x-api-key"] = self._api_key
        if fast_mode:
            headers["anthropic-beta"] = ",".join(
                filter(None, [headers.get("anthropic-beta"), "fast-mode-2026-02-01"])
            )
        return headers

    def _ensure_claude_code_system_prompt(self, system_prompt: str | None) -> list[dict[str, str]]:
        # OAuth requires the identity as its own block. Appending instructions to
        # that text produces a generic 429, even for a tiny request.
        blocks = [{"type": "text", "text": _CLAUDE_CODE_SYSTEM_PREFIX}]
        if system_prompt and system_prompt != _CLAUDE_CODE_SYSTEM_PREFIX:
            blocks.append({"type": "text", "text": system_prompt})
        return blocks

    def _extract_system_prompt(self, messages: Sequence[AgentMessage | dict]) -> str | None:
        parts: list[str] = []
        for message in messages:
            role = _message_role(message)
            if role == "system":
                content = _message_content(message)
                if isinstance(content, str) and content.strip():
                    parts.append(content.strip())
        return "\n\n".join(parts) if parts else None

    def _to_anthropic_messages(
        self, messages: Sequence[AgentMessage | dict]
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for message in messages:
            role = _message_role(message)
            if role == "system":
                continue
            if role == "tool_result" and isinstance(message, ToolResultMessage):
                output.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id,
                                "content": message.content,
                                "is_error": message.is_error,
                            }
                        ],
                    }
                )
                continue

            content = _message_content(message)
            if role == "assistant":
                if (
                    isinstance(message, AssistantMessage)
                    and message.provider == self.name
                    and message.responses_output
                ):
                    output.append(
                        {"role": "assistant", "content": deepcopy(message.responses_output)}
                    )
                    continue
                blocks: list[dict[str, Any]] = []
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, TextContent) and block.text:
                            blocks.append({"type": "text", "text": block.text})
                        elif isinstance(block, ThinkingContent):
                            thinking_block: dict[str, Any] = {
                                "type": "thinking",
                                "thinking": block.thinking,
                            }
                            if block.signature:
                                thinking_block["signature"] = block.signature
                            blocks.append(thinking_block)
                        elif isinstance(block, ToolCallContent):
                            blocks.append(
                                {
                                    "type": "tool_use",
                                    "id": block.id,
                                    "name": block.name,
                                    "input": block.arguments,
                                }
                            )
                elif isinstance(content, str) and content.strip():
                    blocks.append({"type": "text", "text": content})
                if not blocks:
                    continue
                output.append({"role": "assistant", "content": blocks})
                continue

            user_blocks: list[dict[str, Any]] = []
            if isinstance(content, str) and content.strip():
                user_blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, TextContent) and block.text:
                        user_blocks.append({"type": "text", "text": block.text})
                    elif isinstance(block, ImageContent) and block.data:
                        user_blocks.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": block.media_type,
                                    "data": block.data,
                                },
                            }
                        )
            if not user_blocks:
                continue
            output.append({"role": "user", "content": user_blocks})
        combined = []
        for item in output:
            if (
                combined
                and item["role"] == "user"
                and combined[-1]["role"] == "user"
                and all(b.get("type") == "tool_result" for b in item["content"])
                and all(b.get("type") == "tool_result" for b in combined[-1]["content"])
            ):
                combined[-1]["content"].extend(item["content"])
            else:
                combined.append(item)
        return combined

    def _parse_content_blocks(
        self, blocks: list[dict[str, Any]]
    ) -> list[TextContent | ThinkingContent | ToolCallContent]:
        parsed: list[TextContent | ThinkingContent | ToolCallContent] = []
        for block in blocks:
            block_type = block.get("type")
            if block_type == "text":
                parsed.append(TextContent(text=block.get("text") or ""))
            elif block_type == "thinking":
                parsed.append(
                    ThinkingContent(
                        thinking=block.get("thinking") or "",
                        signature=block.get("signature"),
                    )
                )
            elif block_type == "tool_use":
                parsed.append(
                    ToolCallContent(
                        id=block.get("id") or "",
                        name=block.get("name") or "",
                        arguments=(
                            block.get("input") if isinstance(block.get("input"), dict) else {}
                        ),
                    )
                )
        return parsed

    @staticmethod
    def _tool_schema(tool: ToolSchema) -> dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
        }


def _message_role(message: AgentMessage | dict) -> str:
    """Extract role from typed or dict message payload."""
    if isinstance(message, dict):
        return str(message.get("role") or "user")
    return getattr(message, "role", "user")


def _message_content(message: AgentMessage | dict) -> Any:
    """Extract content from typed or dict message payload."""
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", "")


def _map_anthropic_stop_reason(reason: str | None) -> str:
    """Normalize Anthropic stop reasons to Sentinel stop_reason values."""
    mapping = {
        "end_turn": "stop",
        "tool_use": "tool_use",
        "max_tokens": "length",
        "model_context_window_exceeded": "length",
        "refusal": "refusal",
        "pause_turn": "pause_turn",
    }
    return mapping.get(reason or "", "stop")


def _parse_anthropic_stream_event(event: dict[str, Any]) -> list[AgentEvent]:
    """Translate one Anthropic SSE payload into Sentinel agent stream events."""
    event_type = event.get("type")
    index = event.get("index")
    content_block = (
        event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
    )
    delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}

    parsed: list[AgentEvent] = []
    if event_type == "message_start":
        parsed.append(AgentEvent(type="start"))
    elif event_type == "content_block_start":
        block_type = content_block.get("type")
        logger.debug("SSE content_block_start: type=%s index=%s", block_type, index)
        if block_type == "text":
            parsed.append(AgentEvent(type="text_start", content_index=index))
        elif block_type == "thinking":
            parsed.append(AgentEvent(type="thinking_start", content_index=index))
        elif block_type == "tool_use":
            logger.info(
                "SSE tool_use block: name=%s id=%s index=%s",
                content_block.get("name"),
                content_block.get("id"),
                index,
            )
            parsed.append(
                AgentEvent(
                    type="toolcall_start",
                    content_index=index,
                    tool_call=ToolCallContent(
                        id=content_block.get("id") or "",
                        name=content_block.get("name") or "",
                        arguments=(
                            content_block.get("input")
                            if isinstance(content_block.get("input"), dict)
                            else {}
                        ),
                    ),
                )
            )
        else:
            logger.warning("SSE unknown block type: %s index=%s", block_type, index)
    elif event_type == "content_block_delta":
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            parsed.append(
                AgentEvent(
                    type="text_delta",
                    content_index=index,
                    delta=delta.get("text") or "",
                )
            )
        elif delta_type == "thinking_delta":
            parsed.append(
                AgentEvent(
                    type="thinking_delta",
                    content_index=index,
                    delta=delta.get("thinking") or "",
                )
            )
        elif delta_type == "signature_delta":
            parsed.append(
                AgentEvent(
                    type="thinking_delta",
                    content_index=index,
                    delta="",
                    signature=delta.get("signature") or "",
                )
            )
        elif delta_type == "input_json_delta":
            parsed.append(
                AgentEvent(
                    type="toolcall_delta",
                    content_index=index,
                    delta=delta.get("partial_json") or "",
                )
            )
        else:
            logger.debug("SSE unknown delta type: %s index=%s", delta_type, index)
    elif event_type == "content_block_stop":
        block_type = content_block.get("type")
        if block_type == "text":
            parsed.append(AgentEvent(type="text_end", content_index=index))
        elif block_type == "thinking":
            parsed.append(AgentEvent(type="thinking_end", content_index=index))
        elif block_type == "tool_use":
            parsed.append(AgentEvent(type="toolcall_end", content_index=index))
        elif block_type is None:
            # content_block_stop may not carry the block type — this is normal
            pass
        else:
            logger.debug("SSE content_block_stop unknown type: %s index=%s", block_type, index)
    elif event_type == "message_delta":
        raw_stop = (
            event.get("delta", {}).get("stop_reason")
            if isinstance(event.get("delta"), dict)
            else event.get("stop_reason")
        )
        mapped_stop = _map_anthropic_stop_reason(raw_stop)
        logger.info("SSE message_delta: raw_stop_reason=%s mapped=%s", raw_stop, mapped_stop)
        parsed.append(AgentEvent(type="done", stop_reason=mapped_stop))
    elif event_type == "error":
        error = event.get("error") if isinstance(event.get("error"), dict) else {}
        logger.error("SSE error event: %s", error.get("message"))
        parsed.append(
            AgentEvent(type="error", error=error.get("message") or "Provider stream error")
        )

    return parsed
