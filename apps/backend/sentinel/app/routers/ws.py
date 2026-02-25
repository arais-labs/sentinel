from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.auth import decode_and_validate_token
from app.models import Message, Session
from app.services.compaction import CompactionService
from app.services.agent_run_registry import AgentRunRegistry
from app.services.llm.types import AgentEvent, ToolCallContent
from app.services.ws_manager import ConnectionManager

logger = logging.getLogger(__name__)

router = APIRouter()


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
    await manager.connect(session_key, websocket)

    history = await _load_history(db, id)
    await websocket.send_json({"type": "connected", "session_id": session_key, "history": history})

    try:
        while True:
            payload = await websocket.receive_text()
            result = _parse_message(payload)
            if result is None:
                await websocket.send_json({"type": "error", "code": "invalid_payload"})
                continue
            parsed, model_hint, max_iterations = result

            # Check before insert whether this is the first user message
            count_result = await db.execute(
                select(func.count()).select_from(Message).where(Message.session_id == id)
            )
            is_first_message = count_result.scalar_one() == 0

            message = Message(
                session_id=id,
                role="user",
                content=parsed,
                metadata_json={"source": "web"},
            )
            db.add(message)
            await db.commit()
            await db.refresh(message)

            await manager.broadcast_message_ack(
                session_key,
                str(message.id),
                message.content,
                message.created_at,
            )

            agent_loop = getattr(websocket.app.state, "agent_loop", None)
            if agent_loop is None:
                await manager.broadcast_agent_error(session_key, "No provider connected for agent reply.")
                await manager.broadcast_done(session_key, "error")
                continue

            if is_first_message and agent_loop is not None:
                asyncio.create_task(_name_session(id, parsed, manager, agent_loop))

            await manager.broadcast_agent_thinking(session_key)

            async def _broadcast_event(event: AgentEvent) -> None:
                await manager.broadcast_agent_event(session_key, event)

            run_task = asyncio.create_task(
                agent_loop.run(
                    db,
                    id,
                    parsed,
                    persist_user_message=False,
                    on_event=_broadcast_event,
                    model=model_hint or "hint:reasoning",
                    max_iterations=max_iterations,
                    allow_high_risk=True,
                )
            )
            registered = await run_registry.register(session_key, run_task)
            if not registered:
                run_task.cancel()
                await manager.broadcast_agent_error(session_key, "Agent is already processing this session.")
                await manager.broadcast_done(session_key, "error")
                continue

            cancelled = False
            try:
                run_result = await run_task
                cancelled = run_result.error == "Generation stopped by user"
            except asyncio.CancelledError:
                cancelled = True
            except Exception as exc:  # noqa: BLE001
                await manager.broadcast_agent_error(session_key, str(exc))
                await manager.broadcast_done(session_key, "error")
            finally:
                await run_registry.clear(session_key, run_task)

            # Fire-and-forget auto-compaction
            if not cancelled:
                try:
                    compaction_svc = CompactionService(provider=agent_loop.provider)
                    await compaction_svc.auto_compact_if_needed(db, session_id=id)
                except Exception:  # noqa: BLE001
                    logger.warning("Auto-compaction failed for session %s", id, exc_info=True)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await manager.disconnect(session_key, websocket)


async def _get_owned_session(db: AsyncSession, session_id: UUID, user_id: str) -> Session | None:
    result = await db.execute(select(Session).where(Session.id == session_id, Session.user_id == user_id))
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


def _parse_message(payload: str) -> tuple[str, str | None, int] | None:
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
    if not trimmed:
        return None
    model = parsed.get("model")
    if not isinstance(model, str) or not model.strip():
        model = None
    raw_iters = parsed.get("max_iterations")
    max_iterations = int(raw_iters) if isinstance(raw_iters, int) and 1 <= raw_iters <= 100 else 25
    return (trimmed, model, max_iterations)


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
