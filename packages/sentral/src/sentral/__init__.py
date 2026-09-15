"""Standalone agent runtime contracts.

This package is the first extraction seam for pulling Sentinel's agent runtime
into a reusable package without changing current Sentinel behavior.
"""

from sentral.interfaces import (
    Compactor,
    ConversationStore,
    Provider,
    Machine,
    ToolRegistry,
)
from sentral.engine import AgentRuntimeEngine
from sentral.memory_store import InMemoryConversationStore
from sentral.types import (
    AgentEvent,
    ApprovalRequest,
    AssistantTurn,
    CompactionConfig,
    CompactionResult,
    ContentBlock,
    ConversationItem,
    ConversationRole,
    EventSink,
    GenerationConfig,
    ImageBlock,
    ProviderEvent,
    RunTurnRequest,
    StopReason,
    TextBlock,
    ThinkingBlock,
    TokenUsage,
    ToolCallBlock,
    ToolCallInterceptionResult,
    ToolDefinition,
    ToolExecutionResult,
    ToolResultBlock,
    ToolSchema,
    TurnResult,
    TurnStatus,
)

__all__ = [
    "AgentEvent",
    "ApprovalRequest",
    "AgentRuntimeEngine",
    "AssistantTurn",
    "CompactionConfig",
    "CompactionResult",
    "Compactor",
    "ContentBlock",
    "ConversationItem",
    "ConversationRole",
    "ConversationStore",
    "EventSink",
    "GenerationConfig",
    "ImageBlock",
    "InMemoryConversationStore",
    "Provider",
    "ProviderEvent",
    "RunTurnRequest",
    "Machine",
    "StopReason",
    "TextBlock",
    "ThinkingBlock",
    "TokenUsage",
    "ToolCallBlock",
    "ToolCallInterceptionResult",
    "ToolDefinition",
    "ToolExecutionResult",
    "ToolRegistry",
    "ToolResultBlock",
    "ToolSchema",
    "TurnResult",
    "TurnStatus",
]
