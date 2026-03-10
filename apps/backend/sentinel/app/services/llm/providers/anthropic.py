"""Anthropic Messages API provider implementation."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any
from urllib.parse import urlparse

import httpx

from app.services.llm.generic.base import LLMProvider
from app.services.llm.ids import ProviderId
from app.services.llm.generic.types import (
    AgentEvent,
    AgentMessage,
    AssistantMessage,
    ImageContent,
    ReasoningConfig,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    ToolSchema,
    TokenUsage,
    UserMessage,
)

logger = logging.getLogger(__name__)


def _is_cacheable_history_block(
    *,
    role: str,
    content_blocks: list[dict[str, Any]],
) -> bool:
    if role not in {"user", "assistant"}:
        return False
    if not content_blocks:
        return False
    if len(content_blocks) > 1:
        return False
    block = content_blocks[0]
    return block.get("type") == "text" and isinstance(block.get("text"), str)


_ANTHROPIC_MAX_CACHE_CONTROL_BLOCKS = 4


def _apply_cache_control_to_system_blocks(blocks: list[dict[str, Any]], *, budget: int) -> int:
    applied = 0
    if budget <= 0:
        return applied
    for block in blocks:
        if applied >= budget:
            break
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        if block.get("_runtime_dynamic") is True:
            continue
        text = block.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if block.get("cache_control") is not None:
            continue
        block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
        applied += 1
    return applied


def _apply_cache_control_to_history_messages(
    messages: list[dict[str, Any]],
    *,
    budget: int,
) -> int:
    applied = 0
    if budget <= 0:
        return applied
    for message in reversed(messages):
        if applied >= budget:
            break
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, list):
            continue
        if not _is_cacheable_history_block(role=str(role or ""), content_blocks=content):
            continue
        block = content[0]
        if block.get("cache_control") is not None:
            continue
        block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
        applied += 1
    return applied


def _build_oauth_cache_aware_payload(
    *,
    system_blocks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    system_copy = []
    for block in system_blocks:
        copied = dict(block)
        copied.pop("_runtime_dynamic", None)
        system_copy.append(copied)
    messages_copy = [
        {**msg, "content": [dict(block) for block in msg.get("content", [])] if isinstance(msg.get("content"), list) else msg.get("content")}
        for msg in messages
    ]

    used = _apply_cache_control_to_system_blocks(system_copy, budget=_ANTHROPIC_MAX_CACHE_CONTROL_BLOCKS)
    remaining = max(0, _ANTHROPIC_MAX_CACHE_CONTROL_BLOCKS - used)
    if remaining > 0:
        _apply_cache_control_to_history_messages(messages_copy, budget=remaining)
    return system_copy, messages_copy


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API adapter with streaming event translation."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.anthropic.com",
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=60))
        self._is_oauth = self._detect_oauth(api_key)
        self._inference_api_key: str | None = None
        self._oauth_exchange_attempted = False
        self._oauth_exchange_error: str | None = None

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

    async def chat(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str = "claude-sonnet-4-20250514",
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AssistantMessage:
        _ = tool_choice
        rc = reasoning_config or ReasoningConfig()
        if rc.thinking_budget and rc.thinking_budget >= rc.max_tokens:
            rc = ReasoningConfig(
                max_tokens=rc.thinking_budget + 8192,
                thinking_budget=rc.thinking_budget,
                reasoning_effort=rc.reasoning_effort,
            )
            logger.warning(
                "Anthropic: thinking_budget >= max_tokens, auto-raised max_tokens to %d",
                rc.max_tokens,
            )
        use_thinking = rc.thinking_budget is not None and rc.thinking_budget > 0
        logger.info(
            "Anthropic.chat: model=%s use_thinking=%s thinking_budget=%s max_tokens=%s tools=%d",
            model, use_thinking, rc.thinking_budget, rc.max_tokens,
            len(tools) if tools else 0,
        )
        anthropic_messages = self._to_anthropic_messages(messages)
        payload = {
            "model": model or "claude-sonnet-4-20250514",
            "max_tokens": rc.max_tokens,
            "temperature": 1.0 if use_thinking else temperature,
            "messages": anthropic_messages,
        }
        if use_thinking:
            payload["thinking"] = {"type": "enabled", "budget_tokens": rc.thinking_budget}
        if self._is_oauth:
            system_blocks = self._extract_system_prompt_blocks(messages)
            if system_blocks:
                cached_system, cached_messages = _build_oauth_cache_aware_payload(
                    system_blocks=system_blocks,
                    messages=anthropic_messages,
                )
                payload["system"] = cached_system
                payload["messages"] = cached_messages
        else:
            system_prompt = self._extract_system_prompt(messages)
            if system_prompt:
                payload["system"] = system_prompt
        if tools:
            payload["tools"] = [self._tool_schema(tool) for tool in tools]

        async with self._client_factory() as client:
            response = await client.post(
                f"{self._base_url}/v1/messages",
                json=payload,
                headers=await self._auth_headers(thinking=use_thinking, cache_scope=self._is_oauth),
            )
        response.raise_for_status()

        data = response.json()
        usage = data.get("usage") or {}
        usage_payload = _token_usage_from_anthropic_usage(usage)
        return AssistantMessage(
            content=self._parse_content_blocks(data.get("content") or []),
            model=data.get("model") or model,
            provider=self.name,
            usage=usage_payload,
            stop_reason=_map_anthropic_stop_reason(data.get("stop_reason")),
        )

    async def stream(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str = "claude-sonnet-4-20250514",
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        _ = tool_choice
        rc = reasoning_config or ReasoningConfig()
        # Anthropic requires max_tokens > thinking_budget
        if rc.thinking_budget and rc.thinking_budget >= rc.max_tokens:
            rc = ReasoningConfig(
                max_tokens=rc.thinking_budget + 8192,
                thinking_budget=rc.thinking_budget,
                reasoning_effort=rc.reasoning_effort,
            )
            logger.warning(
                "Anthropic: thinking_budget >= max_tokens, auto-raised max_tokens to %d",
                rc.max_tokens,
            )
        use_thinking = rc.thinking_budget is not None and rc.thinking_budget > 0
        logger.info(
            "Anthropic.stream: model=%s use_thinking=%s thinking_budget=%s max_tokens=%s tools=%d",
            model, use_thinking, rc.thinking_budget, rc.max_tokens,
            len(tools) if tools else 0,
        )
        anthropic_messages = self._to_anthropic_messages(messages)
        payload = {
            "model": model or "claude-sonnet-4-20250514",
            "max_tokens": rc.max_tokens,
            "temperature": 1.0 if use_thinking else temperature,
            "stream": True,
            "messages": anthropic_messages,
        }
        if use_thinking:
            payload["thinking"] = {"type": "enabled", "budget_tokens": rc.thinking_budget}
        if self._is_oauth:
            system_blocks = self._extract_system_prompt_blocks(messages)
            if system_blocks:
                cached_system, cached_messages = _build_oauth_cache_aware_payload(
                    system_blocks=system_blocks,
                    messages=anthropic_messages,
                )
                payload["system"] = cached_system
                payload["messages"] = cached_messages
        else:
            system_prompt = self._extract_system_prompt(messages)
            if system_prompt:
                payload["system"] = system_prompt
        if tools:
            payload["tools"] = [self._tool_schema(tool) for tool in tools]

        usage_state = TokenUsage()
        async with self._client_factory() as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/messages",
                json=payload,
                headers=await self._auth_headers(thinking=use_thinking, cache_scope=self._is_oauth),
            ) as response:
                if response.is_error:
                    body = await response.aread()
                    detail = body.decode("utf-8", errors="replace").strip()
                    snippet = detail[:500] if detail else "<no response body>"
                    raise RuntimeError(
                        f"Anthropic stream http_{response.status_code}: {snippet}"
                    )
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
                    for parsed in _parse_anthropic_stream_event(event, usage_state=usage_state):
                        if parsed.type == "error":
                            detail = str(parsed.error or "Provider stream error")
                            raise RuntimeError(f"Anthropic stream sse_error: {detail}")
                        if parsed.type == "done":
                            parsed.message = AssistantMessage(usage=usage_state)
                        yield parsed

    async def _ensure_inference_api_key(self) -> None:
        if not self._is_oauth:
            return
        if not self._supports_oauth_api_key_exchange():
            return
        if self._inference_api_key is not None:
            return
        if self._oauth_exchange_attempted:
            return

        self._oauth_exchange_attempted = True
        token = self._api_key.strip()
        if not token:
            self._oauth_exchange_error = "empty oauth token"
            return

        headers = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
        }
        exchange_url = f"{self._base_url}/api/oauth/claude_cli/create_api_key"
        try:
            async with self._client_factory() as client:
                response = await client.post(exchange_url, headers=headers, json={})
            is_error = bool(getattr(response, "is_error", False))
            if is_error:
                detail = response.text.strip()[:400]
                self._oauth_exchange_error = f"http_{response.status_code}: {detail}"
                logger.warning("Anthropic OAuth API key exchange failed: %s", self._oauth_exchange_error)
                return
            try:
                payload = response.json()
            except Exception:  # noqa: BLE001
                payload = {}
            exchanged_key: str | None = None
            if isinstance(payload, dict):
                candidate = payload.get("raw_key")
                if isinstance(candidate, str) and candidate.strip():
                    exchanged_key = candidate.strip()
                if exchanged_key is None:
                    candidate = payload.get("api_key")
                    if isinstance(candidate, str) and candidate.strip():
                        exchanged_key = candidate.strip()
            if exchanged_key is not None:
                self._inference_api_key = exchanged_key
                logger.info("Anthropic OAuth API key exchange succeeded")
                return
            self._oauth_exchange_error = "response_missing_api_key"
            logger.warning("Anthropic OAuth API key exchange missing raw_key/api_key in response")
        except Exception as exc:  # noqa: BLE001
            self._oauth_exchange_error = str(exc)
            logger.warning("Anthropic OAuth API key exchange exception: %s", self._oauth_exchange_error)

    async def _auth_headers(self, *, thinking: bool = False, cache_scope: bool = False) -> dict[str, str]:
        if self._is_oauth:
            await self._ensure_inference_api_key()
        return self._headers(thinking=thinking, cache_scope=cache_scope)

    def _headers(self, *, thinking: bool = False, cache_scope: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        betas: list[str] = []
        if self._is_oauth:
            if self._inference_api_key:
                headers["x-api-key"] = self._inference_api_key
            else:
                headers["authorization"] = f"Bearer {self._api_key}"
                betas.append("oauth-2025-04-20")
        else:
            headers["x-api-key"] = self._api_key
        if thinking:
            betas.append("interleaved-thinking-2025-05-14")
        if cache_scope and self._supports_prompt_cache_scope_beta():
            betas.append("prompt-caching-scope-2026-01-05")
        if betas:
            headers["anthropic-beta"] = ",".join(dict.fromkeys(betas))
        return headers

    def _extract_system_prompt(self, messages: Sequence[AgentMessage | dict]) -> str | None:
        parts: list[str] = []
        for message in messages:
            role = _message_role(message)
            if role == "system":
                content = _message_content(message)
                if isinstance(content, str) and content.strip():
                    parts.append(content.strip())
        return "\n\n".join(parts) if parts else None

    def _supports_prompt_cache_scope_beta(self) -> bool:
        try:
            host = urlparse(self._base_url).hostname or ""
        except ValueError:
            return False
        return host == "api.anthropic.com"

    def _supports_oauth_api_key_exchange(self) -> bool:
        try:
            host = urlparse(self._base_url).hostname or ""
        except ValueError:
            return False
        return host == "api.anthropic.com"

    def _extract_system_prompt_blocks(self, messages: Sequence[AgentMessage | dict]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for message in messages:
            if _message_role(message) != "system":
                continue
            content = _message_content(message)
            if not isinstance(content, str):
                continue
            text = content.strip()
            if not text:
                continue

            metadata = _message_metadata(message)
            kind = metadata.get("kind") if isinstance(metadata, dict) else None
            is_dynamic = kind == "runtime_info"
            block: dict[str, Any] = {"type": "text", "text": text}
            if is_dynamic:
                block["_runtime_dynamic"] = True
            blocks.append(block)
        return blocks

    def _to_anthropic_messages(self, messages: Sequence[AgentMessage | dict]) -> list[dict[str, Any]]:
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
                blocks: list[dict[str, Any]] = []
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, TextContent) and block.text:
                            blocks.append({"type": "text", "text": block.text})
                        elif isinstance(block, ThinkingContent):
                            thinking_block: dict[str, Any] = {"type": "thinking", "thinking": block.thinking}
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
        return output

    def _parse_content_blocks(self, blocks: list[dict[str, Any]]) -> list[TextContent | ThinkingContent | ToolCallContent]:
        parsed: list[TextContent | ThinkingContent | ToolCallContent] = []
        for block in blocks:
            block_type = block.get("type")
            if block_type == "text":
                parsed.append(TextContent(text=block.get("text") or ""))
            elif block_type == "thinking":
                parsed.append(ThinkingContent(
                    thinking=block.get("thinking") or "",
                    signature=block.get("signature"),
                ))
            elif block_type == "tool_use":
                parsed.append(
                    ToolCallContent(
                        id=block.get("id") or "",
                        name=block.get("name") or "",
                        arguments=block.get("input") if isinstance(block.get("input"), dict) else {},
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


def _message_metadata(message: AgentMessage | dict) -> dict[str, Any]:
    if isinstance(message, dict):
        raw = message.get("metadata")
    else:
        raw = getattr(message, "metadata", None)
    return raw if isinstance(raw, dict) else {}


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _token_usage_from_anthropic_usage(usage: dict[str, Any] | None) -> TokenUsage:
    payload = usage if isinstance(usage, dict) else {}
    cache_creation = payload.get("cache_creation")
    cache_creation_payload = cache_creation if isinstance(cache_creation, dict) else {}
    return TokenUsage(
        input_tokens=_int_or_zero(payload.get("input_tokens")),
        output_tokens=_int_or_zero(payload.get("output_tokens")),
        cache_creation_input_tokens=_int_or_zero(payload.get("cache_creation_input_tokens")),
        cache_read_input_tokens=_int_or_zero(payload.get("cache_read_input_tokens")),
        cache_creation_ephemeral_1h_input_tokens=_int_or_zero(
            cache_creation_payload.get("ephemeral_1h_input_tokens")
        ),
        cache_creation_ephemeral_5m_input_tokens=_int_or_zero(
            cache_creation_payload.get("ephemeral_5m_input_tokens")
        ),
    )


def _map_anthropic_stop_reason(reason: str | None) -> str:
    """Normalize Anthropic stop reasons to Sentinel stop_reason values."""
    mapping = {
        "end_turn": "stop",
        "tool_use": "tool_use",
        "max_tokens": "length",
    }
    return mapping.get(reason or "", "stop")


def _parse_anthropic_stream_event(
    event: dict[str, Any],
    usage_state: TokenUsage | None = None,
) -> list[AgentEvent]:
    """Translate one Anthropic SSE payload into Sentinel agent stream events."""
    event_type = event.get("type")
    index = event.get("index")
    content_block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
    delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}

    parsed: list[AgentEvent] = []
    usage_payload = event.get("usage") if isinstance(event.get("usage"), dict) else None
    if usage_state is not None and usage_payload:
        delta_usage = _token_usage_from_anthropic_usage(usage_payload)
        usage_state.input_tokens += delta_usage.input_tokens
        usage_state.output_tokens += delta_usage.output_tokens
        usage_state.cache_creation_input_tokens += delta_usage.cache_creation_input_tokens
        usage_state.cache_read_input_tokens += delta_usage.cache_read_input_tokens
        usage_state.cache_creation_ephemeral_1h_input_tokens += (
            delta_usage.cache_creation_ephemeral_1h_input_tokens
        )
        usage_state.cache_creation_ephemeral_5m_input_tokens += (
            delta_usage.cache_creation_ephemeral_5m_input_tokens
        )
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
                content_block.get("name"), content_block.get("id"), index,
            )
            parsed.append(
                AgentEvent(
                    type="toolcall_start",
                    content_index=index,
                    tool_call=ToolCallContent(
                        id=content_block.get("id") or "",
                        name=content_block.get("name") or "",
                        arguments=content_block.get("input")
                        if isinstance(content_block.get("input"), dict)
                        else {},
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
        parsed.append(AgentEvent(type="error", error=error.get("message") or "Provider stream error"))

    return parsed
