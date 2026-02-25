"""Google Gemini LLM provider — raw httpx, SSE streaming.

Follows the same pattern as anthropic_provider.py.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any
from uuid import uuid4

import httpx

from app.services.llm.base import LLMProvider
from app.services.llm.gemini_schema_cleaner import clean_schema_for_gemini
from app.services.llm.types import (
    AgentEvent,
    AgentMessage,
    AssistantMessage,
    ReasoningConfig,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    ToolSchema,
    TokenUsage,
    UserMessage,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=120))

    @property
    def name(self) -> str:
        return "gemini"

    # ------------------------------------------------------------------
    # chat (non-streaming)
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str = "gemini-2.5-flash",
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
    ) -> AssistantMessage:
        rc = reasoning_config or ReasoningConfig()
        thinking_budget = rc.thinking_budget if rc.thinking_budget and rc.thinking_budget > 0 else 0
        logger.info(
            "Gemini.chat: model=%s thinking_budget=%s tools=%d",
            model, thinking_budget, len(tools) if tools else 0,
        )

        payload = self._build_payload(messages, model, tools, temperature, thinking_budget)
        url = f"{self._base_url}/models/{model}:generateContent"

        async with self._client_factory() as client:
            response = await client.post(url, json=payload, headers=self._headers())
        response.raise_for_status()
        data = response.json()

        return self._parse_response(data, model)

    # ------------------------------------------------------------------
    # stream (SSE)
    # ------------------------------------------------------------------

    async def stream(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str = "gemini-2.5-flash",
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
    ) -> AsyncIterator[AgentEvent]:
        rc = reasoning_config or ReasoningConfig()
        thinking_budget = rc.thinking_budget if rc.thinking_budget and rc.thinking_budget > 0 else 0
        logger.info(
            "Gemini.stream: model=%s thinking_budget=%s tools=%d",
            model, thinking_budget, len(tools) if tools else 0,
        )

        payload = self._build_payload(messages, model, tools, temperature, thinking_budget)
        url = f"{self._base_url}/models/{model}:streamGenerateContent?alt=sse"

        started = False
        text_started = False
        thinking_started = False
        tool_index = 0

        async with self._client_factory() as client:
            async with client.stream("POST", url, json=payload, headers=self._headers()) as response:
                if response.is_error:
                    body = await response.aread()
                    detail = body.decode("utf-8", errors="replace").strip()
                    snippet = detail[:500] if detail else "<no response body>"
                    raise RuntimeError(f"Gemini stream http_{response.status_code}: {snippet}")

                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_blob = line[5:].strip()
                    if not data_blob or data_blob == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data_blob)
                    except json.JSONDecodeError:
                        continue

                    if not started:
                        started = True
                        yield AgentEvent(type="start")

                    # Check for errors
                    if "error" in chunk:
                        error = chunk["error"]
                        msg = error.get("message") if isinstance(error, dict) else "Gemini stream error"
                        yield AgentEvent(type="error", error=msg)
                        continue

                    candidates = chunk.get("candidates") or []
                    if not candidates:
                        continue
                    candidate = candidates[0]
                    content = candidate.get("content") or {}
                    parts = content.get("parts") or []
                    finish_reason = candidate.get("finishReason")

                    has_function_call = False
                    for part in parts:
                        if not isinstance(part, dict):
                            continue

                        # Thinking part
                        if part.get("thought") is True and "text" in part:
                            if not thinking_started:
                                thinking_started = True
                                yield AgentEvent(type="thinking_start", content_index=0)
                            yield AgentEvent(type="thinking_delta", content_index=0, delta=part["text"])
                            continue

                        # Text part
                        if "text" in part and "functionCall" not in part:
                            if thinking_started:
                                thinking_started = False
                                yield AgentEvent(type="thinking_end", content_index=0)
                            if not text_started:
                                text_started = True
                                yield AgentEvent(type="text_start", content_index=0)
                            yield AgentEvent(type="text_delta", content_index=0, delta=part["text"])
                            continue

                        # Function call part (arrives as complete object)
                        fc = part.get("functionCall")
                        if isinstance(fc, dict):
                            has_function_call = True
                            if text_started:
                                text_started = False
                                yield AgentEvent(type="text_end", content_index=0)
                            call_id = f"gemini_{uuid4().hex[:8]}"
                            args = fc.get("args") or {}
                            yield AgentEvent(
                                type="toolcall_start",
                                content_index=tool_index,
                                tool_call=ToolCallContent(
                                    id=call_id,
                                    name=fc.get("name") or "",
                                    arguments={},
                                ),
                            )
                            yield AgentEvent(
                                type="toolcall_delta",
                                content_index=tool_index,
                                delta=json.dumps(args),
                            )
                            yield AgentEvent(type="toolcall_end", content_index=tool_index)
                            tool_index += 1

                    if finish_reason:
                        if thinking_started:
                            yield AgentEvent(type="thinking_end", content_index=0)
                        if text_started:
                            yield AgentEvent(type="text_end", content_index=0)
                        stop = "tool_use" if has_function_call else _map_finish_reason(finish_reason)
                        yield AgentEvent(type="done", stop_reason=stop)

    # ------------------------------------------------------------------
    # Payload building
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None,
        temperature: float,
        thinking_budget: int,
    ) -> dict[str, Any]:
        system_text, contents = self._to_gemini_contents(messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
            },
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [self._tool_schema(t) for t in tools],
                }
            ]
        if thinking_budget > 0:
            payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": thinking_budget}
        return payload

    def _headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self._api_key,
            "content-type": "application/json",
        }

    # ------------------------------------------------------------------
    # Message format conversion
    # ------------------------------------------------------------------

    def _to_gemini_contents(
        self, messages: Sequence[AgentMessage | dict],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Convert sentinel messages to Gemini contents array + optional system text."""
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []

        for message in messages:
            # --- dict passthrough ---
            if isinstance(message, dict):
                role = str(message.get("role", "user"))
                if role == "system":
                    c = message.get("content")
                    if isinstance(c, str) and c.strip():
                        system_parts.append(c.strip())
                    continue
                contents.append(message)
                continue

            # --- SystemMessage ---
            if isinstance(message, SystemMessage):
                if isinstance(message.content, str) and message.content.strip():
                    system_parts.append(message.content.strip())
                continue

            # --- ToolResultMessage → user role with functionResponse ---
            if isinstance(message, ToolResultMessage):
                contents.append({
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": message.tool_name,
                                "response": {"result": message.content},
                            }
                        }
                    ],
                })
                continue

            # --- UserMessage ---
            if isinstance(message, UserMessage):
                text = ""
                if isinstance(message.content, str):
                    text = message.content
                elif isinstance(message.content, list):
                    text = "\n".join(
                        item.text for item in message.content if isinstance(item, TextContent)
                    )
                if text.strip():
                    contents.append({"role": "user", "parts": [{"text": text}]})
                continue

            # --- AssistantMessage ---
            if isinstance(message, AssistantMessage):
                parts: list[dict[str, Any]] = []
                content = message.content
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, TextContent) and block.text:
                            parts.append({"text": block.text})
                        elif isinstance(block, ThinkingContent):
                            # Skip thinking — Gemini manages thinking internally
                            continue
                        elif isinstance(block, ToolCallContent):
                            parts.append({
                                "functionCall": {
                                    "name": block.name,
                                    "args": block.arguments,
                                }
                            })
                elif isinstance(content, str) and content.strip():
                    parts.append({"text": content})
                if parts:
                    contents.append({"role": "model", "parts": parts})
                continue

        system_text = "\n\n".join(system_parts) if system_parts else None
        return system_text, contents

    # ------------------------------------------------------------------
    # Response parsing (non-streaming)
    # ------------------------------------------------------------------

    def _parse_response(self, data: dict[str, Any], model: str) -> AssistantMessage:
        candidates = data.get("candidates") or []
        if not candidates:
            return AssistantMessage(
                content=[TextContent(text="")],
                model=model,
                provider=self.name,
                usage=self._parse_usage(data),
                stop_reason="stop",
            )

        candidate = candidates[0]
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        finish_reason = candidate.get("finishReason")

        blocks: list[TextContent | ThinkingContent | ToolCallContent] = []
        has_function_call = False

        for part in parts:
            if not isinstance(part, dict):
                continue
            # Thinking
            if part.get("thought") is True and "text" in part:
                blocks.append(ThinkingContent(thinking=part["text"]))
                continue
            # Function call
            fc = part.get("functionCall")
            if isinstance(fc, dict):
                has_function_call = True
                blocks.append(ToolCallContent(
                    id=f"gemini_{uuid4().hex[:8]}",
                    name=fc.get("name") or "",
                    arguments=fc.get("args") if isinstance(fc.get("args"), dict) else {},
                ))
                continue
            # Text
            if "text" in part:
                blocks.append(TextContent(text=part["text"]))

        stop = "tool_use" if has_function_call else _map_finish_reason(finish_reason)

        return AssistantMessage(
            content=blocks,
            model=model,
            provider=self.name,
            usage=self._parse_usage(data),
            stop_reason=stop,
        )

    @staticmethod
    def _parse_usage(data: dict[str, Any]) -> TokenUsage:
        usage = data.get("usageMetadata") or {}
        return TokenUsage(
            input_tokens=int(usage.get("promptTokenCount") or 0),
            output_tokens=int(usage.get("candidatesTokenCount") or 0),
        )

    @staticmethod
    def _tool_schema(tool: ToolSchema) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description,
        }
        if tool.parameters:
            schema["parameters"] = clean_schema_for_gemini(tool.parameters)
        return schema


def _map_finish_reason(reason: str | None) -> str:
    mapping = {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "stop",
        "RECITATION": "stop",
        "OTHER": "stop",
    }
    return mapping.get(reason or "", "stop")
