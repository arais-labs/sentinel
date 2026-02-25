from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TypeAlias

StopReason = Literal["stop", "length", "tool_use", "error", "aborted", "timeout"]
AgentEventType = Literal[
    "start",
    "text_start",
    "text_delta",
    "text_end",
    "thinking_start",
    "thinking_delta",
    "thinking_end",
    "toolcall_start",
    "toolcall_delta",
    "toolcall_end",
    "agent_progress",
    "done",
    "error",
]


@dataclass(slots=True)
class TextContent:
    type: str = "text"
    text: str = ""


@dataclass(slots=True)
class ThinkingContent:
    type: str = "thinking"
    thinking: str = ""
    signature: str | None = None


@dataclass(slots=True)
class ToolCallContent:
    type: str = "tool_call"
    id: str = ""
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


AssistantContent: TypeAlias = TextContent | ThinkingContent | ToolCallContent


@dataclass(slots=True)
class SystemMessage:
    role: str = "system"
    content: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(slots=True)
class UserMessage:
    role: str = "user"
    content: str | list[TextContent] = ""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class AssistantMessage:
    role: str = "assistant"
    content: list[AssistantContent] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    stop_reason: StopReason = "stop"


@dataclass(slots=True)
class ToolResultMessage:
    role: str = "tool_result"
    tool_call_id: str = ""
    tool_name: str = ""
    content: str = ""
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


AgentMessage: TypeAlias = SystemMessage | UserMessage | AssistantMessage | ToolResultMessage


@dataclass(slots=True)
class AgentEvent:
    type: AgentEventType
    content_index: int | None = None
    delta: str | None = None
    tool_call: ToolCallContent | None = None
    message: AssistantMessage | None = None
    stop_reason: StopReason | None = None
    error: str | None = None
    iteration: int | None = None
    max_iterations: int | None = None
    signature: str | None = None


@dataclass(slots=True)
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(slots=True)
class ReasoningConfig:
    """Per-call reasoning parameters that flow from tier config to API payload."""
    max_tokens: int = 8192
    thinking_budget: int | None = None    # Anthropic extended thinking budget_tokens
    reasoning_effort: str | None = None   # OpenAI: "low" | "medium" | "high"


@dataclass(slots=True)
class ModelCompat:
    """Per-model capability flags — avoids sending unsupported params."""
    supports_thinking: bool = False
    supports_reasoning_effort: bool = False
    max_tokens_field: str = "max_tokens"  # Some models use "max_completion_tokens"


@dataclass(slots=True)
class ProviderCapabilities:
    supports_tools: bool = True
    supports_streaming: bool = True
    max_context_tokens: int = 200_000
