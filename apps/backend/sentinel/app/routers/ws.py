from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from typing import Any
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.middleware.auth import decode_and_validate_token
from app.models import Message, Session
from app.logging_context import reset_log_session, set_log_session
from app.services.compaction import CompactionService
from app.services.agent_run_registry import AgentRunRegistry
from app.services.llm.types import AgentEvent, ToolCallContent
from app.services.ws_manager import ConnectionManager

logger = logging.getLogger(__name__)

router = APIRouter()

_COMPACTION_AUTO_RESUME_PROMPT = (
    "Context was compacted automatically. Continue the same task from the latest state. "
    "Do not repeat finished steps. If you are blocked, say exactly what is blocking you and the best next step."
)


@router.websocket("/{id}/stream")
async def stream_session(
    websocket: WebSocket,
    id: UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    manager = _resolve_manager(websocket)
    run_registry = _resolve_run_registry(websocket)
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    try:
        user = await decode_and_validate_token(token, db, expected_type="access")
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    session = await _get_owned_session(db, id, user.sub)
    if session is None:
        await websocket.close(code=4004, reason="Session not found")
        return
    if session.status == "ended":
        await websocket.close(code=4003, reason="Session ended")
        return

    await websocket.accept()
    session_key = str(id)
    session_log_token = set_log_session(session_key)
    await manager.connect(session_key, websocket)

    history = await _load_history(db, id)
    await websocket.send_json({"type": "connected", "session_id": session_key, "history": history})
    session_has_messages = len(history) > 0

    try:
        while True:
            payload = await websocket.receive_text()
            result = _parse_message(payload)
            if result is None:
                await websocket.send_json({"type": "error", "code": "invalid_payload"})
                continue
            parsed, model_hint, max_iterations, attachments = result

            is_first_message = not session_has_messages
            parsed_text = parsed.strip()

            metadata: dict[str, Any] = {"source": "web"}
            if attachments:
                metadata["attachments"] = attachments
            if parsed_text and not session.initial_prompt:
                session.initial_prompt = parsed_text
            message = Message(
                session_id=id,
                role="user",
                content=parsed,
                metadata_json=metadata,
            )
            db.add(message)
            await db.commit()
            await db.refresh(message)
            session_has_messages = True

            await manager.broadcast_message_ack(
                session_key,
                str(message.id),
                message.content,
                message.created_at,
                metadata=metadata,
            )

            agent_loop = getattr(websocket.app.state, "agent_loop", None)
            if agent_loop is None:
                await manager.broadcast_agent_error(
                    session_key, "No provider connected for agent reply."
                )
                await manager.broadcast_done(session_key, "error")
                continue

            if is_first_message and parsed and agent_loop is not None:
                asyncio.create_task(_name_session(id, parsed, manager, agent_loop))

            await manager.broadcast_agent_thinking(session_key)

            async def _broadcast_event(event: AgentEvent) -> None:
                await manager.broadcast_agent_event(session_key, event)

            from app.services.llm.types import ImageContent, TextContent

            if attachments:
                user_blocks: list[TextContent | ImageContent] = []
                if parsed:
                    user_blocks.append(TextContent(text=parsed))
                for item in attachments:
                    user_blocks.append(
                        ImageContent(
                            media_type=str(item.get("mime_type", "image/png")),
                            data=str(item.get("base64", "")),
                        )
                    )
                user_payload: str | list[TextContent | ImageContent] = user_blocks
            else:
                user_payload = parsed

            async def _run_once(
                payload: str | list[TextContent | ImageContent],
                *,
                persist_user_message: bool,
            ) -> tuple[bool, str | None, bool]:
                run_task = asyncio.create_task(
                    agent_loop.run(
                        db,
                        id,
                        payload,
                        persist_user_message=persist_user_message,
                        on_event=_broadcast_event,
                        model=model_hint or "hint:reasoning",
                        max_iterations=max_iterations,
                        allow_high_risk=True,
                    )
                )
                registered = await run_registry.register(session_key, run_task)
                if not registered:
                    run_task.cancel()
                    await manager.broadcast_agent_error(
                        session_key, "Agent is already processing this session."
                    )
                    await manager.broadcast_done(session_key, "error")
                    return (False, "Agent is already processing this session.", True)

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

                return (cancelled, run_error, failed)

            cancelled, run_error, failed = await _run_once(
                user_payload,
                persist_user_message=False,
            )
            if failed:
                continue

            # Fire-and-forget auto-compaction
            if not cancelled and not run_error:
                try:
                    compaction_svc = CompactionService(
                        provider=getattr(agent_loop, "provider", None)
                    )
                    should_compact = await compaction_svc.should_auto_compact(db, session_id=id)
                    if should_compact:
                        await manager.broadcast(
                            session_key,
                            {"type": "compaction_started", "session_id": session_key},
                        )
                        result = await compaction_svc.auto_compact_if_needed(db, session_id=id)
                        if result is not None:
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
                            if settings.compaction_auto_resume_enabled:
                                await manager.broadcast(
                                    session_key,
                                    {"type": "compaction_resuming", "session_id": session_key},
                                )
                                await manager.broadcast_agent_thinking(session_key)
                                _, _, resume_failed = await _run_once(
                                    _COMPACTION_AUTO_RESUME_PROMPT,
                                    persist_user_message=False,
                                )
                                if resume_failed:
                                    continue
                except Exception:  # noqa: BLE001
                    logger.warning("Auto-compaction failed for session %s", id, exc_info=True)
                    await manager.broadcast(
                        session_key,
                        {
                            "type": "compaction_failed",
                            "session_id": session_key,
                            "error": "Auto-compaction failed",
                        },
                    )
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reset_log_session(session_log_token)
        await manager.disconnect(session_key, websocket)


async def _get_owned_session(db: AsyncSession, session_id: UUID, user_id: str) -> Session | None:
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user_id)
    )
    return result.scalars().first()


