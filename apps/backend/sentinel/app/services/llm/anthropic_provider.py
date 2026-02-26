from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import httpx

from app.services.llm.base import LLMProvider

logger = logging.getLogger(__name__)
from app.services.llm.types import (
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


class AnthropicProvider(LLMProvider):
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
        return "anthropic"

    async def chat(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str = "claude-sonnet-4-20250514",
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
    ) -> AssistantMessage:
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
        payload = {
            "model": model or "claude-sonnet-4-20250514",
            "max_tokens": rc.max_tokens,
            "temperature": 1.0 if use_thinking else temperature,
            "messages": self._to_anthropic_messages(messages),
        }
        if use_thinking:
            payload["thinking"] = {"type": "enabled", "budget_tokens": rc.thinking_budget}
        system_prompt = self._extract_system_prompt(messages)
        if system_prompt:
            payload["system"] = system_prompt
        if tools:
            payload["tools"] = [self._tool_schema(tool) for tool in tools]

        async with self._client_factory() as client:
            response = await client.post(
                f"{self._base_url}/v1/messages",
                json=payload,
                headers=self._headers(thinking=use_thinking),
            )
        response.raise_for_status()

        data = response.json()
        usage = data.get("usage") or {}
        return AssistantMessage(
            content=self._parse_content_blocks(data.get("content") or []),
            model=data.get("model") or model,
            provider=self.name,
            usage=TokenUsage(
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
            ),
            stop_reason=_map_anthropic_stop_reason(data.get("stop_reason")),
        )

    async def stream(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str = "claude-sonnet-4-20250514",
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
    ) -> AsyncIterator[AgentEvent]:
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
        payload = {
            "model": model or "claude-sonnet-4-20250514",
            "max_tokens": rc.max_tokens,
            "temperature": 1.0 if use_thinking else temperature,
            "stream": True,
            "messages": self._to_anthropic_messages(messages),
        }
        if use_thinking:
            payload["thinking"] = {"type": "enabled", "budget_tokens": rc.thinking_budget}
        system_prompt = self._extract_system_prompt(messages)
        if system_prompt:
            payload["system"] = system_prompt
        if tools:
            payload["tools"] = [self._tool_schema(tool) for tool in tools]

        async with self._client_factory() as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/messages",
                json=payload,
                headers=self._headers(thinking=use_thinking),
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
                    for parsed in _parse_anthropic_stream_event(event):
                        yield parsed

    def _headers(self, *, thinking: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        betas: list[str] = []
        if self._is_oauth:
            headers["authorization"] = f"Bearer {self._api_key}"
            betas.append("oauth-2025-04-20")
        else:
            headers["x-api-key"] = self._api_key
        if thinking:
            betas.append("interleaved-thinking-2025-05-14")
        if betas:
            headers["anthropic-beta"] = ",".join(betas)
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
    if isinstance(message, dict):
        return str(message.get("role") or "user")
    return getattr(message, "role", "user")


def _message_content(message: AgentMessage | dict) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", "")


def _map_anthropic_stop_reason(reason: str | None) -> str:
    mapping = {
        "end_turn": "stop",
        "tool_use": "tool_use",
        "max_tokens": "length",
    }
    return mapping.get(reason or "", "stop")


def _parse_anthropic_stream_event(event: dict[str, Any]) -> list[AgentEvent]:
    event_type = event.get("type")
    index = event.get("index")
    content_block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
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
