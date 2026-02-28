"""Model-hint router that dispatches to concrete providers/models."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import AgentEvent, AgentMessage, AssistantMessage, ReasoningConfig, ToolSchema


class RouterProvider(LLMProvider):
    """Resolve `hint:*` model ids into concrete provider/model pairs."""

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
        tool_choice: str | None = None,
    ) -> AssistantMessage:
        """Resolve model hint and dispatch a single non-streaming provider call."""
        provider, resolved_model = self._resolve(model)
        return await provider.chat(
            messages,
            model=resolved_model,
            tools=tools,
            temperature=temperature,
            reasoning_config=reasoning_config,
            tool_choice=tool_choice,
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
        """Resolve model hint and dispatch a single streaming provider call."""
        provider, resolved_model = self._resolve(model)
        async for event in provider.stream(
            messages,
            model=resolved_model,
            tools=tools,
            temperature=temperature,
            reasoning_config=reasoning_config,
            tool_choice=tool_choice,
        ):
            yield event

    def _resolve(self, model: str) -> tuple[LLMProvider, str]:
        """Resolve explicit model strings or hinted aliases to routing targets."""
        if model.startswith("hint:"):
            hint = model.split(":", 1)[1]
            provider_model = self._routes.get(hint)
            if provider_model:
                return provider_model
            return self._default

        provider, _default_model = self._default
        return provider, model
