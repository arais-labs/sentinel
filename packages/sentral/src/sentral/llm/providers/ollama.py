"""Ollama's native HTTP protocol. Server installation/lifecycle is user-owned."""

from __future__ import annotations

import json
from copy import deepcopy
from urllib.parse import urlsplit
from uuid import uuid4

from sentral.llm.generic.base import LLMProvider
from sentral.llm.generic.types import (
    AgentEvent,
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    TokenUsage,
)
from sentral.llm.http_pool import provider_http_client
from sentral.llm.ids import ProviderId
from sentral.llm.providers.openai import OpenAIProvider


def normalize_endpoint(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Use an HTTP(S) Ollama server URL without credentials, query, or fragment."
        )
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Invalid Ollama server port.") from exc
    return value


class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str, api_key: str | None = None, *, client_factory=None):
        self.base_url = normalize_endpoint(base_url)
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client_factory = client_factory or provider_http_client

    @property
    def name(self):
        return ProviderId.OLLAMA

    @property
    def provider_id(self):
        return ProviderId.OLLAMA

    def model_context(self, model):
        return {
            "context_window_tokens": 32768,
            "context_token_budget": 24576,
            "output_reserve_tokens": 8192,
        }

    async def discover_models(self):
        async with self._client_factory() as client:
            response = await client.get(
                f"{self.base_url}/api/tags", headers=self._headers, timeout=10
            )
            response.raise_for_status()
            return response.json().get("models", [])

    async def model_info(self, model):
        async with self._client_factory() as client:
            response = await client.post(
                f"{self.base_url}/api/show",
                json={"model": model},
                headers=self._headers,
                timeout=10,
            )
            response.raise_for_status()
            return response.json()

    async def pull_model(self, model):
        async with self._client_factory() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/pull",
                headers=self._headers,
                json={"model": model, "stream": True},
                timeout=60,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.strip():
                        yield json.loads(line)

    async def delete_model(self, model):
        async with self._client_factory() as client:
            response = await client.request(
                "DELETE",
                f"{self.base_url}/api/delete",
                headers=self._headers,
                json={"model": model},
                timeout=30,
            )
            response.raise_for_status()

    def _payload(self, messages, model, tools, temperature, reasoning_config, tool_choice, stream):
        # Reuse the shared normalized message conversion, copying dict inputs before adapting.
        converted = deepcopy(OpenAIProvider._to_openai_messages(self, messages))
        names = {}
        for original in messages:
            for block in (
                getattr(original, "content", [])
                if not isinstance(getattr(original, "content", ""), str)
                else []
            ):
                if isinstance(block, ToolCallContent):
                    names[block.id] = block.name
        for message in converted:
            if isinstance(message.get("content"), list):
                blocks = message["content"]
                message["content"] = "\n".join(
                    b.get("text", "") for b in blocks if b.get("type") == "text"
                )
                images = [
                    b["image_url"]["url"].split(",", 1)[1]
                    for b in blocks
                    if b.get("type") == "image_url" and b["image_url"]["url"].startswith("data:")
                ]
                if images:
                    message["images"] = images
            if message.get("role") == "tool":
                message["tool_name"] = names.get(message.pop("tool_call_id", ""), "")
            for call in message.get("tool_calls", []):
                arguments = call["function"].get("arguments", {})
                if isinstance(arguments, str):
                    call["function"]["arguments"] = json.loads(arguments)
        payload = {
            "model": model,
            "messages": converted,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "num_ctx": 32768,
                "num_predict": (reasoning_config.max_tokens if reasoning_config else 8192),
            },
        }
        if tools and tool_choice != "none":
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        # Boolean false is supported by thinking models and harmless for non-thinking ones.
        payload["think"] = False
        return payload

    @staticmethod
    def _normalize_tool_call(name, arguments, tools):
        """Map a flattened "<tool>.<action>" call back onto the grouped tool it came from."""
        if not tools:
            return name, arguments
        by_name = {t.name: t for t in tools}
        if name in by_name:
            return name, arguments
        for separator in (".", ":", "/", "_"):
            head, found, tail = name.partition(separator)
            if not found or not tail:
                continue
            tool = by_name.get(head) or by_name.get(f"{head}_tool")
            if tool is None:
                continue
            actions = tool.parameters.get("properties", {}).get("action", {}).get("enum") or []
            if separator == "_" and tail not in actions:
                continue
            merged = dict(arguments)
            merged.setdefault("action", tail)
            return tool.name, merged
        return name, arguments

    def _message(self, data, model, tools=None):
        message = data.get("message", {})
        blocks = []
        if message.get("thinking"):
            blocks.append(ThinkingContent(thinking=message["thinking"]))
        if message.get("content"):
            blocks.append(TextContent(text=message["content"]))
        for call in message.get("tool_calls", []):
            function = call["function"]
            args = function.get("arguments", {})
            name, arguments = self._normalize_tool_call(
                function["name"],
                json.loads(args) if isinstance(args, str) else args,
                tools,
            )
            blocks.append(
                ToolCallContent(id=call.get("id") or str(uuid4()), name=name, arguments=arguments)
            )
        return AssistantMessage(
            content=blocks,
            model=data.get("model") or model,
            provider=self.name,
            usage=TokenUsage(
                input_tokens=data.get("prompt_eval_count", 0),
                output_tokens=data.get("eval_count", 0),
            ),
            stop_reason=(
                "tool_use"
                if any(isinstance(b, ToolCallContent) for b in blocks)
                else "length" if data.get("done_reason") == "length" else "stop"
            ),
        )

    async def chat(
        self,
        messages,
        model,
        tools=None,
        temperature=0.7,
        reasoning_config=None,
        tool_choice=None,
    ):
        payload = self._payload(
            messages, model, tools, temperature, reasoning_config, tool_choice, False
        )
        async with self._client_factory() as client:
            response = await client.post(
                f"{self.base_url}/api/chat", json=payload, headers=self._headers
            )
            response.raise_for_status()
            data = response.json()
            if data.get("error"):
                raise ValueError("Ollama rejected generation. Check the selected model and server.")
            return self._message(data, model, tools)

    async def stream(
        self,
        messages,
        model,
        tools=None,
        temperature=0.7,
        reasoning_config=None,
        tool_choice=None,
    ):
        payload = self._payload(
            messages, model, tools, temperature, reasoning_config, tool_choice, True
        )
        assembled = {"content": "", "thinking": "", "tool_calls": []}
        yield AgentEvent(type="start")
        async with self._client_factory() as client:
            async with client.stream(
                "POST", f"{self.base_url}/api/chat", json=payload, headers=self._headers
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise ValueError(
                            "Ollama interrupted generation. Check the selected model and server."
                        )
                    delta = data.get("message", {})
                    for key, event in (
                        ("thinking", "thinking_delta"),
                        ("content", "text_delta"),
                    ):
                        if delta.get(key):
                            assembled[key] += delta[key]
                            yield AgentEvent(
                                type=event,
                                delta=delta[key],
                                content_index=0 if key == "content" else 1,
                            )
                    assembled["tool_calls"].extend(delta.get("tool_calls", []))
                    if data.get("done"):
                        result = self._message({**data, "message": assembled}, model, tools)
                        for index, block in enumerate(result.content, start=2):
                            if isinstance(block, ToolCallContent):
                                yield AgentEvent(
                                    type="toolcall_start",
                                    tool_call=block,
                                    content_index=index,
                                )
                                yield AgentEvent(
                                    type="toolcall_end",
                                    tool_call=block,
                                    content_index=index,
                                )
                        yield AgentEvent(
                            type="done", message=result, stop_reason=result.stop_reason
                        )
                        return
        raise ValueError("Ollama stream ended before completion.")
