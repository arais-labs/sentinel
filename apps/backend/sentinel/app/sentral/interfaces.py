"""Standalone agent runtime protocols.

These contracts are the extraction seam between Sentinel and a reusable agent
runtime package.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from app.sentral.types import (
    AgentEvent,
    AssistantTurn,
    CheckpointSink,
    CompactionConfig,
    CompactionResult,
    ConversationItem,
    EventSink,
    GenerationConfig,
    ProviderStream,
    RunTurnRequest,
    ToolDefinition,
    ToolSchema,
    TurnResult,
)


class ConversationStore(Protocol):
    async def load_history(self, conversation_id: str) -> list[ConversationItem]:
        """Return the full conversation history for one conversation."""

    async def append_items(
        self,
        conversation_id: str,
        items: list[ConversationItem],
    ) -> None:
        """Append new items to a stored conversation."""

    async def replace_history(
        self,
        conversation_id: str,
        items: list[ConversationItem],
    ) -> None:
        """Replace the full stored conversation history."""


class Provider(Protocol):
    @property
    def name(self) -> str:
        """Stable provider identifier for logs and metadata."""

    async def chat(
        self,
        *,
        messages: list[ConversationItem],
        tools: list[ToolSchema],
        config: GenerationConfig,
    ) -> AssistantTurn:
        """Return one fully assembled assistant turn."""

    async def stream(
        self,
        *,
        messages: list[ConversationItem],
        tools: list[ToolSchema],
        config: GenerationConfig,
    ) -> ProviderStream:
        """Yield incremental provider events for one assistant turn."""


class ToolRegistry(Protocol):
    def list_tools(self) -> list[ToolDefinition]:
        """Return all registered tools."""

    def get_tool(self, name: str) -> ToolDefinition | None:
        """Return one tool by name when available."""


class Compactor(Protocol):
    async def compact(
        self,
        *,
        history: list[ConversationItem],
        config: CompactionConfig,
    ) -> CompactionResult:
        """Produce a compacted history and usage summary."""


class Runtime(Protocol):
    async def run_turn(
        self,
        request: RunTurnRequest,
        *,
        sink: EventSink | None = None,
        checkpoint: CheckpointSink | None = None,
    ) -> TurnResult:
        """Execute one agent turn against the supplied history/input."""

    async def stream_turn(
        self,
        request: RunTurnRequest,
        *,
        checkpoint: CheckpointSink | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Stream the event surface for one agent turn."""

    async def compact(
        self,
        *,
        history: list[ConversationItem],
        config: CompactionConfig,
    ) -> CompactionResult:
        """Expose explicit compaction as a runtime capability."""