async def _load_history(db: AsyncSession, session_id: UUID) -> list[dict]:
    result = await db.execute(select(Message).where(Message.session_id == session_id))
    messages = result.scalars().all()
    messages.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC))
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


_ALLOWED_IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
}
_MAX_MESSAGE_ATTACHMENTS = 4
_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


def _parse_message(payload: str) -> tuple[str, str | None, int, list[dict[str, Any]]] | None:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None
    if parsed.get("type") != "message":
        return None
    content = parsed.get("content")
    if not isinstance(content, str):
        return None
    trimmed = content.strip()
    attachments = _normalize_attachments(parsed.get("attachments"))
    if attachments is None:
        return None
    if not trimmed and not attachments:
        return None
    model = parsed.get("model")
    if not isinstance(model, str) or not model.strip():
        model = None
    raw_iters = parsed.get("max_iterations")
    max_iterations = int(raw_iters) if isinstance(raw_iters, int) and 1 <= raw_iters <= 100 else 25
    return (trimmed, model, max_iterations, attachments)


def _normalize_attachments(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    if len(value) > _MAX_MESSAGE_ATTACHMENTS:
        return None

    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        mime_type = str(item.get("mime_type") or "").strip().lower()
        if mime_type not in _ALLOWED_IMAGE_MIME_TYPES:
            return None
        raw_base64 = item.get("base64")
        if not isinstance(raw_base64, str):
            return None
        base64_data = raw_base64.strip()
        if ";base64," in base64_data:
            _, _, base64_data = base64_data.partition(";base64,")
        if not base64_data:
            return None
        try:
            decoded = base64.b64decode(base64_data, validate=True)
        except (binascii.Error, ValueError):
            return None
        if len(decoded) > _MAX_ATTACHMENT_BYTES:
            return None
        filename_raw = item.get("filename")
        filename = filename_raw.strip() if isinstance(filename_raw, str) else None
        normalized.append(
            {
                "mime_type": mime_type,
                "base64": base64_data,
                "filename": filename[:200] if filename else None,
                "size_bytes": len(decoded),
            }
        )
    return normalized


def _resolve_manager(websocket: WebSocket) -> ConnectionManager:
    manager = getattr(websocket.app.state, "ws_manager", None)
    if isinstance(manager, ConnectionManager):
        return manager
    fallback = ConnectionManager()
    websocket.app.state.ws_manager = fallback
    return fallback


def _resolve_run_registry(websocket: WebSocket) -> AgentRunRegistry:
    registry = getattr(websocket.app.state, "agent_run_registry", None)
    if isinstance(registry, AgentRunRegistry):
        return registry
    fallback = AgentRunRegistry()
    websocket.app.state.agent_run_registry = fallback
    return fallback


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _event_payload(event: AgentEvent) -> dict:
    payload: dict = {"type": event.type}
    if event.delta is not None:
        payload["delta"] = event.delta
    if event.content_index is not None:
        payload["content_index"] = event.content_index
    if event.stop_reason is not None:
        payload["stop_reason"] = event.stop_reason
    if event.error is not None:
        payload["error"] = event.error
    if event.tool_call is not None:
        payload["tool_call"] = _tool_call_payload(event.tool_call)
    return payload


def _tool_call_payload(call: ToolCallContent) -> dict:
    return {
        "id": call.id,
        "name": call.name,
        "arguments": call.arguments,
    }


async def _name_session(
    session_id: UUID,
    first_message: str,
    manager: ConnectionManager,
    agent_loop: object,
) -> None:
    """Generate a short session title from the first message (fire-and-forget)."""
    from app.database import AsyncSessionLocal
    from app.models import Session as SessionModel
    from app.services.llm.types import TextContent, UserMessage

    prompt = (
        "Generate a very short title (3-6 words max) for a chat session that starts with "
        "this message. Reply with ONLY the title, no quotes, no punctuation at the end.\n\n"
        f"Message: {first_message[:300]}"
    )
    try:
        result = await agent_loop.provider.chat(  # type: ignore[union-attr]
            [UserMessage(content=prompt)],
            model="hint:fast",
            tools=[],
            temperature=0.3,
        )
        title = ""
        for block in result.content:
            if isinstance(block, TextContent):
                title += block.text
        title = title.strip()[:80]
        if not title:
            return

        async with AsyncSessionLocal() as db:
            db_result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
            session = db_result.scalars().first()
            if session is None:
                return
            session.title = title
            await db.commit()

        await manager.broadcast(
            str(session_id),
            {"type": "session_named", "session_id": str(session_id), "title": title},
        )
    except Exception:
        logger.warning("Auto-naming failed for session %s", session_id, exc_info=True)
