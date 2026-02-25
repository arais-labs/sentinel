from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from app.services.llm.base import LLMProvider
from app.services.llm.types import AgentEvent, AgentMessage, AssistantMessage, ReasoningConfig, ToolSchema


class RouterProvider(LLMProvider):
    def __init__(
        self,
        routes: dict[str, tuple[LLMProvider, str]],
        *,
        default: tuple[LLMProvider, str],
    ) -> None:
        self._routes = routes
        self._default = default

    @property
    def name(self) -> str:
        return "router"

    async def chat(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
    ) -> AssistantMessage:
        provider, resolved_model = self._resolve(model)
        return await provider.chat(messages, model=resolved_model, tools=tools, temperature=temperature, reasoning_config=reasoning_config)

    async def stream(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
    ) -> AsyncIterator[AgentEvent]:
        provider, resolved_model = self._resolve(model)
        async for event in provider.stream(messages, model=resolved_model, tools=tools, temperature=temperature, reasoning_config=reasoning_config):
            yield event

    def _resolve(self, model: str) -> tuple[LLMProvider, str]:
        if model.startswith("hint:"):
            hint = model.split(":", 1)[1]
            provider_model = self._routes.get(hint)
            if provider_model:
                return provider_model
            return self._default

        provider, _default_model = self._default
        return provider, model
