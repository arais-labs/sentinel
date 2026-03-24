"""Adapters from Sentinel providers to standalone runtime provider contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.sentral import (
    AssistantTurn,
    ConversationItem,
    GenerationConfig,
    Provider,
    ToolSchema as RuntimeToolSchema,
)
from app.services.agent_runtime_adapters.conversions import (
    runtime_items_to_sentinel_messages,
    runtime_tool_schema_to_sentinel,
    sentinel_assistant_turn_to_runtime,
    sentinel_event_to_runtime_event,
)
from app.services.llm.generic.base import LLMProvider


class SentinelProviderAdapter(Provider):
    """Wrap Sentinel's current provider interface with runtime-neutral contracts."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    @property
    def name(self) -> str:
        return self._provider.name

    async def chat(
        self,
        *,
        messages: list[ConversationItem],
        tools: list[RuntimeToolSchema],
        config: GenerationConfig,
    ) -> AssistantTurn:
        response = await self._provider.chat(
            runtime_items_to_sentinel_messages(messages),
            model=config.model,
            tools=[runtime_tool_schema_to_sentinel(tool) for tool in tools],
            temperature=config.temperature,
        )
        return sentinel_assistant_turn_to_runtime(response, item_id="assistant")

    async def stream(
        self,
        *,
        messages: list[ConversationItem],
        tools: list[RuntimeToolSchema],
        config: GenerationConfig,
    ) -> AsyncIterator:
        async for event in self._provider.stream(
            runtime_items_to_sentinel_messages(messages),
            model=config.model,
            tools=[runtime_tool_schema_to_sentinel(tool) for tool in tools],
            temperature=config.temperature,
        ):
            yield sentinel_event_to_runtime_event(event)
