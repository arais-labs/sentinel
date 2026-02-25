from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

import httpx

from app.services.llm.base import LLMProvider
from app.services.llm.types import AgentEvent, AgentMessage, AssistantMessage, ReasoningConfig, ToolSchema


class ReliableProvider(LLMProvider):
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
    ) -> AssistantMessage:
        diagnostics: list[str] = []
        for provider in self._providers:
            for attempt in range(1, self._max_retries + 1):
                try:
                    return await provider.chat(messages, model=model, tools=tools, temperature=temperature, reasoning_config=reasoning_config)
                except Exception as exc:  # noqa: BLE001
                    retryable = _is_retryable(exc)
                    diagnostics.append(
                        f"provider={provider.name} model={model} attempt {attempt}/{self._max_retries}: {_error_tag(exc)}"
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
    ) -> AsyncIterator[AgentEvent]:
        diagnostics: list[str] = []
        for provider in self._providers:
            for attempt in range(1, self._max_retries + 1):
                try:
                    async for event in provider.stream(messages, model=model, tools=tools, temperature=temperature, reasoning_config=reasoning_config):
                        yield event
                    return
                except Exception as exc:  # noqa: BLE001
                    retryable = _is_retryable(exc)
                    diagnostics.append(
                        f"provider={provider.name} model={model} attempt {attempt}/{self._max_retries}: {_error_tag(exc)}"
                    )
                    if retryable and attempt < self._max_retries:
                        await self._sleep((self._base_backoff_ms * (2 ** (attempt - 1))) / 1000)
                        continue
                    break
        raise RuntimeError("All providers failed. " + " | ".join(diagnostics))


def _status_code(exc: Exception) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return getattr(exc, "status_code", None)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
        return True
    status_code = _status_code(exc)
    if status_code is None:
        return False
    if status_code == 429:
        return True
    return status_code >= 500


def _error_tag(exc: Exception) -> str:
    status_code = _status_code(exc)
    if status_code == 429:
        return "rate_limited"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, (ConnectionError, httpx.ConnectError, httpx.NetworkError)):
        return "connection_error"
    if status_code is not None:
        body = ""
        if isinstance(exc, httpx.HTTPStatusError):
            # Streaming responses can raise ResponseNotRead when .text is
            # accessed before the body has been read.
            try:
                body = (exc.response.text or "")[:300]
            except Exception:  # noqa: BLE001
                body = "<streaming body unavailable>"
        return f"http_{status_code}: {body}".strip()
    return f"{exc.__class__.__name__}: {exc}"
