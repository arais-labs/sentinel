"""Adapters from Sentinel providers to standalone runtime provider contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sentral import (
    AssistantTurn,
    ConversationItem,
    GenerationConfig,
    Provider,
    ToolSchema as RuntimeToolSchema,
)
from sentral.llm.runtime_conversions import (
    runtime_items_to_sentinel_messages,
    runtime_tool_schema_to_sentinel,
    sentinel_assistant_turn_to_runtime,
    sentinel_event_to_runtime_event,
)
from sentral.llm.generic.base import LLMProvider
from sentral.llm.generic.types import ReasoningConfig, ToolResultMessage
from sentral.llm.tool_images import (
    ToolImageReinjectionPolicy,
    build_tool_image_reinjection_messages,
)


class SentinelProviderAdapter(Provider):
    """Wrap Sentinel's current provider interface with runtime-neutral contracts."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        image_policy: ToolImageReinjectionPolicy | None = None,
    ) -> None:
        self._provider = provider
        self._image_policy = image_policy or ToolImageReinjectionPolicy()

    @property
    def name(self) -> str:
        return self._provider.name

    def _messages(self, messages):
        converted = runtime_items_to_sentinel_messages(messages)
        # Current tool observations must be image inputs, not base64 text.
        # Add them after all results in the batch to keep tool-call pairing intact.
        latest = []
        for message in reversed(converted):
            if not latest and getattr(message, "metadata", {}).get("steering_id"):
                continue
            if not isinstance(message, ToolResultMessage):
                break
            latest.append(message)
        images = build_tool_image_reinjection_messages(
            list(reversed(latest)),
            policy=self._image_policy,
        )
        return converted + images.messages

    @staticmethod
    def _reasoning(config: GenerationConfig) -> ReasoningConfig | None:
        values = {
            key: config.provider_metadata[key]
            for key in ("reasoning_effort", "thinking_budget")
            if key in config.provider_metadata
        }
        if config.max_output_tokens is not None:
            values["max_tokens"] = config.max_output_tokens
        return ReasoningConfig(**values, override_fields=frozenset(values)) if values else None

    async def chat(
        self,
        *,
        messages: list[ConversationItem],
        tools: list[RuntimeToolSchema],
        config: GenerationConfig,
    ) -> AssistantTurn:
        response = await self._provider.chat(
            self._messages(messages),
            model=config.model,
            tools=[runtime_tool_schema_to_sentinel(tool) for tool in tools],
            temperature=config.temperature,
            tool_choice=config.tool_choice,
            reasoning_config=self._reasoning(config),
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
            self._messages(messages),
            model=config.model,
            tools=[runtime_tool_schema_to_sentinel(tool) for tool in tools],
            temperature=config.temperature,
            tool_choice=config.tool_choice,
            reasoning_config=self._reasoning(config),
        ):
            yield sentinel_event_to_runtime_event(event)
