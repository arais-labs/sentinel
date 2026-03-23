from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Message, Session
from app.services.agent.agent_modes import AgentMode, normalize_agent_mode_value
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.compaction import CompactionService
from app.services.llm.generic.types import AgentEvent, ImageContent, TextContent
from app.services.llm.ids import TierName
from app.services.messages import (
    build_generation_metadata,
    normalize_generation_metadata,
    web_ingress_metadata,
    with_generation_metadata,
)
from app.services.sessions.session_naming import (
    apply_conversation_message_delta,
    conversation_delta_for_role,
)
from app.services.ws.ws_manager import ConnectionManager
from app.services.ws.ws_stream_parser import ParsedWsMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentRunOutcome:
    failed: bool
    cancelled: bool
    run_error: str | None


class AgentLoopProtocol(Protocol):
    provider: Any

    async def run(
        self,
        db: AsyncSession,
        session_id: UUID,
        user_message: str | list[TextContent | ImageContent],
        *,
        persist_user_message: bool,
        on_event: Any,
        model: str,
        max_iterations: int,
        agent_mode: AgentMode,
        persist_incremental: bool,
        user_metadata: dict[str, Any] | None = None,
    ) -> Any: ...


async def get_owned_session(db: AsyncSession, session_id: UUID, user_id: str) -> Session | None:
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user_id)
    )
    return result.scalars().first()


async def load_history(db: AsyncSession, session_id: UUID) -> list[dict[str, Any]]:
    result = await db.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at.asc())
    )
    messages = result.scalars().all()
    return [
        {
            "id": str(message.id),
            "role": message.role,
            "content": message.content,
            "tool_call_id": message.tool_call_id,
            "tool_name": message.tool_name,
            "metadata": message.metadata_json or {},
            "created_at": _iso(message.created_at),
        }
        for message in messages
    ]


def unresolved_tool_calls_from_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    resolved_ids: set[str] = set()
    pending_order: list[str] = []
    pending: dict[str, dict[str, Any]] = {}

    for item in history:
        role = str(item.get("role") or "")
        if role == "assistant":
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                continue
            tool_calls = metadata.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for raw_call in tool_calls:
                if not isinstance(raw_call, dict):
                    continue
                call_id = str(raw_call.get("id") or "").strip()
                if not call_id or call_id in resolved_ids:
                    continue
                if call_id in pending:
                    continue
                name = str(raw_call.get("name") or "unknown")
                arguments = raw_call.get("arguments")
                generation = normalize_generation_metadata(
                    metadata.get("generation") if isinstance(metadata.get("generation"), dict) else None
                )
                pending[call_id] = {
                    "id": call_id,
                    "name": name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                    "generation": generation,
                }
                pending_order.append(call_id)
            continue

        if role not in {"tool", "tool_result"}:
            continue
        call_id = str(item.get("tool_call_id") or "").strip()
        if not call_id:
            continue
        resolved_ids.add(call_id)
        pending.pop(call_id, None)

    return [pending[call_id] for call_id in pending_order if call_id in pending]


