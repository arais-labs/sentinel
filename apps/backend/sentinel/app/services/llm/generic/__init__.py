"""Generic LLM runtime layer exports with lazy loading."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.llm.generic.base import LLMProvider
    from app.services.llm.generic.reliable import ReliableProvider
    from app.services.llm.generic.router import RouterProvider
    from app.services.llm.generic.tier import TierConfig, TierModelConfig, TierProvider
    from app.services.llm.generic.types import (
        AgentEvent,
        AgentMessage,
        AssistantContent,
        AssistantMessage,
        ImageContent,
        ModelCompat,
        ProviderCapabilities,
        ReasoningConfig,
        StopReason,
        SystemMessage,
        TextContent,
        ThinkingContent,
        TokenUsage,
        ToolCallContent,
        ToolResultMessage,
        ToolSchema,
        UserContentBlock,
        UserMessage,
    )

__all__ = [
    "LLMProvider",
    "scrub",
    "error_tag",
    "is_retryable",
    "status_code",
    "ReliableProvider",
    "RouterProvider",
    "TierProvider",
    "TierConfig",
    "TierModelConfig",
    "ReasoningConfig",
    "ModelCompat",
    "StopReason",
    "AssistantContent",
    "AgentMessage",
    "TextContent",
    "ImageContent",
    "UserContentBlock",
    "ThinkingContent",
    "ToolCallContent",
    "SystemMessage",
    "UserMessage",
    "AssistantMessage",
    "ToolResultMessage",
    "TokenUsage",
    "AgentEvent",
    "ToolSchema",
    "ProviderCapabilities",
]


def __getattr__(name: str) -> Any:
    if name == "LLMProvider":
        from app.services.llm.generic.base import LLMProvider

        return LLMProvider
    if name == "scrub":
        from app.services.llm.generic.credential_scrubber import scrub

        return scrub
    if name in {"error_tag", "is_retryable", "status_code"}:
        from app.services.llm.generic import errors as _errors

        return getattr(_errors, name)
    if name == "ReliableProvider":
        from app.services.llm.generic.reliable import ReliableProvider

        return ReliableProvider
    if name == "RouterProvider":
        from app.services.llm.generic.router import RouterProvider

        return RouterProvider
    if name in {"TierProvider", "TierConfig", "TierModelConfig"}:
        from app.services.llm.generic.tier import TierConfig, TierModelConfig, TierProvider

        return {
            "TierProvider": TierProvider,
            "TierConfig": TierConfig,
            "TierModelConfig": TierModelConfig,
        }[name]
    if name in {
        "ReasoningConfig",
        "ModelCompat",
        "StopReason",
        "AssistantContent",
        "AgentMessage",
        "TextContent",
        "ImageContent",
        "UserContentBlock",
        "ThinkingContent",
        "ToolCallContent",
        "SystemMessage",
        "UserMessage",
        "AssistantMessage",
        "ToolResultMessage",
        "TokenUsage",
        "AgentEvent",
        "ToolSchema",
        "ProviderCapabilities",
    }:
        from app.services.llm.generic import types as _types

        return getattr(_types, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
