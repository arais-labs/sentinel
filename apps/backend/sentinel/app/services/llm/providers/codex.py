"""OpenAI Codex Responses API provider implementation."""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any
from uuid import uuid4

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

_PROMPT_CACHE_KEY_LIMIT = 256
_REASONING_INCLUDE_KEY = "reasoning.encrypted_content"
_TOOL_CHOICE_VALUES = {"auto", "required", "none"}
_MODELS_CACHE_TTL_SECONDS = 300.0
_MODELS_CLIENT_VERSION = "0.1.0"

_CODEX_EXECUTION_MARKER = "You are Codex, a coding agent running in Sentinel."
_CODEX_EXECUTION_PRELUDE = (
    f"{_CODEX_EXECUTION_MARKER}\n\n"
    "Keep acting until the task is complete. Do not stop at analysis if the user asked for execution.\n"
    "When verification is possible via tools, run the tools instead of guessing.\n"
    "Do not claim commands were run or files were changed unless tool output confirms it.\n"
    "If blocked, state the concrete blocker and the best immediate next action."
)


class CodexProvider(LLMProvider):
    """OpenAI Codex OAuth provider — Responses API via chatgpt.com/backend-api.

    The Codex endpoint uses the OpenAI *Responses* API format which is
    fundamentally different from Chat Completions:
      - ``instructions`` (system prompt) + ``input`` (message list)
      - ``stream: true`` is mandatory
      - SSE events use ``response.*`` event types
      - Tool schemas are flat (no nested ``function`` wrapper)
    """

    def __init__(
        self,
        oauth_token: str,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._api_key = oauth_token
        self._base_url = "https://chatgpt.com/backend-api"
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=120))
        self._prompt_cache_namespace = uuid4().hex[:12]
        self._prompt_cache_keys: OrderedDict[str, str] = OrderedDict()
        self._model_catalog_by_slug: dict[str, dict[str, Any]] = {}
        self._model_catalog_expiry = 0.0

    @property
    def name(self) -> str:
        return ProviderId.OPENAI_CODEX

    @property
    def provider_id(self) -> ProviderId:
        return ProviderId.OPENAI_CODEX

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AssistantMessage:
        """Non-streaming call — internally streams and collects the result."""
        content_blocks: list[TextContent | ThinkingContent | ToolCallContent] = []
        usage = TokenUsage(input_tokens=0, output_tokens=0)
        stop_reason = "stop"
        result_model = model

        # Accumulate streaming chunks into a full response
        text_parts: dict[int, str] = {}
        tool_calls: dict[int, dict[str, Any]] = {}

        async for event in self.stream(
            messages,
            model,
            tools,
            temperature,
            reasoning_config,
            tool_choice,
        ):
            if event.type == "text_delta" and event.delta:
                idx = event.content_index or 0
                text_parts[idx] = text_parts.get(idx, "") + event.delta
            elif event.type == "toolcall_start" and event.tool_call:
                idx = event.content_index or 0
                tool_calls[idx] = {
                    "id": event.tool_call.id,
                    "name": event.tool_call.name,
                    "arguments_raw": "",
                }
            elif event.type == "toolcall_delta" and event.delta is not None:
                idx = event.content_index or 0
                if idx in tool_calls:
                    tool_calls[idx]["arguments_raw"] += event.delta
            elif event.type == "done":
                stop_reason = event.stop_reason or "stop"

        # Build content blocks
        for idx in sorted(text_parts):
            content_blocks.append(TextContent(text=text_parts[idx]))

        for idx in sorted(tool_calls):
            tc = tool_calls[idx]
            raw = tc["arguments_raw"]
            try:
                parsed = json.loads(raw)
                arguments = parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                arguments = {"raw": raw}
            content_blocks.append(
                ToolCallContent(id=tc["id"], name=tc["name"], arguments=arguments)
            )

        return AssistantMessage(
            content=content_blocks,
            model=result_model,
            provider=self.name,
            usage=usage,
            stop_reason=stop_reason,
        )

    async def stream(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        rc = reasoning_config or ReasoningConfig()
        _ = temperature
        instructions, input_items = self._to_codex_input(messages)
        effective_instructions = self._with_codex_execution_prelude(
            instructions or "You are a helpful assistant."
        )
        tool_entries = [self._tool_schema(t) for t in tools] if tools else []

        started = False
        text_started: set[int] = set()
        text_delta_seen: set[int] = set()
        text_ended: set[int] = set()
        thinking_started: set[int] = set()
        tool_indices: dict[str, int] = {}  # item_id → output_index
        tool_argument_buffers: dict[str, str] = {}  # item_id → emitted arguments
        saw_tool_calls = False
        done_emitted = False
        sse_event_name: str | None = None

        async with self._client_factory() as client:
            supports_parallel_tool_calls = await self._parallel_tools_supported_for_model(
                model=model,
                client=client,
            )
            payload: dict[str, Any] = {
                "model": model,
                "instructions": effective_instructions,
                "input": input_items,
                "tools": tool_entries,
                "tool_choice": self._normalize_tool_choice(tool_choice),
                "parallel_tool_calls": supports_parallel_tool_calls,
                "stream": True,
                "store": False,
                "include": [],
                "prompt_cache_key": self._prompt_cache_key(
                    model=model,
                    instructions=effective_instructions,
                    tools=tools,
                ),
            }
            reasoning_payload = self._reasoning_payload(rc)
            if reasoning_payload is not None:
                payload["reasoning"] = reasoning_payload
                payload["include"] = [_REASONING_INCLUDE_KEY]

            async with client.stream(
                "POST",
                f"{self._base_url}/codex/responses",
                json=payload,
                headers=self._headers(),
            ) as response:
                if response.is_error:
                    body = await response.aread()
                    detail = body.decode("utf-8", errors="replace").strip()
                    raise RuntimeError(
                        f"Codex stream http_{response.status_code}: {detail[:500]}"
                    )

                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line:
                        sse_event_name = None
                        continue

                    # SSE format: "event: <type>\ndata: <json>"
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        sse_event_name = event_name if event_name else None
                        continue
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    if data_str == "[DONE]":
                        if not done_emitted:
                            yield AgentEvent(
                                type="done",
                                stop_reason="tool_use" if saw_tool_calls else "stop",
                            )
                            done_emitted = True
                        break

                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        if sse_event_name == "error":
                            raise RuntimeError(f"Codex stream sse_error: {data_str[:500]}")
                        continue
                    if not isinstance(event, dict):
                        continue
                    etype = str(event.get("type") or sse_event_name or "")

                    # --- response lifecycle ---
                    if etype == "response.created":
                        if not started:
                            started = True
                            yield AgentEvent(type="start")
                    elif etype in {"error", "response.error", "response.failed"}:
                        raise RuntimeError(
                            f"Codex stream sse_error: {self._error_message(event)}"
                        )

                    # --- text streaming ---
                    elif etype == "response.content_part.added":
                        idx = int(event.get("content_index") or event.get("output_index") or 0)
                        if idx not in text_started:
                            text_started.add(idx)
                            yield AgentEvent(type="text_start", content_index=idx)

                    elif etype == "response.output_text.delta":
                        idx = int(event.get("content_index") or event.get("output_index") or 0)
                        delta = event.get("delta", "")
                        if delta:
                            if idx not in text_started:
                                text_started.add(idx)
                                yield AgentEvent(type="text_start", content_index=idx)
                            text_delta_seen.add(idx)
                            yield AgentEvent(type="text_delta", content_index=idx, delta=delta)

                    elif etype == "response.output_text.done":
                        idx = int(event.get("content_index") or event.get("output_index") or 0)
                        if idx in text_started and idx not in text_ended:
                            text_ended.add(idx)
                            yield AgentEvent(type="text_end", content_index=idx)

                    # --- tool call streaming ---
                    elif etype == "response.output_item.added":
                        item = event.get("item", {})
                        if item.get("type") == "function_call":
                            saw_tool_calls = True
                            out_idx = event.get("output_index", 0)
                            item_id = str(item.get("id") or f"idx:{out_idx}")
                            tool_indices[item_id] = out_idx
                            tool_argument_buffers.setdefault(item_id, "")
                            yield AgentEvent(
                                type="toolcall_start",
                                content_index=out_idx,
                                tool_call=ToolCallContent(
                                    id=str(item.get("call_id") or item.get("id") or ""),
                                    name=str(item.get("name") or ""),
                                    arguments={},
                                ),
                            )
                            initial_arguments = item.get("arguments")
                            if isinstance(initial_arguments, str) and initial_arguments:
                                if item_id:
                                    tool_argument_buffers[item_id] = initial_arguments
                                yield AgentEvent(
                                    type="toolcall_delta",
                                    content_index=out_idx,
                                    delta=initial_arguments,
                                )

                    elif etype == "response.function_call_arguments.delta":
                        item_id = str(
                            event.get("item_id")
                            or f"idx:{int(event.get('output_index') or 0)}"
                        )
                        out_idx = tool_indices.get(item_id, event.get("output_index", 0))
                        delta = event.get("delta", "")
                        if delta:
                            if item_id:
                                tool_argument_buffers[item_id] = (
                                    tool_argument_buffers.get(item_id, "") + delta
                                )
                            yield AgentEvent(
                                type="toolcall_delta",
                                content_index=out_idx,
                                delta=delta,
                            )

                    elif etype == "response.function_call_arguments.done":
                        item_id = str(
                            event.get("item_id")
                            or f"idx:{int(event.get('output_index') or 0)}"
                        )
                        arguments = event.get("arguments")
                        if isinstance(arguments, str) and arguments:
                            already_emitted = tool_argument_buffers.get(item_id, "")
                            if not already_emitted:
                                out_idx = tool_indices.get(item_id, event.get("output_index", 0))
                                tool_argument_buffers[item_id] = arguments
                                yield AgentEvent(
                                    type="toolcall_delta",
                                    content_index=out_idx,
                                    delta=arguments,
                                )

                    elif etype == "response.output_item.done":
                        item = event.get("item", {})
                        if item.get("type") == "function_call":
                            saw_tool_calls = True
                            item_id = item.get("id", "")
                            out_idx = tool_indices.get(item_id, event.get("output_index", 0))
                            arguments = item.get("arguments")
                            already_emitted = tool_argument_buffers.get(item_id, "")
                            if (
                                isinstance(arguments, str)
                                and arguments
                                and not already_emitted
                            ):
                                tool_argument_buffers[item_id] = arguments
                                yield AgentEvent(
                                    type="toolcall_delta",
                                    content_index=out_idx,
                                    delta=arguments,
                                )
                            yield AgentEvent(type="toolcall_end", content_index=out_idx)
                        elif item.get("type") == "message":
                            out_idx = int(event.get("output_index") or event.get("content_index") or 0)
                            text_value = self._extract_message_output_text(item)
                            if text_value and out_idx not in text_delta_seen:
                                if out_idx not in text_started:
                                    text_started.add(out_idx)
                                    yield AgentEvent(type="text_start", content_index=out_idx)
                                yield AgentEvent(
                                    type="text_delta",
                                    content_index=out_idx,
                                    delta=text_value,
                                )
                            if out_idx in text_started and out_idx not in text_ended:
                                text_ended.add(out_idx)
                                yield AgentEvent(type="text_end", content_index=out_idx)

                    # --- reasoning deltas ---
                    elif etype == "response.reasoning_summary_part.added":
                        idx = int(event.get("summary_index") or 0)
                        if idx not in thinking_started:
                            thinking_started.add(idx)
                            yield AgentEvent(type="thinking_start", content_index=idx)

                    elif etype in {
                        "response.reasoning_text.delta",
                        "response.reasoning_summary_text.delta",
                    }:
                        idx = int(
                            event.get("content_index")
                            or event.get("summary_index")
                            or 0
                        )
                        delta = event.get("delta", "")
                        if isinstance(delta, str) and delta:
                            if idx not in thinking_started:
                                thinking_started.add(idx)
                                yield AgentEvent(type="thinking_start", content_index=idx)
                            yield AgentEvent(
                                type="thinking_delta",
                                content_index=idx,
                                delta=delta,
                            )

                    elif etype in {
                        "response.reasoning_text.done",
                        "response.reasoning_summary_text.done",
                    }:
                        idx = int(
                            event.get("content_index")
                            or event.get("summary_index")
                            or 0
                        )
                        if idx in thinking_started:
                            yield AgentEvent(type="thinking_end", content_index=idx)

                    # --- completion ---
                    elif etype in {"response.completed", "response.done"}:
                        resp = event.get("response", {})
                        if not isinstance(resp, dict):
                            resp = {}
                        output = resp.get("output", [])
                        if isinstance(output, list):
                            saw_tool_calls = saw_tool_calls or any(
                                isinstance(item, dict) and item.get("type") == "function_call"
                                for item in output
                            )
                        stop_reason = self._stop_reason(resp, saw_tool_calls=saw_tool_calls)
                        yield AgentEvent(
                            type="done",
                            stop_reason=stop_reason,
                        )
                        done_emitted = True

                if not done_emitted:
                    yield AgentEvent(
                        type="done",
                        stop_reason="tool_use" if saw_tool_calls else "stop",
                    )

    # ------------------------------------------------------------------
    # Message conversion
    # ------------------------------------------------------------------

    def _to_codex_input(
        self, messages: Sequence[AgentMessage | dict]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert internal messages to Codex Responses API format.

        Returns ``(instructions, input_items)`` where instructions is the
        system prompt and input_items is the ``input`` array.
        """
        instructions_parts: list[str] = []
        input_items: list[dict[str, Any]] = []

        for message in messages:
            # Raw dict passthrough
            if isinstance(message, dict):
                role = message.get("role", "user")
                content = message.get("content", "")
                if role == "system":
                    instructions_parts.append(content)
                elif role == "assistant":
                    # Check for tool calls
                    tool_calls = message.get("tool_calls")
                    if tool_calls:
                        for tc in tool_calls:
                            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                            input_items.append({
                                "type": "function_call",
                                "call_id": tc.get("id", ""),
                                "name": fn.get("name", ""),
                                "arguments": fn.get("arguments", "{}"),
                            })
                    if content:
                        input_items.append({"role": "assistant", "content": content})
                elif role == "tool":
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": message.get("tool_call_id", ""),
                        "output": content if isinstance(content, str) else json.dumps(content),
                    })
                else:
                    input_items.append({"role": "user", "content": content})
                continue

            # ToolResultMessage
            if isinstance(message, ToolResultMessage):
                input_items.append({
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content if isinstance(message.content, str) else json.dumps(message.content),
                })
                continue

            role = getattr(message, "role", "user")
            content = getattr(message, "content", "")

            # System message → instructions
            if role == "system":
                if isinstance(content, str):
                    instructions_parts.append(content)
                continue

            # Assistant message (may contain text + tool calls)
            if role == "assistant" and isinstance(content, list):
                text_parts = [item.text for item in content if isinstance(item, TextContent)]
                for item in content:
                    if isinstance(item, ToolCallContent):
                        input_items.append({
                            "type": "function_call",
                            "call_id": item.id,
                            "name": item.name,
                            "arguments": json.dumps(item.arguments),
                        })
                if text_parts:
                    input_items.append({"role": "assistant", "content": "\n".join(text_parts)})
                continue

            # User message
            if isinstance(message, UserMessage) and isinstance(content, list):
                has_images = any(isinstance(item, ImageContent) and item.data for item in content)
                if has_images:
                    parts: list[dict[str, Any]] = []
                    for item in content:
                        if isinstance(item, TextContent) and item.text:
                            parts.append({"type": "input_text", "text": item.text})
                        elif isinstance(item, ImageContent) and item.data:
                            parts.append(
                                {
                                    "type": "input_image",
                                    "image_url": f"data:{item.media_type};base64,{item.data}",
                                }
                            )
                    if parts:
                        input_items.append({"role": "user", "content": parts})
                    continue
                text = "\n".join(item.text for item in content if isinstance(item, TextContent))
                input_items.append({"role": "user", "content": text})
                continue

            input_items.append({
                "role": role,
                "content": content if isinstance(content, str) else "",
            })

        return "\n\n".join(instructions_parts), input_items

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }

    @staticmethod
    def _tool_schema(tool: ToolSchema) -> dict[str, Any]:
        """Codex uses flat tool schemas (no nested ``function`` wrapper)."""
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            "strict": False,
        }

    @staticmethod
    def _normalize_tool_choice(tool_choice: str | None) -> str:
        if tool_choice in _TOOL_CHOICE_VALUES:
            return tool_choice
        return "auto"

    @staticmethod
    def _extract_message_output_text(item: dict[str, Any]) -> str:
        content = item.get("content")
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type not in {"output_text", "text"}:
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        return "".join(parts)

    @staticmethod
    def _with_codex_execution_prelude(instructions: str) -> str:
        normalized = instructions.strip()
        if _CODEX_EXECUTION_MARKER in normalized:
            return normalized
        if not normalized:
            return _CODEX_EXECUTION_PRELUDE
        return f"{_CODEX_EXECUTION_PRELUDE}\n\n{normalized}"

    @staticmethod
    def _reasoning_payload(reasoning_config: ReasoningConfig) -> dict[str, Any] | None:
        effort = reasoning_config.reasoning_effort
        if not effort:
            return None
        return {"effort": effort}

    def _prompt_cache_key(
        self,
        *,
        model: str,
        instructions: str,
        tools: Sequence[ToolSchema] | None,
    ) -> str:
        tool_fingerprint = [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in (tools or [])
        ]
        fingerprint_source = json.dumps(
            {
                "model": model,
                "instructions": instructions,
                "tools": tool_fingerprint,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
        existing = self._prompt_cache_keys.get(fingerprint)
        if existing is not None:
            self._prompt_cache_keys.move_to_end(fingerprint)
            return existing

        key = f"{self._prompt_cache_namespace}:{fingerprint[:20]}"
        self._prompt_cache_keys[fingerprint] = key
        if len(self._prompt_cache_keys) > _PROMPT_CACHE_KEY_LIMIT:
            self._prompt_cache_keys.popitem(last=False)
        return key

    @staticmethod
    def _error_message(event: dict[str, Any]) -> str:
        error = event.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
            return json.dumps(error)[:500]
        if isinstance(error, str) and error.strip():
            return error.strip()
        return json.dumps(event)[:500]

    @staticmethod
    def _stop_reason(response: dict[str, Any], *, saw_tool_calls: bool) -> str:
        if saw_tool_calls:
            return "tool_use"

        status = str(response.get("status") or "").lower()
        if status == "cancelled":
            return "aborted"
        if status == "failed":
            return "error"

        incomplete = response.get("incomplete_details")
        if isinstance(incomplete, dict):
            reason = str(incomplete.get("reason") or "").lower()
            if reason in {"max_output_tokens", "max_completion_tokens"}:
                return "length"
        return "stop"

    async def _parallel_tools_supported_for_model(
        self,
        *,
        model: str,
        client: httpx.AsyncClient,
    ) -> bool:
        catalog = await self._load_model_catalog(client)
        if not catalog:
            return False
        model_info = catalog.get(model)
        if not isinstance(model_info, dict):
            return False
        return bool(model_info.get("supports_parallel_tool_calls"))

    async def _load_model_catalog(
        self,
        client: httpx.AsyncClient,
    ) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        if now < self._model_catalog_expiry and self._model_catalog_by_slug:
            return self._model_catalog_by_slug

        try:
            response = await client.get(
                f"{self._base_url}/codex/models",
                params={"client_version": _MODELS_CLIENT_VERSION},
                headers=self._headers(),
            )
            status_code = int(getattr(response, "status_code", 200))
            if status_code >= 400:
                self._model_catalog_expiry = now + 30.0
                return self._model_catalog_by_slug
            payload = response.json()
        except Exception:
            self._model_catalog_expiry = now + 30.0
            return self._model_catalog_by_slug

        models = payload.get("models") if isinstance(payload, dict) else None
        parsed: dict[str, dict[str, Any]] = {}
        if isinstance(models, list):
            for entry in models:
                if not isinstance(entry, dict):
                    continue
                slug = entry.get("slug")
                if isinstance(slug, str) and slug:
                    parsed[slug] = entry
        if parsed:
            self._model_catalog_by_slug = parsed
            self._model_catalog_expiry = now + _MODELS_CACHE_TTL_SECONDS
        else:
            self._model_catalog_expiry = now + 30.0
        return self._model_catalog_by_slug
