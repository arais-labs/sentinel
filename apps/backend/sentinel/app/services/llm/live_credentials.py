"""Reload an OAuth-backed provider when its external CLI credential changes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any, TypeVar

from sentral.llm.generic.base import LLMProvider
from sentral.llm.generic.types import (
    AgentEvent,
    AgentMessage,
    AssistantMessage,
    ProviderCapabilities,
    ReasoningConfig,
    ToolSchema,
)

Credential = TypeVar("Credential")


def _fingerprint(value: Any) -> str:
    serializer = getattr(value, "as_json", None)
    if callable(serializer):
        return serializer()
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class LiveCredentialProvider(LLMProvider):
    """Delegate to a provider built from the latest external credential."""

    def __init__(
        self,
        initial: Credential,
        *,
        load: Callable[[], Awaitable[Credential | None]],
        build: Callable[[Credential], LLMProvider],
        unavailable_message: str,
    ) -> None:
        self._load = load
        self._build = build
        self._unavailable_message = unavailable_message
        self._credential_fingerprint = _fingerprint(initial)
        self._provider = build(initial)
        self._lock = asyncio.Lock()

    async def _current(self) -> LLMProvider:
        async with self._lock:
            credential = await self._load()
            if credential is None:
                raise RuntimeError(self._unavailable_message)
            fingerprint = _fingerprint(credential)
            if fingerprint == self._credential_fingerprint:
                return self._provider
            previous = self._provider
            self._provider = self._build(credential)
            self._credential_fingerprint = fingerprint
            close = getattr(previous, "aclose", None)
            if callable(close):
                await close()
            return self._provider

    @property
    def name(self) -> str:
        return self._provider.name

    @property
    def provider_id(self):
        return self._provider.provider_id

    def capabilities(self) -> ProviderCapabilities:
        return self._provider.capabilities()

    def model_context(self, model):
        return self._provider.model_context(model)

    def supports_fast_mode(self, model: str) -> bool:
        return self._provider.supports_fast_mode(model)

    def resolve_generation_hint(self, model: str) -> tuple[str, str] | None:
        return self._provider.resolve_generation_hint(model)

    async def count_input_tokens(self, messages, model, tools=None, reasoning_config=None):
        provider = await self._current()
        return await provider.count_input_tokens(messages, model, tools, reasoning_config)

    async def chat(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AssistantMessage:
        provider = await self._current()
        return await provider.chat(
            messages, model, tools, temperature, reasoning_config, tool_choice
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
        provider = await self._current()
        async for event in provider.stream(
            messages, model, tools, temperature, reasoning_config, tool_choice
        ):
            yield event

    async def aclose(self) -> None:
        close = getattr(self._provider, "aclose", None)
        if callable(close):
            await close()
