"""Standalone agent runtime contracts.

This package is the first extraction seam for pulling Sentinel's agent runtime
into a reusable package without changing current Sentinel behavior.
"""

from app.sentral.interfaces import (
    Compactor,
    ConversationStore,
    Provider,
    Runtime,
    ToolRegistry,
)
from app.sentral.engine import AgentRuntimeEngine
from app.sentral.memory_store import InMemoryConversationStore
from app.sentral.types import (
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
    "Runtime",
    "StopReason",
    "TextBlock",
    "ThinkingBlock",
    "TokenUsage",
    "ToolCallBlock",
    "ToolDefinition",
    "ToolExecutionResult",
    "ToolRegistry",
    "ToolResultBlock",
    "ToolSchema",
    "TurnResult",
    "TurnStatus",
]
