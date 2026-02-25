from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import httpx

from app.services.llm.base import LLMProvider
from app.services.llm.types import (
    AgentEvent,
    AgentMessage,
    AssistantMessage,
    ReasoningConfig,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    ToolSchema,
    TokenUsage,
    UserMessage,
)


class OpenAIProvider(LLMProvider):
    _chat_endpoint: str = "/chat/completions"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=60))

    @property
    def name(self) -> str:
        return "openai"

    def _payload_extras(self) -> dict[str, Any]:
        """Hook for subclasses to inject extra payload fields."""
        return {}

    async def chat(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
    ) -> AssistantMessage:
        rc = reasoning_config or ReasoningConfig()
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._to_openai_messages(messages),
            "temperature": temperature,
            "max_completion_tokens": rc.max_tokens,
            **self._payload_extras(),
        }
        if rc.reasoning_effort:
            payload["reasoning_effort"] = rc.reasoning_effort
        if tools:
            payload["tools"] = [self._tool_schema(tool) for tool in tools]

        async with self._client_factory() as client:
            response = await client.post(
                f"{self._base_url}{self._chat_endpoint}",
                json=payload,
                headers=self._headers(),
            )
        response.raise_for_status()
        data = response.json()

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        content_blocks: list[TextContent | ThinkingContent | ToolCallContent] = []

        text_content = message.get("content")
        if isinstance(text_content, str) and text_content:
            content_blocks.append(TextContent(text=text_content))

        for tool_call in message.get("tool_calls") or []:
            fn = tool_call.get("function") if isinstance(tool_call, dict) else None
            if not isinstance(fn, dict):
                continue
            arguments = fn.get("arguments")
            parsed_arguments: dict[str, Any]
            if isinstance(arguments, str):
                try:
                    parsed = json.loads(arguments)
                    parsed_arguments = parsed if isinstance(parsed, dict) else {"value": parsed}
                except json.JSONDecodeError:
                    parsed_arguments = {"raw": arguments}
            elif isinstance(arguments, dict):
                parsed_arguments = arguments
            else:
                parsed_arguments = {}
            content_blocks.append(
                ToolCallContent(
                    id=tool_call.get("id") or "",
                    name=fn.get("name") or "",
                    arguments=parsed_arguments,
                )
            )

        return AssistantMessage(
            content=content_blocks,
            model=data.get("model") or model,
            provider=self.name,
            usage=TokenUsage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
            ),
            stop_reason=_map_openai_finish_reason(choice.get("finish_reason")),
        )

    async def stream(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
    ) -> AsyncIterator[AgentEvent]:
        rc = reasoning_config or ReasoningConfig()
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._to_openai_messages(messages),
            "temperature": temperature,
            "stream": True,
            "max_completion_tokens": rc.max_tokens,
            **self._payload_extras(),
        }
        if rc.reasoning_effort:
            payload["reasoning_effort"] = rc.reasoning_effort
        if tools:
            payload["tools"] = [self._tool_schema(tool) for tool in tools]

        started = False
        text_started = False
        tool_started: set[int] = set()

        async with self._client_factory() as client:
            async with client.stream(
                "POST",
                f"{self._base_url}{self._chat_endpoint}",
                json=payload,
                headers=self._headers(),
            ) as response:
                if response.is_error:
                    body = await response.aread()
                    detail = body.decode("utf-8", errors="replace").strip()
                    snippet = detail[:500] if detail else "<no response body>"
                    raise RuntimeError(
                        f"OpenAI stream http_{response.status_code}: {snippet}"
                    )
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    data_blob = line[5:].strip()
                    if not data_blob:
                        continue
                    if data_blob == "[DONE]":
                        if text_started:
                            yield AgentEvent(type="text_end", content_index=0)
                        yield AgentEvent(type="done", stop_reason="stop")
                        break
                    event = json.loads(data_blob)

                    if not started:
                        started = True
                        yield AgentEvent(type="start")

                    if "error" in event:
                        error = event.get("error")
                        message = error.get("message") if isinstance(error, dict) else "Provider stream error"
                        yield AgentEvent(type="error", error=message)
                        continue

                    choice = (event.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    finish_reason = choice.get("finish_reason")

                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        if not text_started:
                            text_started = True
                            yield AgentEvent(type="text_start", content_index=0)
                        yield AgentEvent(type="text_delta", content_index=0, delta=content)

                    for tool_delta in delta.get("tool_calls") or []:
                        if not isinstance(tool_delta, dict):
                            continue
                        idx = int(tool_delta.get("index") or 0)
                        fn = tool_delta.get("function") if isinstance(tool_delta.get("function"), dict) else {}
                        if idx not in tool_started:
                            tool_started.add(idx)
                            yield AgentEvent(
                                type="toolcall_start",
                                content_index=idx,
                                tool_call=ToolCallContent(
                                    id=tool_delta.get("id") or "",
                                    name=fn.get("name") or "",
                                    arguments={},
                                ),
                            )
                        arguments_delta = fn.get("arguments")
                        if isinstance(arguments_delta, str) and arguments_delta:
                            yield AgentEvent(
                                type="toolcall_delta",
                                content_index=idx,
                                delta=arguments_delta,
                            )

                    if finish_reason == "tool_calls":
                        for idx in sorted(tool_started):
                            yield AgentEvent(type="toolcall_end", content_index=idx)
                        yield AgentEvent(type="done", stop_reason="tool_use")
                    elif finish_reason:
                        if text_started:
                            yield AgentEvent(type="text_end", content_index=0)
                        yield AgentEvent(type="done", stop_reason=_map_openai_finish_reason(finish_reason))

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }

    def _to_openai_messages(self, messages: Sequence[AgentMessage | dict]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, dict):
                converted.append(message)
                continue

            if isinstance(message, ToolResultMessage):
                converted.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": message.content,
                    }
                )
                continue

            role = getattr(message, "role", "user")
            content = getattr(message, "content", "")
            if role == "assistant" and isinstance(content, list):
                text_parts = [item.text for item in content if isinstance(item, TextContent)]
                tool_calls = [
                    {
                        "id": item.id,
                        "type": "function",
                        "function": {
                            "name": item.name,
                            "arguments": json.dumps(item.arguments),
                        },
                    }
                    for item in content
                    if isinstance(item, ToolCallContent)
                ]
                payload: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts)}
                if tool_calls:
                    payload["tool_calls"] = tool_calls
                converted.append(payload)
                continue

            if isinstance(message, UserMessage) and isinstance(content, list):
                text = "\n".join(item.text for item in content if isinstance(item, TextContent))
                converted.append({"role": "user", "content": text})
                continue

            converted.append({"role": role, "content": content if isinstance(content, str) else ""})
        return converted

    @staticmethod
    def _tool_schema(tool: ToolSchema) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }


def _map_openai_finish_reason(reason: str | None) -> str:
    mapping = {
        "stop": "stop",
        "tool_calls": "tool_use",
        "length": "length",
    }
    return mapping.get(reason or "", "stop")
