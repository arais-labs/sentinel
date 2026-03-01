"""Public LLM service exports with lazy loading.

Avoids importing provider dependencies at package import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.llm.generic.base import LLMProvider
    from app.services.llm.generic.router import RouterProvider
    from app.services.llm.generic.reliable import ReliableProvider
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
    from app.services.llm.providers.anthropic import AnthropicProvider
    from app.services.llm.providers.codex import CodexProvider
    from app.services.llm.providers.gemini import GeminiProvider
    from app.services.llm.providers.openai import OpenAIProvider

__all__ = [
    "LLMProvider",
    "scrub",
    "AnthropicProvider",
    "CodexProvider",
    "GeminiProvider",
    "OpenAIProvider",
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
    if name == "AnthropicProvider":
        from app.services.llm.providers.anthropic import AnthropicProvider

        return AnthropicProvider
    if name == "CodexProvider":
        from app.services.llm.providers.codex import CodexProvider

        return CodexProvider
    if name == "GeminiProvider":
        from app.services.llm.providers.gemini import GeminiProvider

        return GeminiProvider
    if name == "OpenAIProvider":
        from app.services.llm.providers.openai import OpenAIProvider

        return OpenAIProvider
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