async def persist_user_message(
    db: AsyncSession,
    *,
    session_id: UUID,
    session: Session,
    content: str,
    attachments: list[dict[str, Any]],
    requested_tier: TierName | None,
    temperature: float,
    max_iterations: int,
    agent_mode: AgentMode,
) -> Message:
    metadata: dict[str, Any] = web_ingress_metadata()
    metadata["agent_mode"] = normalize_agent_mode_value(agent_mode)
    if attachments:
        metadata["attachments"] = attachments
    generation = build_generation_metadata(
        requested_tier=requested_tier or TierName.NORMAL,
        resolved_model=None,
        provider=None,
        temperature=temperature,
        max_iterations=max_iterations,
    )
    metadata = with_generation_metadata(metadata, generation=generation)
    if content and not session.initial_prompt:
        session.initial_prompt = content
    apply_conversation_message_delta(session, conversation_delta_for_role("user"))

    message = Message(
        session_id=session_id,
        role="user",
        content=content,
        metadata_json=metadata,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


def build_user_payload(parsed: ParsedWsMessage) -> str | list[TextContent | ImageContent]:
    if not parsed.attachments:
        return parsed.content

    user_blocks: list[TextContent | ImageContent] = []
    if parsed.content:
        user_blocks.append(TextContent(text=parsed.content))
    for item in parsed.attachments:
        user_blocks.append(
            ImageContent(
                media_type=str(item.get("mime_type", "image/png")),
                data=str(item.get("base64", "")),
            )
        )
    return user_blocks


async def run_agent_once(
    *,
    db: AsyncSession,
    session_id: UUID,
    session_key: str,
    manager: ConnectionManager,
    run_registry: AgentRunRegistry,
    agent_loop: AgentLoopProtocol,
    payload: str | list[TextContent | ImageContent],
    tier: TierName | None,
    max_iterations: int,
    agent_mode: AgentMode,
    persist_user_message: bool,
) -> AgentRunOutcome:
    async def _broadcast_event(event: AgentEvent) -> None:
        await manager.broadcast_agent_event(session_key, event)

    run_task = asyncio.create_task(
        agent_loop.run(
            db,
            session_id,
            payload,
            persist_user_message=persist_user_message,
            on_event=_broadcast_event,
            model=(tier or TierName.NORMAL).value,
            max_iterations=max_iterations,
            agent_mode=agent_mode,
            persist_incremental=True,
        )
    )
    registered = await run_registry.register(session_key, run_task)
    if not registered:
        run_task.cancel()
        await manager.broadcast_agent_error(
            session_key, "Agent is already processing this session."
        )
        await manager.broadcast_done(session_key, "error")
        return AgentRunOutcome(
            failed=True,
            cancelled=False,
            run_error="Agent is already processing this session.",
        )

    cancelled = False
    run_error: str | None = None
    failed = False
    try:
        run_result = await run_task
        run_error = getattr(run_result, "error", None)
        cancelled = run_error == "Generation stopped by user"
    except asyncio.CancelledError:
        cancelled = True
    except Exception as exc:  # noqa: BLE001
        failed = True
        await manager.broadcast_agent_error(session_key, str(exc))
        await manager.broadcast_done(session_key, "error")
    finally:
        await run_registry.clear(session_key, run_task)

    return AgentRunOutcome(failed=failed, cancelled=cancelled, run_error=run_error)


async def maybe_auto_compact_and_resume(
    *,
    db: AsyncSession,
    session_id: UUID,
    session_key: str,
    manager: ConnectionManager,
    run_registry: AgentRunRegistry,
    agent_loop: AgentLoopProtocol,
    tier: TierName | None,
    max_iterations: int,
    agent_mode: AgentMode,
    auto_resume_prompt: str,
    compaction_service_cls: type[CompactionService] = CompactionService,
) -> None:
    try:
        compaction_svc = compaction_service_cls(provider=getattr(agent_loop, "provider", None))
        should_compact = await compaction_svc.should_auto_compact(db, session_id=session_id)
        if not should_compact:
            return

        await manager.broadcast(
            session_key,
            {"type": "compaction_started", "session_id": session_key},
        )
        result = await compaction_svc.auto_compact_if_needed(db, session_id=session_id)
        if result is None:
            return

        await manager.broadcast(
            session_key,
            {
                "type": "compaction_completed",
                "session_id": session_key,
                "raw_token_count": result.raw_token_count,
                "compressed_token_count": result.compressed_token_count,
                "summary_preview": result.summary_preview,
            },
        )
        if not settings.compaction_auto_resume_enabled:
            return

        await manager.broadcast(
            session_key,
            {"type": "compaction_resuming", "session_id": session_key},
        )
        await manager.broadcast_agent_thinking(session_key)
        await run_agent_once(
            db=db,
            session_id=session_id,
            session_key=session_key,
            manager=manager,
            run_registry=run_registry,
            agent_loop=agent_loop,
            payload=auto_resume_prompt,
            tier=tier,
            max_iterations=max_iterations,
            agent_mode=agent_mode,
            persist_user_message=False,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Auto-compaction failed for session %s", session_id, exc_info=True)
        await manager.broadcast(
            session_key,
            {
                "type": "compaction_failed",
                "session_id": session_key,
                "error": "Auto-compaction failed",
            },
        )

def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
