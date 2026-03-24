"""Standalone agent runtime types.

These types intentionally avoid Sentinel ORM/session concepts so the runtime
can be extracted into a reusable package later.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TypeAlias


ConversationRole = Literal["system", "user", "assistant", "tool"]
StopReason = Literal[
    "stop",
    "length",
    "tool_use",
    "pending_approval",
    "error",
    "aborted",
    "timeout",
]
TurnStatus = Literal[
    "completed",
    "pending_approval",
    "compaction_required",
    "error",
    "aborted",
    "timeout",
]
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
    "tool_result",
    "approval_required",
    "agent_progress",
    "compaction_required",
    "done",
    "error",
]
ToolExecutionStatus = Literal["ok", "error", "pending_approval"]


@dataclass(slots=True)
class TextBlock:
    type: str = "text"
    text: str = ""


@dataclass(slots=True)
class ImageBlock:
    type: str = "image"
    media_type: str = "image/png"
    data: str = ""


@dataclass(slots=True)
class ThinkingBlock:
    type: str = "thinking"
    thinking: str = ""
    signature: str | None = None


@dataclass(slots=True)
class ToolCallBlock:
    type: str = "tool_call"
    id: str = ""
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    thought_signature: str | None = None


@dataclass(slots=True)
class ToolResultBlock:
    type: str = "tool_result"
    tool_call_id: str = ""
    tool_name: str = ""
    content: str = ""
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    tool_arguments: dict[str, Any] | None = None


ContentBlock: TypeAlias = (
    TextBlock | ImageBlock | ThinkingBlock | ToolCallBlock | ToolResultBlock
)


@dataclass(slots=True)
class ConversationItem:
    id: str
    role: ConversationRole
    content: list[ContentBlock] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class GenerationConfig:
    model: str
    temperature: float = 0.7
    max_iterations: int = 50
    stream: bool = True
    system_prompt: str | None = None
    max_output_tokens: int | None = None
    tool_choice: str | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolSchema:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(slots=True)
class ApprovalRequest:
    id: str
    tool_name: str
    action: str
    description: str
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolExecutionResult:
    status: ToolExecutionStatus
    content: Any = None
    error: str | None = None
    approval_request: ApprovalRequest | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters_schema: dict[str, Any]
    execute: Callable[[dict[str, Any]], Awaitable[ToolExecutionResult]]
    enabled: bool = True


@dataclass(slots=True)
class AgentEvent:
    type: AgentEventType
    item: ConversationItem | None = None
    delta: str | None = None
    tool_call: ToolCallBlock | None = None
    tool_result: ToolResultBlock | None = None
    approval_request: ApprovalRequest | None = None
    stop_reason: StopReason | None = None
    error: str | None = None
    iteration: int | None = None
    max_iterations: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AssistantTurn:
    item: ConversationItem
    stop_reason: StopReason
    usage: TokenUsage = field(default_factory=TokenUsage)


ProviderEvent: TypeAlias = AgentEvent
EventSink: TypeAlias = Callable[[AgentEvent], Awaitable[None]]
ProviderStream: TypeAlias = AsyncIterator[ProviderEvent]


@dataclass(slots=True)
class RunTurnRequest:
    conversation_id: str | None = None
    new_items: list[ConversationItem] = field(default_factory=list)
    history: list[ConversationItem] | None = None
    config: GenerationConfig | None = None


@dataclass(slots=True)
class TurnResult:
    status: TurnStatus
    history: list[ConversationItem]
    usage: TokenUsage = field(default_factory=TokenUsage)
    iterations: int = 0
    final_item: ConversationItem | None = None
    stop_reason: StopReason | None = None
    pending_approval: ApprovalRequest | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CompactionConfig:
    target_token_count: int
    model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CompactionResult:
    history: list[ConversationItem]
    raw_token_count: int
    compacted_token_count: int
    summary_preview: str
