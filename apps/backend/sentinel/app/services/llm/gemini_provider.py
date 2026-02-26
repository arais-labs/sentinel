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
        raw_contents: list[dict[str, Any]] = []

        for message in messages:
            # --- dict normalization ---
            if isinstance(message, dict):
                normalized = self._normalize_dict_message(message)
                if normalized is None:
                    continue
                kind, payload = normalized
                if kind == "system":
                    if payload:
                        system_parts.append(payload)
                    continue
                raw_contents.append(payload)
                continue

            # --- SystemMessage ---
            if isinstance(message, SystemMessage):
                if isinstance(message.content, str) and message.content.strip():
                    system_parts.append(message.content.strip())
                continue

            # --- ToolResultMessage → user role with functionResponse ---
            if isinstance(message, ToolResultMessage):
                raw_contents.append({
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
                    raw_contents.append({"role": "user", "parts": [{"text": text}]})
                continue

            # --- AssistantMessage ---
            if isinstance(message, AssistantMessage):
                text_parts: list[dict[str, Any]] = []
                function_parts: list[dict[str, Any]] = []
                content = message.content
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, TextContent) and block.text:
                            text_parts.append({"text": block.text})
                        elif isinstance(block, ThinkingContent):
                            # Skip thinking — Gemini manages thinking internally
                            continue
                        elif isinstance(block, ToolCallContent):
                            function_parts.append({
                                "functionCall": {
                                    "name": block.name,
                                    "args": block.arguments,
                                }
                            })
                elif isinstance(content, str) and content.strip():
                    text_parts.append({"text": content})
                # Gemini is strict about function-calling turn order.
                # If a turn has function calls, keep only functionCall parts.
                if function_parts:
                    raw_contents.append({"role": "model", "parts": function_parts})
                elif text_parts:
                    raw_contents.append({"role": "model", "parts": text_parts})
                continue

        contents = self._sanitize_contents(raw_contents)
        system_text = "\n\n".join(system_parts) if system_parts else None
        return system_text, contents

    def _normalize_dict_message(
        self,
        message: dict[str, Any],
    ) -> tuple[str, str | dict[str, Any]] | None:
        role = str(message.get("role", "user")).strip().lower()

        # System dicts are accepted in some internal call-sites (e.g. compaction).
        if role == "system":
            text = self._extract_text(message.get("content"))
            if text:
                return ("system", text)
            parts = message.get("parts")
            if isinstance(parts, list):
                text_parts = [p.get("text") for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)]
                merged = "\n".join(item for item in text_parts if item.strip()).strip()
                if merged:
                    return ("system", merged)
            return None

        mapped_role = "model" if role in {"assistant", "model"} else "user"
        normalized_parts = self._normalize_dict_parts(message, mapped_role)
        if not normalized_parts:
            return None
        return ("content", {"role": mapped_role, "parts": normalized_parts})

    def _normalize_dict_parts(self, message: dict[str, Any], mapped_role: str) -> list[dict[str, Any]]:
        parts_out: list[dict[str, Any]] = []
        parts = message.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    parts_out.append({"text": text})
                    continue
                fc = part.get("functionCall")
                if (
                    mapped_role == "model"
                    and isinstance(fc, dict)
                    and isinstance(fc.get("name"), str)
                    and fc.get("name")
                ):
                    args = fc.get("args") if isinstance(fc.get("args"), dict) else {}
                    parts_out.append({"functionCall": {"name": fc["name"], "args": args}})
                    continue
                fr = part.get("functionResponse")
                if (
                    mapped_role == "user"
                    and isinstance(fr, dict)
                    and isinstance(fr.get("name"), str)
                    and fr.get("name")
                ):
                    response = fr.get("response")
                    if not isinstance(response, dict):
                        response = {"result": str(response) if response is not None else ""}
                    parts_out.append({"functionResponse": {"name": fr["name"], "response": response}})
            if parts_out:
                return parts_out

        # Common OpenAI-style payloads use "content" string instead of Gemini "parts".
        text = self._extract_text(message.get("content"))
        if text:
            return [{"text": text}]

        # Allow "tool_result" dict-like messages when passed through internal adapters.
        if mapped_role == "user":
            tool_name = message.get("tool_name") or message.get("name")
            if isinstance(tool_name, str) and tool_name.strip():
                result_text = self._extract_text(message.get("content")) or self._extract_text(message.get("result")) or ""
                return [
                    {
                        "functionResponse": {
                            "name": tool_name.strip(),
                            "response": {"result": result_text},
                        }
                    }
                ]
        return []

    def _sanitize_contents(self, contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Make conversation history safe for Gemini function-calling constraints."""
        sanitized: list[dict[str, Any]] = []

        for item in contents:
            role = item.get("role")
            parts = item.get("parts")
            if role not in {"user", "model"} or not isinstance(parts, list):
                continue

            text_parts = [
                {"text": p.get("text")}
                for p in parts
                if isinstance(p, dict) and isinstance(p.get("text"), str) and p.get("text").strip()
            ]
            function_call_parts = [
                {"functionCall": {"name": p["functionCall"]["name"], "args": p["functionCall"].get("args") if isinstance(p["functionCall"].get("args"), dict) else {}}}
                for p in parts
                if (
                    isinstance(p, dict)
                    and isinstance(p.get("functionCall"), dict)
                    and isinstance(p["functionCall"].get("name"), str)
                    and p["functionCall"]["name"]
                )
            ]
            function_response_parts = [
                {"functionResponse": {"name": p["functionResponse"]["name"], "response": p["functionResponse"].get("response") if isinstance(p["functionResponse"].get("response"), dict) else {"result": str(p["functionResponse"].get("response") or "")}}}
                for p in parts
                if (
                    isinstance(p, dict)
                    and isinstance(p.get("functionResponse"), dict)
                    and isinstance(p["functionResponse"].get("name"), str)
                    and p["functionResponse"]["name"]
                )
            ]

            if role == "model":
                if function_call_parts:
                    if not sanitized:
                        continue
                    # Gemini requires functionCall turns to come after user/functionResponse.
                    if sanitized[-1]["role"] != "user":
                        continue
                    sanitized.append({"role": "model", "parts": function_call_parts})
                    continue
                if text_parts:
                    sanitized.append({"role": "model", "parts": text_parts})
                continue

            # role == "user"
            if function_response_parts:
                if sanitized:
                    prev = sanitized[-1]
                    prev_has_fc = prev["role"] == "model" and any(
                        isinstance(p, dict) and "functionCall" in p for p in prev.get("parts", [])
                    )
                    if prev_has_fc:
                        sanitized.append({"role": "user", "parts": function_response_parts})
                        continue
                # Orphan functionResponse: keep explicit user text if present; otherwise drop.
                if text_parts:
                    sanitized.append({"role": "user", "parts": text_parts})
                continue

            if text_parts:
                sanitized.append({"role": "user", "parts": text_parts})

        # Gemini payloads should not start with a model turn.
        while sanitized and sanitized[0]["role"] != "user":
            sanitized.pop(0)
        return sanitized

    @staticmethod
    def _extract_text(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(part for part in parts if part.strip()).strip()
        return ""

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
