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

    @property
    def name(self) -> str:
        return "openai-codex"

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
    ) -> AssistantMessage:
        """Non-streaming call — internally streams and collects the result."""
        content_blocks: list[TextContent | ThinkingContent | ToolCallContent] = []
        usage = TokenUsage(input_tokens=0, output_tokens=0)
        stop_reason = "stop"
        result_model = model

        # Accumulate streaming chunks into a full response
        text_parts: dict[int, str] = {}
        tool_calls: dict[int, dict[str, Any]] = {}

        async for event in self.stream(messages, model, tools, temperature, reasoning_config):
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
    ) -> AsyncIterator[AgentEvent]:
        instructions, input_items = self._to_codex_input(messages)

        payload: dict[str, Any] = {
            "model": model,
            "instructions": instructions or "You are a helpful assistant.",
            "input": input_items,
            "stream": True,
            "store": False,
        }
        if tools:
            payload["tools"] = [self._tool_schema(t) for t in tools]

        started = False
        text_started: set[int] = set()
        tool_indices: dict[str, int] = {}  # item_id → output_index

        async with self._client_factory() as client:
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

                    # SSE format: "event: <type>\ndata: <json>"
                    if line.startswith("event:"):
                        continue  # we parse the data line
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str:
                        continue

                    event = json.loads(data_str)
                    etype = event.get("type", "")

                    # --- response lifecycle ---
                    if etype == "response.created":
                        if not started:
                            started = True
                            yield AgentEvent(type="start")

                    # --- text streaming ---
                    elif etype == "response.content_part.added":
                        idx = event.get("content_index", 0)
                        if idx not in text_started:
                            text_started.add(idx)
                            yield AgentEvent(type="text_start", content_index=idx)

                    elif etype == "response.output_text.delta":
                        idx = event.get("content_index", 0)
                        delta = event.get("delta", "")
                        if delta:
                            if idx not in text_started:
                                text_started.add(idx)
                                yield AgentEvent(type="text_start", content_index=idx)
                            yield AgentEvent(type="text_delta", content_index=idx, delta=delta)

                    elif etype == "response.output_text.done":
                        idx = event.get("content_index", 0)
                        if idx in text_started:
                            yield AgentEvent(type="text_end", content_index=idx)

                    # --- tool call streaming ---
                    elif etype == "response.output_item.added":
                        item = event.get("item", {})
                        if item.get("type") == "function_call":
                            out_idx = event.get("output_index", 0)
                            item_id = item.get("id", "")
                            tool_indices[item_id] = out_idx
                            yield AgentEvent(
                                type="toolcall_start",
                                content_index=out_idx,
                                tool_call=ToolCallContent(
                                    id=item.get("call_id", ""),
                                    name=item.get("name", ""),
                                    arguments={},
                                ),
                            )

                    elif etype == "response.function_call_arguments.delta":
                        item_id = event.get("item_id", "")
                        out_idx = tool_indices.get(item_id, event.get("output_index", 0))
                        delta = event.get("delta", "")
                        if delta:
                            yield AgentEvent(
                                type="toolcall_delta",
                                content_index=out_idx,
                                delta=delta,
                            )

                    elif etype == "response.output_item.done":
                        item = event.get("item", {})
                        if item.get("type") == "function_call":
                            item_id = item.get("id", "")
                            out_idx = tool_indices.get(item_id, event.get("output_index", 0))
                            yield AgentEvent(type="toolcall_end", content_index=out_idx)

                    # --- completion ---
                    elif etype == "response.completed":
                        resp = event.get("response", {})
                        output = resp.get("output", [])
                        has_tool_calls = any(
                            o.get("type") == "function_call" for o in output
                        )
                        yield AgentEvent(
                            type="done",
                            stop_reason="tool_use" if has_tool_calls else "stop",
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
        }
