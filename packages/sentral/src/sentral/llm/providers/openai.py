"""OpenAI provider, with Responses for current native OpenAI models."""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import aclosing
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
    UserMessage,
)
from sentral.llm.http_pool import provider_http_client
from sentral.llm.ids import ProviderId
from sentral.llm.pricing import openai_token_price
from sentral.llm.providers.fast_mode import matches_model


class OpenAIProvider(LLMProvider):
    """OpenAI adapter for Responses and compatible Chat Completions endpoints."""

    _chat_endpoint: str = "/chat/completions"
    _responses_endpoint: str = "/responses"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory or provider_http_client

    @property
    def name(self) -> str:
        return ProviderId.OPENAI

    @property
    def provider_id(self) -> ProviderId:
        return ProviderId.OPENAI

    def supports_fast_mode(self, model: str) -> bool:

        # https://developers.openai.com/api/docs/pricing (Fast mode), September 2026.
        return self._base_url == "https://api.openai.com/v1" and matches_model(
            model,
            (
                "gpt-6-astra",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-5.6-luna",
                "gpt-5.5",
                "gpt-5.4",
                "gpt-5.3-codex",
            ),
        )

    def _speed_options(self, model: str, rc: ReasoningConfig) -> dict[str, str]:
        if rc.fast_mode and not self.supports_fast_mode(model):
            raise ValueError("Fast mode is not supported by this provider and model")
        if self.supports_fast_mode(model):
            return {"service_tier": "fast" if rc.fast_mode else "default"}
        return {}

    def _payload_extras(self) -> dict[str, Any]:
        """Hook for subclasses to inject extra payload fields."""
        return {}

    async def count_input_tokens(self, messages, model, tools=None, reasoning_config=None):
        if self.name == ProviderId.OPENAI_CODEX or self._base_url != "https://api.openai.com/v1":
            return None
        instructions, items = self._to_responses_input(messages)
        payload = {
            "model": model,
            "instructions": instructions,
            "input": items,
            "tools": [self._response_tool(tool) for tool in tools or []],
        }
        await self._prepare_response(payload, None, {})
        async with self._client_factory() as client:
            response = await client.post(
                f"{self._base_url}/responses/input_tokens",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
            return int(response.json()["input_tokens"])

    async def chat(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AssistantMessage:
        if self._uses_responses(model):
            async with aclosing(
                self._responses_stream(messages, model, tools, reasoning_config, tool_choice)
            ) as events:
                async for event in events:
                    if event.type == "done" and event.message:
                        return event.message
            raise RuntimeError("OpenAI response ended without completion")
        rc = reasoning_config or ReasoningConfig()
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._to_openai_messages(messages),
            "temperature": temperature,
            "max_completion_tokens": rc.max_tokens,
            **self._payload_extras(),
            **self._speed_options(model, rc),
        }
        if rc.reasoning_effort:
            payload["reasoning_effort"] = rc.reasoning_effort
        if tools:
            payload["tools"] = [self._tool_schema(tool) for tool in tools]
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

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
        tool_choice: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        if self._uses_responses(model):
            async with aclosing(
                self._responses_stream(messages, model, tools, reasoning_config, tool_choice)
            ) as events:
                async for event in events:
                    yield event
            return
        rc = reasoning_config or ReasoningConfig()
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._to_openai_messages(messages),
            "temperature": temperature,
            "stream": True,
            "max_completion_tokens": rc.max_tokens,
            **self._payload_extras(),
            **self._speed_options(model, rc),
        }
        if rc.reasoning_effort:
            payload["reasoning_effort"] = rc.reasoning_effort
        if tools:
            payload["tools"] = [self._tool_schema(tool) for tool in tools]
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

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
                    raise RuntimeError(f"OpenAI stream http_{response.status_code}: {snippet}")
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
                        message = (
                            error.get("message")
                            if isinstance(error, dict)
                            else "Provider stream error"
                        )
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
                        fn = (
                            tool_delta.get("function")
                            if isinstance(tool_delta.get("function"), dict)
                            else {}
                        )
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
                        yield AgentEvent(
                            type="done", stop_reason=_map_openai_finish_reason(finish_reason)
                        )

    def _uses_responses(self, model: str) -> bool:
        # Do not change the wire protocol of generic OpenAI-compatible providers.
        return (
            self.provider_id == ProviderId.OPENAI and self._base_url == "https://api.openai.com/v1"
        )

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
                has_images = any(isinstance(item, ImageContent) and item.data for item in content)
                if has_images:
                    parts: list[dict[str, Any]] = []
                    for item in content:
                        if isinstance(item, TextContent) and item.text:
                            parts.append({"type": "text", "text": item.text})
                        elif isinstance(item, ImageContent) and item.data:
                            parts.append(
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{item.media_type};base64,{item.data}",
                                    },
                                }
                            )
                    if parts:
                        converted.append({"role": "user", "content": parts})
                    continue
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

    def _responses_message(self, response, model, output):
        content = []
        for item in output:
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content") or []:
                    text = (
                        part.get("text")
                        if part.get("type") == "output_text"
                        else part.get("refusal")
                    )
                    if text:
                        content.append(TextContent(text=text))
            elif kind == "reasoning":
                summary = "\n".join(p.get("text", "") for p in item.get("summary") or [])
                if summary:
                    content.append(ThinkingContent(thinking=summary))
            elif kind == "function_call":
                raw = item.get("arguments") or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else raw
                    if not isinstance(args, dict):
                        args = {"value": args}
                except json.JSONDecodeError:
                    args = {"raw": raw}
                content.append(
                    ToolCallContent(
                        id=item.get("call_id", ""), name=item.get("name", ""), arguments=args
                    )
                )
        usage = response.get("usage") or {}
        provider_usage = None
        if isinstance(response.get("usage"), dict):
            resolved_model = response.get("model") or model
            service_tier = response.get("service_tier") or "unknown"
            is_codex = self.name == ProviderId.OPENAI_CODEX
            price = None
            if is_codex or self._base_url == "https://api.openai.com/v1":
                price = openai_token_price(resolved_model, usage, service_tier)
            provider_usage = {
                "response_id": response.get("id"),
                "model": resolved_model,
                "provider": self.name,
                "service_tier": service_tier,
                "usage": copy.deepcopy(usage),
                "price": price,
                "price_kind": "api_equivalent" if is_codex else "api_list_price",
            }
        reason = "tool_use" if any(isinstance(c, ToolCallContent) for c in content) else "stop"
        if (
            reason == "stop"
            and any(item.get("type") in {"program", "program_output"} for item in output)
            and not any(
                item.get("type") == "message" and item.get("phase") != "commentary"
                for item in output
            )
        ):
            reason = "pause_turn"
        if response.get("status") == "cancelled":
            reason = "aborted"
        elif response.get("status") == "incomplete":
            reason = (
                "length"
                if (response.get("incomplete_details") or {}).get("reason") == "max_output_tokens"
                else "error"
            )
        return AssistantMessage(
            content=content,
            model=response.get("model") or model,
            provider=self.name,
            usage=TokenUsage(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            ),
            stop_reason=reason,
            responses_output=copy.deepcopy(output),
            provider_usage=provider_usage,
        )

    def _to_responses_input(self, messages):
        instructions, items = [], []
        callers = {}
        for message in messages:
            native = (
                message
                if isinstance(message, dict)
                else {
                    "provider": getattr(message, "provider", ""),
                    "responses_output": getattr(message, "responses_output", None),
                    "responses_context_reset": getattr(message, "responses_context_reset", False),
                }
            )
            if native.get("provider") == self.name and native.get("responses_output"):
                if native.get("responses_context_reset") or any(
                    item.get("type") == "compaction" for item in native["responses_output"]
                ):
                    # A native compaction is a replacement, not an append to the
                    # original overlong prefix. Stored DB history remains intact.
                    items = []
                    callers = {}
                for item in native["responses_output"]:
                    if item.get("type") == "function_call" and item.get("caller"):
                        callers[item["call_id"]] = copy.deepcopy(item["caller"])
                items.extend(copy.deepcopy(native["responses_output"]))
                continue
            converted = self._to_openai_messages([message])[0]
            role, content = converted.get("role", "user"), converted.get("content", "")
            if role == "system":
                instructions.append(content)
            elif role in {"tool", "tool_result"}:
                if converted.get("tool_call_id"):
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": converted["tool_call_id"],
                            "output": (
                                content if isinstance(content, (str, list)) else json.dumps(content)
                            ),
                        }
                    )
                    if converted["tool_call_id"] in callers:
                        items[-1]["caller"] = callers[converted["tool_call_id"]]
            else:
                for call in converted.get("tool_calls") or []:
                    function = call.get("function") or {}
                    if call.get("id") and function.get("name"):
                        items.append({"type": "function_call", "call_id": call["id"], **function})
                if isinstance(content, list):
                    content = [
                        (
                            {"type": "input_image", "image_url": part["image_url"]["url"]}
                            if part.get("type") == "image_url"
                            else {"type": "input_text", "text": part.get("text", "")}
                        )
                        for part in content
                    ]
                if content or role != "assistant":
                    items.append({"role": role, "content": content})
        return "\n\n".join(instructions), items

    async def _prepare_response(self, payload, client, headers):
        """Enable hosted orchestration only on the documented public model family."""
        if self._base_url != "https://api.openai.com/v1" or not payload["model"].startswith(
            ("gpt-6-", "gpt-5.6")
        ):
            return
        # Slow HTTP requests may overlap independent model work. Interactive and
        # delegated tools retain the normal approval/interception flow.
        if payload["model"].startswith("gpt-6-"):
            for tool in payload["tools"]:
                if tool.get("name") == "http_request":
                    tool["async"] = True
        eligible = [
            tool
            for tool in payload["tools"]
            if tool.get("type") == "function"
            and not tool.get("async")
            and tool.get("name") not in {"form", "sub_agents"}
            and '"$ref"' not in json.dumps(tool.get("parameters", {}))
        ]
        if eligible and payload.get("tool_choice", "auto") == "auto":
            for tool in eligible:
                tool["allowed_callers"] = ["direct", "programmatic"]
            payload["tools"].append({"type": "programmatic_tool_calling"})

    def _response_tool(self, tool):
        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            "strict": False,
        }

    async def _stream_lines(self, client, payload, headers):
        async with client.stream(
            "POST", f"{self._base_url}{self._responses_endpoint}", json=payload, headers=headers
        ) as response:
            if response.is_error:
                raise RuntimeError(f"{self.name} Responses http_{response.status_code}")
            async for line in response.aiter_lines():
                yield line

    async def _responses_stream(
        self, messages, model, tools=None, reasoning_config=None, tool_choice=None
    ):
        rc = reasoning_config or ReasoningConfig()
        instructions, items = self._to_responses_input(messages)
        payload = {
            "model": model,
            "instructions": instructions,
            "input": items,
            "tools": [self._response_tool(tool) for tool in tools or []],
            "tool_choice": tool_choice or "auto",
            "stream": True,
            "store": False,
            "max_output_tokens": rc.max_tokens,
            **self._speed_options(model, rc),
            "include": ["reasoning.encrypted_content"],
        }
        if rc.reasoning_effort:
            effort = rc.reasoning_effort
            if effort == "minimal" or (model.startswith("gpt-6") and effort == "none"):
                effort = "low"
            payload["reasoning"] = {"effort": effort}
        output, text, arguments, item_indices = {}, {}, {}, {}
        started_text, ended_text, started_tools, ended_tools, thinking = (
            set(),
            set(),
            set(),
            set(),
            set(),
        )

        def emit_item(index, item, done):
            output[index] = copy.deepcopy(item)
            if item.get("id"):
                item_indices[item["id"]] = index
            if item.get("type") == "function_call":
                if index not in started_tools:
                    started_tools.add(index)
                    yield AgentEvent(
                        type="toolcall_start",
                        content_index=index,
                        tool_call=ToolCallContent(
                            id=item.get("call_id") or item.get("id", ""), name=item.get("name", "")
                        ),
                    )
                full, previous = item.get("arguments") or "", arguments.get(index, "")
                if full.startswith(previous) and len(full) > len(previous):
                    arguments[index] = full
                    yield AgentEvent(
                        type="toolcall_delta", content_index=index, delta=full[len(previous) :]
                    )
                if done and index not in ended_tools:
                    ended_tools.add(index)
                    yield AgentEvent(type="toolcall_end", content_index=index)
            elif done and item.get("type") == "message":
                full = "".join(
                    part.get("text", part.get("refusal", "")) for part in item.get("content") or []
                )
                if full and not text.get(index):
                    started_text.add(index)
                    text[index] = full
                    yield AgentEvent(type="text_start", content_index=index)
                    yield AgentEvent(type="text_delta", content_index=index, delta=full)
                if index in started_text and index not in ended_text:
                    ended_text.add(index)
                    yield AgentEvent(type="text_end", content_index=index)

        async with self._client_factory() as client:
            headers = self._headers()
            await self._prepare_response(payload, client, headers)
            async with aclosing(self._stream_lines(client, payload, headers)) as lines:
                yield AgentEvent(type="start")
                event_name = ""
                async for line in lines:
                    if not line.strip():
                        event_name = ""
                    elif line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        blob = line[5:].strip()
                        if not blob or blob == "[DONE]":
                            continue
                        try:
                            event = json.loads(blob)
                        except json.JSONDecodeError:
                            if event_name == "error":
                                raise RuntimeError(f"{self.name} Responses stream error")
                            continue
                        kind = event.get("type") or event_name
                        index = int(
                            event.get(
                                "output_index",
                                item_indices.get(
                                    event.get("item_id"), event.get("content_index", 0)
                                ),
                            )
                        )
                        if kind in {"error", "response.error", "response.failed"}:
                            error = (
                                event.get("error")
                                or (event.get("response") or {}).get("error")
                                or {}
                            )
                            detail = (
                                error.get("message", "Response failed")
                                if isinstance(error, dict)
                                else str(error)
                            )
                            raise RuntimeError(f"{self.name} Responses: {detail}")
                        if kind in {"response.output_item.added", "response.output_item.done"}:
                            for mapped in emit_item(
                                index, event.get("item") or {}, kind.endswith(".done")
                            ):
                                yield mapped
                        elif kind in {"response.output_text.delta", "response.refusal.delta"}:
                            if index not in started_text:
                                started_text.add(index)
                                yield AgentEvent(type="text_start", content_index=index)
                            delta = event.get("delta") or ""
                            text[index] = text.get(index, "") + delta
                            yield AgentEvent(type="text_delta", content_index=index, delta=delta)
                        elif kind in {"response.output_text.done", "response.refusal.done"}:
                            if index in started_text and index not in ended_text:
                                ended_text.add(index)
                                yield AgentEvent(type="text_end", content_index=index)
                        elif kind in {
                            "response.reasoning_summary_text.delta",
                            "response.reasoning_text.delta",
                        }:
                            if index not in thinking:
                                thinking.add(index)
                                yield AgentEvent(type="thinking_start", content_index=index)
                            yield AgentEvent(
                                type="thinking_delta",
                                content_index=index,
                                delta=event.get("delta") or "",
                            )
                        elif kind == "response.function_call_arguments.delta":
                            delta = event.get("delta") or ""
                            arguments[index] = arguments.get(index, "") + delta
                            yield AgentEvent(
                                type="toolcall_delta", content_index=index, delta=delta
                            )
                        elif kind == "response.function_call_arguments.done" and index in output:
                            item = {
                                **output[index],
                                "arguments": event.get("arguments") or arguments.get(index, ""),
                            }
                            for mapped in emit_item(index, item, False):
                                yield mapped
                        elif kind in {"response.completed", "response.done", "response.incomplete"}:
                            final = event.get("response") or {}
                            final_items = dict(enumerate(final.get("output") or [])) or dict(output)
                            for idx, item in final_items.items():
                                for mapped in emit_item(idx, item, True):
                                    yield mapped
                            for idx, value in text.items():
                                if idx not in output:
                                    output[idx] = {
                                        "type": "message",
                                        "role": "assistant",
                                        "content": [{"type": "output_text", "text": value}],
                                    }
                            for idx in sorted(started_text - ended_text):
                                yield AgentEvent(type="text_end", content_index=idx)
                            for idx in sorted(thinking):
                                yield AgentEvent(type="thinking_end", content_index=idx)
                            message = self._responses_message(
                                final, model, [output[k] for k in sorted(output)]
                            )
                            yield AgentEvent(
                                type="done", stop_reason=message.stop_reason, message=message
                            )
                            return
                raise RuntimeError(f"{self.name} Responses stream ended before response completion")


def _map_openai_finish_reason(reason: str | None) -> str:
    """Normalize OpenAI finish reasons to Sentinel stop_reason values."""
    mapping = {
        "stop": "stop",
        "tool_calls": "tool_use",
        "length": "length",
    }
    return mapping.get(reason or "", "stop")
