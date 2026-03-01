"""Base provider contract for chat/stream interactions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence

from app.services.llm.ids import ProviderId
from app.services.llm.generic.types import AgentEvent, AgentMessage, AssistantMessage, ProviderCapabilities, ReasoningConfig, ToolSchema


class LLMProvider(ABC):
    """Abstract provider interface for chat and streaming completion APIs."""

    @abstractmethod
    async def chat(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AssistantMessage:
        """Return one fully assembled assistant response."""
        raise NotImplementedError

    @abstractmethod
    async def stream(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Yield incremental agent events for one assistant response."""
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable provider identifier used in logs and routing."""
        raise NotImplementedError

    @property
    def provider_id(self) -> ProviderId | None:
        """Typed external provider id when this adapter maps to one provider."""
        return None

    def capabilities(self) -> ProviderCapabilities:
        """Provider capability flags used for runtime behavior decisions."""
        return ProviderCapabilities()
