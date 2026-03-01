from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.models import Message
from app.services.llm.generic.types import (
    AgentMessage,
    AssistantMessage,
    ImageContent,
    SystemMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)


@dataclass(frozen=True, slots=True)
class ContextUsageMetrics:
    context_token_budget: int
    estimated_context_tokens: int
    estimated_context_percent: int


def normalize_context_budget(value: int | None = None) -> int:
    raw = int(value) if isinstance(value, int) else int(settings.context_token_budget)
    return max(1, raw)


def estimate_text_tokens(text: str) -> int:
    cleaned = (text or "").strip()
    if not cleaned:
        return 0
    return max(1, len(cleaned) // 4)


def estimate_db_message_tokens(message: Message) -> int:
    if isinstance(message.token_count, int) and message.token_count > 0:
        return int(message.token_count)
    return estimate_text_tokens(message.content or "")


def estimate_db_messages_tokens(messages: list[Message]) -> int:
    return sum(estimate_db_message_tokens(message) for message in messages)


def estimate_agent_message_tokens(message: AgentMessage) -> int:
    if isinstance(message, (SystemMessage, ToolResultMessage)):
        return estimate_text_tokens(message.content or "")
    if isinstance(message, UserMessage):
        if isinstance(message.content, str):
            return estimate_text_tokens(message.content)
        total = 0
        for block in message.content:
            if isinstance(block, TextContent):
                total += estimate_text_tokens(block.text)
            elif isinstance(block, ImageContent):
                total += estimate_text_tokens(block.data)
        return total
    if isinstance(message, AssistantMessage):
        total = 0
        for block in message.content:
            if isinstance(block, TextContent):
                total += estimate_text_tokens(block.text)
            elif isinstance(block, ThinkingContent):
                total += estimate_text_tokens(block.thinking)
            elif isinstance(block, ToolCallContent):
                total += estimate_text_tokens(block.name)
                total += estimate_text_tokens(str(block.arguments or {}))
        return total
    return 0


def estimate_agent_messages_tokens(messages: list[AgentMessage]) -> int:
    return sum(estimate_agent_message_tokens(message) for message in messages)


def build_context_usage_metrics(
    *,
    estimated_tokens: int,
    context_budget: int | None = None,
) -> ContextUsageMetrics:
    budget = normalize_context_budget(context_budget)
    safe_tokens = max(0, int(estimated_tokens))
    percent = int(min(100, round((safe_tokens / budget) * 100)))
    return ContextUsageMetrics(
        context_token_budget=budget,
        estimated_context_tokens=safe_tokens,
        estimated_context_percent=percent,
    )


def extract_runtime_context_metrics(
    run_context: dict[str, Any] | None,
    *,
    default_budget: int | None = None,
) -> ContextUsageMetrics | None:
    if not isinstance(run_context, dict):
        return None
    raw_tokens = run_context.get("estimated_context_tokens")
    if not isinstance(raw_tokens, int) or raw_tokens < 0:
        return None
    raw_budget = run_context.get("context_token_budget")
    budget = (
        int(raw_budget)
        if isinstance(raw_budget, int) and raw_budget > 0
        else normalize_context_budget(default_budget)
    )
    raw_percent = run_context.get("estimated_context_percent")
    if isinstance(raw_percent, int):
        percent = max(0, min(100, int(raw_percent)))
        return ContextUsageMetrics(
            context_token_budget=budget,
            estimated_context_tokens=raw_tokens,
            estimated_context_percent=percent,
        )
    return build_context_usage_metrics(
        estimated_tokens=raw_tokens,
        context_budget=budget,
    )

