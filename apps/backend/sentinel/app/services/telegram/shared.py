from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

TELEGRAM_MAX_MSG_LEN = 4096
TELEGRAM_OWNER_PAIRING_TTL_SECONDS = 600
TELEGRAM_BUSY_POLL_ATTEMPTS = 12
TELEGRAM_BUSY_POLL_INTERVAL_SECONDS = 5


class _RunRegistryProtocol(Protocol):
    async def register(self, session_id: str, task: asyncio.Task[object]) -> bool: ...

    async def clear(self, session_id: str, task: asyncio.Task[object] | None = None) -> None: ...

    async def is_running(self, session_id: str) -> bool: ...


class _WSManagerProtocol(Protocol):
    async def broadcast_message_ack(
        self,
        session_id: str,
        message_id: str,
        content: str,
        created_at: datetime | None,
        metadata: dict | None = None,
    ) -> None: ...

    async def broadcast_agent_thinking(self, session_id: str) -> None: ...

    async def broadcast_agent_event(self, session_id: str, event: "AgentEvent") -> None: ...

    async def broadcast_agent_error(self, session_id: str, message: str) -> None: ...

    async def broadcast_done(self, session_id: str, stop_reason: str) -> None: ...

    async def broadcast(self, session_id: str, data: dict) -> None: ...


class _RuntimeSupportProtocol(Protocol):
    provider: Any
    context_builder: Any
    tool_adapter: Any

    async def estop_level(self, db: Any) -> Any: ...

    async def prepare_runtime_turn_context(self, db: Any, session_id: UUID, **kwargs) -> Any: ...

    async def persist_created_messages(self, db: Any, session_id: UUID, created: list[Any], assistant_iterations: dict[int, int], **kwargs) -> None: ...

    def extract_final_text(self, messages: list[Any]) -> str: ...

    def collect_attachments(self, messages: list[Any]) -> list[dict[str, Any]]: ...


@dataclass(slots=True)
class _RouteContext:
    """Resolved routing metadata for one inbound Telegram message."""

    session_id: UUID
    session_key: str
    route_scope: str
    inline_reply_mode: bool
    chat_id: int | None
    chat_type: str


@dataclass(slots=True)
class _PersistedInboundMessage:
    """Persisted Telegram user message details used in downstream delivery steps."""

    message: Any
    is_first_message: bool


@dataclass(slots=True)
class _ToolDeliveryState:
    """Tracks whether outbound Telegram delivery occurred via tool or fallback path."""

    expected_chat_id: int | None
    delivered: bool = False
    delivered_chat_id: int | None = None
    fallback_used: bool = False


__all__ = [
    "TELEGRAM_BUSY_POLL_ATTEMPTS",
    "TELEGRAM_BUSY_POLL_INTERVAL_SECONDS",
    "TELEGRAM_MAX_MSG_LEN",
    "TELEGRAM_OWNER_PAIRING_TTL_SECONDS",
    "_RuntimeSupportProtocol",
    "_PersistedInboundMessage",
    "_RouteContext",
    "_RunRegistryProtocol",
    "_ToolDeliveryState",
    "_WSManagerProtocol",
]
