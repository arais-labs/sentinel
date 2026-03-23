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


class _AgentLoopProtocol(Protocol):
    provider: Any

    async def run(
        self,
        db: Any,
        session_id: UUID,
        user_message: str | list[Any],
        *,
        system_prompt: str | None = None,
        max_iterations: int = 50,
        temperature: float = 0.7,
        model: str,
        persist_user_message: bool = True,
        stream: bool = True,
        timeout_seconds: float | None = None,
        on_event: Any = None,
        inject_queue: asyncio.Queue[str] | None = None,
        persist_incremental: bool = False,
        user_metadata: dict[str, Any] | None = None,
    ) -> Any: ...


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
    "_AgentLoopProtocol",
    "_PersistedInboundMessage",
    "_RouteContext",
    "_RunRegistryProtocol",
    "_ToolDeliveryState",
    "_WSManagerProtocol",
]
