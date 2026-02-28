"""Google Gemini provider using raw httpx + SSE streaming."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any
from uuid import uuid4

import httpx

from app.services.llm.generic.base import LLMProvider
from app.services.llm.ids import ProviderId
from app.services.llm.generic.errors import TransientProviderError
from app.services.llm.providers.gemini_schema_cleaner import clean_schema_for_gemini
from app.services.llm.generic.types import (
    AgentEvent,
    AgentMessage,
    AssistantMessage,
    ImageContent,
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
    """Gemini provider adapter with SSE streaming + function-call normalization."""

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
        return ProviderId.GEMINI

    @property
    def provider_id(self) -> ProviderId:
        return ProviderId.GEMINI

    # ------------------------------------------------------------------
    # chat (non-streaming)
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str = "gemini-3-flash-preview",
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AssistantMessage:
        rc = reasoning_config or ReasoningConfig()
        thinking_budget = rc.thinking_budget if rc.thinking_budget and rc.thinking_budget > 0 else 0
        logger.info(
            "Gemini.chat: model=%s thinking_budget=%s tools=%d",
            model, thinking_budget, len(tools) if tools else 0,
        )

        payload = self._build_payload(
            messages,
            model,
            tools,
            temperature,
            thinking_budget,
            tool_choice=tool_choice,
        )
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
        model: str = "gemini-3-flash-preview",
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        rc = reasoning_config or ReasoningConfig()
        thinking_budget = rc.thinking_budget if rc.thinking_budget and rc.thinking_budget > 0 else 0
        logger.info(
            "Gemini.stream: model=%s thinking_budget=%s tools=%d",
            model, thinking_budget, len(tools) if tools else 0,
        )

        payload = self._build_payload(
            messages,
            model,
            tools,
            temperature,
            thinking_budget,
            tool_choice=tool_choice,
        )
        url = f"{self._base_url}/models/{model}:streamGenerateContent?alt=sse"

        started = False
        text_started = False
        thinking_started = False
        tool_index = 0
        response_has_function_call = False
        done_emitted = False
        saw_any_output = False
        saw_any_candidate = False
        last_finish_reason: str | None = None
        last_block_reason: str | None = None

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

                    prompt_feedback = chunk.get("promptFeedback")
                    if isinstance(prompt_feedback, dict):
                        block_reason = prompt_feedback.get("blockReason")
                        if isinstance(block_reason, str) and block_reason.strip():
                            last_block_reason = block_reason.strip()

                    candidates = chunk.get("candidates") or []
                    if not candidates:
                        continue
                    saw_any_candidate = True
                    candidate = candidates[0]
                    content = candidate.get("content") or {}
                    parts = content.get("parts") or []
                    finish_reason = candidate.get("finishReason")
                    if isinstance(finish_reason, str) and finish_reason.strip():
                        last_finish_reason = finish_reason

                    for part in parts:
                        if not isinstance(part, dict):
                            continue

                        # Thinking part
                        if part.get("thought") is True and "text" in part:
                            if not thinking_started:
                                thinking_started = True
                                yield AgentEvent(type="thinking_start", content_index=0)
                            signature = part.get("thoughtSignature")
                            signature_value = signature if isinstance(signature, str) and signature.strip() else None
                            yield AgentEvent(
                                type="thinking_delta",
                                content_index=0,
                                delta=part["text"],
                                signature=signature_value,
                            )
                            saw_any_output = True
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
                            saw_any_output = True
                            continue

                        # Function call part (arrives as complete object)
                        fc = part.get("functionCall")
                        if isinstance(fc, dict):
                            response_has_function_call = True
                            if text_started:
                                text_started = False
                                yield AgentEvent(type="text_end", content_index=0)
                            call_id = f"gemini_{uuid4().hex[:8]}"
                            args = fc.get("args") or {}
                            thought_sig = part.get("thoughtSignature")
                            thought_signature = (
                                thought_sig.strip()
                                if isinstance(thought_sig, str) and thought_sig.strip()
                                else None
                            )
                            yield AgentEvent(
                                type="toolcall_start",
                                content_index=tool_index,
                                tool_call=ToolCallContent(
                                    id=call_id,
                                    name=fc.get("name") or "",
                                    arguments={},
                                    thought_signature=thought_signature,
                                ),
                            )
                            yield AgentEvent(
                                type="toolcall_delta",
                                content_index=tool_index,
                                delta=json.dumps(args),
                            )
                            yield AgentEvent(type="toolcall_end", content_index=tool_index)
                            tool_index += 1
                            saw_any_output = True

                    if finish_reason and not done_emitted:
                        if (
                            not response_has_function_call
                            and not saw_any_output
                        ):
                            finish_upper = str(finish_reason).upper()
                            if finish_upper == "SAFETY" or last_block_reason:
                                reason = last_block_reason or finish_upper
                                raise RuntimeError(
                                    f"Gemini blocked response ({reason})"
                                )
                            raise TransientProviderError(
                                f"Gemini stream finished without content (finishReason={finish_reason})"
                            )
                        if thinking_started:
                            yield AgentEvent(type="thinking_end", content_index=0)
                        if text_started:
                            yield AgentEvent(type="text_end", content_index=0)
                        stop = (
                            "tool_use"
                            if response_has_function_call
                            else _map_finish_reason(finish_reason)
                        )
                        yield AgentEvent(type="done", stop_reason=stop)
                        done_emitted = True

        if not started:
            raise TransientProviderError("Gemini stream returned no data events")

        if not done_emitted:
            if thinking_started:
                yield AgentEvent(type="thinking_end", content_index=0)
            if text_started:
                yield AgentEvent(type="text_end", content_index=0)

            if response_has_function_call:
                yield AgentEvent(type="done", stop_reason="tool_use")
                return

            if saw_any_output:
                yield AgentEvent(
                    type="done",
                    stop_reason=_map_finish_reason(last_finish_reason),
                )
                return

            if last_block_reason:
                raise RuntimeError(f"Gemini blocked response ({last_block_reason})")
            if saw_any_candidate:
                raise TransientProviderError(
                    "Gemini stream ended without terminal content"
                )
            raise TransientProviderError(
                "Gemini stream ended without candidates"
            )

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
        *,
        tool_choice: str | None = None,
    ) -> dict[str, Any]:
        """Build a Gemini generateContent/streamGenerateContent request payload."""
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
            mode = "ANY" if tool_choice == "required" else "AUTO"
            payload["toolConfig"] = {"functionCallingConfig": {"mode": mode}}
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
                parts: list[dict[str, Any]] = []
                if isinstance(message.content, str):
                    text = message.content
                    if text.strip():
                        parts.append({"text": text})
                elif isinstance(message.content, list):
                    for item in message.content:
                        if isinstance(item, TextContent) and item.text:
                            parts.append({"text": item.text})
                        elif isinstance(item, ImageContent) and item.data:
                            parts.append(
                                {
                                    "inlineData": {
                                        "mimeType": item.media_type,
                                        "data": item.data,
                                    }
                                }
                            )
                if parts:
                    raw_contents.append({"role": "user", "parts": parts})
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
                            function_call_part: dict[str, Any] = {
                                "name": block.name,
                                "args": block.arguments,
                            }
                            part_payload: dict[str, Any] = {"functionCall": function_call_part}
                            if isinstance(block.thought_signature, str) and block.thought_signature.strip():
                                part_payload["thoughtSignature"] = block.thought_signature.strip()
                            function_parts.append(part_payload)
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
        """Normalize mixed dict message inputs into Gemini-compatible shape."""
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
        """Normalize dict message parts to Gemini `parts` payload entries."""
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
                    function_call: dict[str, Any] = {"name": fc["name"], "args": args}
                    normalized_part: dict[str, Any] = {"functionCall": function_call}
                    thought_signature = part.get("thoughtSignature")
                    if isinstance(thought_signature, str) and thought_signature.strip():
                        normalized_part["thoughtSignature"] = thought_signature.strip()
                    else:
                        nested_signature = fc.get("thoughtSignature")
                        if isinstance(nested_signature, str) and nested_signature.strip():
                            normalized_part["thoughtSignature"] = nested_signature.strip()
                            parts_out.append(normalized_part)
                            continue
                        alt_signature = fc.get("thought_signature")
                        if isinstance(alt_signature, str) and alt_signature.strip():
                            normalized_part["thoughtSignature"] = alt_signature.strip()
                    parts_out.append(normalized_part)
                    continue
                inline = part.get("inlineData")
                if (
                    mapped_role == "user"
                    and isinstance(inline, dict)
                    and isinstance(inline.get("data"), str)
                    and inline.get("data")
                ):
                    mime = inline.get("mimeType")
                    mime_type = mime if isinstance(mime, str) and mime else "image/png"
                    parts_out.append(
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": inline["data"],
                            }
                        }
                    )
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
                self._sanitize_function_call_part(p)
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
            image_parts = [
                {"inlineData": {"mimeType": p["inlineData"]["mimeType"], "data": p["inlineData"]["data"]}}
                for p in parts
                if (
                    isinstance(p, dict)
                    and isinstance(p.get("inlineData"), dict)
                    and isinstance(p["inlineData"].get("mimeType"), str)
                    and p["inlineData"]["mimeType"]
                    and isinstance(p["inlineData"].get("data"), str)
                    and p["inlineData"]["data"]
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
                    prev_has_fr = prev["role"] == "user" and any(
                        isinstance(p, dict) and "functionResponse" in p for p in prev.get("parts", [])
                    )
                    if prev_has_fc:
                        sanitized.append({"role": "user", "parts": function_response_parts})
                        continue
                    if prev_has_fr:
                        prev["parts"].extend(function_response_parts)
                        continue
                # Orphan functionResponse: keep explicit user text if present; otherwise drop.
                if text_parts:
                    sanitized.append({"role": "user", "parts": text_parts})
                continue

            if text_parts or image_parts:
                sanitized.append({"role": "user", "parts": [*text_parts, *image_parts]})

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
        """Parse non-streaming Gemini response JSON into typed assistant blocks."""
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
                thought_signature = part.get("thoughtSignature")
                blocks.append(
                    ThinkingContent(
                        thinking=part["text"],
                        signature=(
                            thought_signature.strip()
                            if isinstance(thought_signature, str) and thought_signature.strip()
                            else None
                        ),
                    )
                )
                continue
            # Function call
            fc = part.get("functionCall")
            if isinstance(fc, dict):
                has_function_call = True
                thought_signature = part.get("thoughtSignature")
                blocks.append(ToolCallContent(
                    id=f"gemini_{uuid4().hex[:8]}",
                    name=fc.get("name") or "",
                    arguments=fc.get("args") if isinstance(fc.get("args"), dict) else {},
                    thought_signature=(
                        thought_signature.strip()
                        if isinstance(thought_signature, str) and thought_signature.strip()
                        else None
                    ),
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

    @staticmethod
    def _sanitize_function_call_part(part: dict[str, Any]) -> dict[str, Any]:
        function_call = part.get("functionCall")
        if not isinstance(function_call, dict):
            return {"functionCall": {"name": "", "args": {}}}
        sanitized: dict[str, Any] = {
            "name": function_call["name"],
            "args": function_call.get("args") if isinstance(function_call.get("args"), dict) else {},
        }
        payload: dict[str, Any] = {"functionCall": sanitized}
        thought_signature = part.get("thoughtSignature")
        if isinstance(thought_signature, str) and thought_signature.strip():
            payload["thoughtSignature"] = thought_signature.strip()
        else:
            # Accept legacy/nested shapes for forward compatibility.
            nested_signature = function_call.get("thoughtSignature")
            if isinstance(nested_signature, str) and nested_signature.strip():
                payload["thoughtSignature"] = nested_signature.strip()
        return payload


def _map_finish_reason(reason: str | None) -> str:
    """Normalize Gemini finish reasons to Sentinel stop_reason values."""
    mapping = {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "stop",
        "RECITATION": "stop",
        "OTHER": "stop",
    }
    return mapping.get(reason or "", "stop")
