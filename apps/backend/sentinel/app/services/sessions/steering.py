"""Shared session steering delivery, independent of HTTP request/response handling."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message
from app.schemas.sessions import SteeringRequest
from app.services.agent_runtime_adapters.conversions import db_messages_to_runtime_items
from app.services.llm.session_selection import selection_model
from app.services.sessions.errors import (
    AgentRuntimeUnavailableError,
    SteeringConflictError,
    SteeringValidationError,
)
from app.services.ws.ws_stream_parser import parse_ws_message
from app.services.ws.ws_stream_service import persist_user_message

if TYPE_CHECKING:
    from sentral.llm.generic.base import LLMProvider
    from app.services.sessions.agent_run_registry import AgentRunRegistry
    from app.services.sessions.service import SessionService
    from app.services.ws.ws_manager import ConnectionManager


async def enqueue_session_steering(
    db: AsyncSession,
    *,
    session_id: UUID,
    user_id: str,
    payload: SteeringRequest,
    sessions: SessionService,
    provider: LLMProvider | None,
    registry: AgentRunRegistry,
    manager: ConnectionManager | None,
    ingress_metadata: dict[str, Any] | None = None,
) -> Message:
    """Persist once, enqueue once, and notify the existing idle wakeup worker.

    Provenance is supplied by internal producers, never taken from the payload.
    Keep the shared parser's attachment normalization and message validation.
    """
    session = await sessions.get_session(db, session_id=session_id, user_id=user_id)
    if provider is None or manager is None:
        raise AgentRuntimeUnavailableError("Agent runtime unavailable")
    parsed = parse_ws_message(json.dumps({**payload.model_dump(mode="json"), "type": "message"}))
    if parsed is None:
        raise SteeringValidationError("Invalid steering message or attachments")
    try:
        provider.model_context(
            selection_model(
                parsed.tier, parsed.provider_id, parsed.reasoning_level, parsed.fast_mode
            )
        )
    except (ValueError, KeyError) as exc:
        raise SteeringValidationError(str(exc)) from exc

    key = str(session_id)
    # Serialize enqueue against run completion and other submissions. A message
    # arriving after completion is picked up by the existing idle wakeup worker.
    async with registry.idle_guard(key):
        result = await db.execute(select(Message).where(Message.id == payload.message_id))
        message = result.scalars().first()
        if message is not None:
            if message.session_id != session_id or not (message.metadata_json or {}).get(
                "steering_id"
            ):
                raise SteeringConflictError("Message ID already in use")
        else:
            message = await persist_user_message(
                db,
                session_id=session_id,
                session=session,
                content=parsed.content,
                attachments=parsed.attachments,
                requested_tier=parsed.tier,
                provider_id=parsed.provider_id,
                reasoning_level=parsed.reasoning_level,
                fast_mode=parsed.fast_mode,
                temperature=0.7,
                max_iterations=parsed.max_iterations,
                agent_mode=parsed.agent_mode,
                message_id=payload.message_id,
                steering=True,
                ingress_metadata=ingress_metadata,
            )
        await manager.broadcast_message_ack(
            key,
            str(message.id),
            message.content,
            message.created_at,
            metadata=message.metadata_json or {},
        )
        if (message.metadata_json or {}).get("steering") == "pending":
            if not any(
                item.metadata.get("steering_id") == str(message.id)
                for item in registry.peek_interjections(key)
            ):
                registry.enqueue_interjection(key, db_messages_to_runtime_items([message])[0])
    await registry.notify_idle_interjections(key)
    return message
