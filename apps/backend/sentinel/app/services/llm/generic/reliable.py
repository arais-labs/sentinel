"""Provider failover wrapper with retry/backoff behavior."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.errors import error_tag, is_retryable
from app.services.llm.generic.types import AgentEvent, AgentMessage, AssistantMessage, ReasoningConfig, ToolSchema


class ReliableProvider(LLMProvider):
    """Retry/failover wrapper that tries providers in order with backoff."""

    def __init__(
        self,
        providers: Sequence[LLMProvider],
        *,
        max_retries: int = 3,
        base_backoff_ms: int = 500,
        sleep_func: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        if not providers:
            raise ValueError("ReliableProvider requires at least one provider")
        self._providers = list(providers)
        self._max_retries = max_retries
        self._base_backoff_ms = base_backoff_ms
        self._sleep = sleep_func

    @property
    def name(self) -> str:
        return "reliable"

    async def chat(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AssistantMessage:
        """Run non-streaming inference with retry/fallback across providers."""
        diagnostics: list[str] = []
        for provider in self._providers:
            for attempt in range(1, self._max_retries + 1):
                try:
                    return await provider.chat(
                        messages,
                        model=model,
                        tools=tools,
                        temperature=temperature,
                        reasoning_config=reasoning_config,
                        tool_choice=tool_choice,
                    )
                except Exception as exc:  # noqa: BLE001
                    retryable = is_retryable(exc)
                    diagnostics.append(
                        f"provider={provider.name} model={model} attempt {attempt}/{self._max_retries}: {error_tag(exc)}"
                    )
                    if retryable and attempt < self._max_retries:
                        await self._sleep((self._base_backoff_ms * (2 ** (attempt - 1))) / 1000)
                        continue
                    break
        raise RuntimeError("All providers failed. " + " | ".join(diagnostics))

    async def stream(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run streaming inference with retry/fallback across providers."""
        diagnostics: list[str] = []
        for provider in self._providers:
            for attempt in range(1, self._max_retries + 1):
                try:
                    async for event in provider.stream(
                        messages,
                        model=model,
                        tools=tools,
                        temperature=temperature,
                        reasoning_config=reasoning_config,
                        tool_choice=tool_choice,
                    ):
                        yield event
                    return
                except Exception as exc:  # noqa: BLE001
                    retryable = is_retryable(exc)
                    diagnostics.append(
                        f"provider={provider.name} model={model} attempt {attempt}/{self._max_retries}: {error_tag(exc)}"
                    )
                    if retryable and attempt < self._max_retries:
                        await self._sleep((self._base_backoff_ms * (2 ** (attempt - 1))) / 1000)
                        continue
                    break
        raise RuntimeError("All providers failed. " + " | ".join(diagnostics))
