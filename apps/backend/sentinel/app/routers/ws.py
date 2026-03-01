from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.logging_context import reset_log_session, set_log_session
from app.middleware.auth import ACCESS_TOKEN_COOKIE_NAME, decode_and_validate_token
from app.services.agent_run_registry import AgentRunRegistry
from app.services.compaction import CompactionService
from app.services.ws_manager import ConnectionManager
from app.services.ws_stream_parser import parse_ws_message
from app.services.ws_stream_service import (
    build_user_payload,
    get_owned_session,
    load_history,
    maybe_auto_compact_and_resume,
    name_session,
    persist_user_message,
    run_agent_once,
)

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
        token = websocket.cookies.get(ACCESS_TOKEN_COOKIE_NAME)
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    try:
        user = await decode_and_validate_token(token, db, expected_type="access")
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    session = await get_owned_session(db, id, user.sub)
    if session is None:
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.accept()
    session_key = str(id)
    session_log_token = set_log_session(session_key)
    await manager.connect(session_key, websocket)

    history = await load_history(db, id)
    await websocket.send_json(
        {
            "type": "connected",
            "session_id": session_key,
            "history": history,
            "context_token_budget": int(settings.context_token_budget),
        }
    )
    session_has_messages = len(history) > 0

    try:
        while True:
            payload = await websocket.receive_text()
            parsed = parse_ws_message(payload)
            if parsed is None:
                await websocket.send_json({"type": "error", "code": "invalid_payload"})
                continue

            is_first_message = not session_has_messages
            message = await persist_user_message(
                db,
                session_id=id,
                session=session,
                content=parsed.content,
                attachments=parsed.attachments,
            )
            session_has_messages = True

            await manager.broadcast_message_ack(
                session_key,
                str(message.id),
                message.content,
                message.created_at,
                metadata=message.metadata_json or {},
            )

            agent_loop = getattr(websocket.app.state, "agent_loop", None)
            if agent_loop is None:
                await manager.broadcast_agent_error(
                    session_key, "No provider connected for agent reply."
                )
                await manager.broadcast_done(session_key, "error")
                continue

            if is_first_message and parsed.content:
                asyncio.create_task(
                    name_session(
                        session_id=id,
                        first_message=parsed.content,
                        manager=manager,
                        agent_loop=agent_loop,
                    )
                )

            await manager.broadcast_agent_thinking(session_key)
            outcome = await run_agent_once(
                db=db,
                session_id=id,
                session_key=session_key,
                manager=manager,
                run_registry=run_registry,
                agent_loop=agent_loop,
                payload=build_user_payload(parsed),
                tier=parsed.tier,
                max_iterations=parsed.max_iterations,
                persist_user_message=False,
            )
            if outcome.failed:
                continue

            if not outcome.cancelled and not outcome.run_error:
                await maybe_auto_compact_and_resume(
                    db=db,
                    session_id=id,
                    session_key=session_key,
                    manager=manager,
                    run_registry=run_registry,
                    agent_loop=agent_loop,
                    tier=parsed.tier,
                    max_iterations=parsed.max_iterations,
                    auto_resume_prompt=_COMPACTION_AUTO_RESUME_PROMPT,
                    compaction_service_cls=CompactionService,
                )
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reset_log_session(session_log_token)
        await manager.disconnect(session_key, websocket)


def _resolve_manager(websocket: WebSocket) -> ConnectionManager:
    manager = getattr(websocket.app.state, "ws_manager", None)
    if isinstance(manager, ConnectionManager):
        return manager
    raise RuntimeError("WebSocket manager is not initialized on app.state")


def _resolve_run_registry(websocket: WebSocket) -> AgentRunRegistry:
    registry = getattr(websocket.app.state, "agent_run_registry", None)
    if isinstance(registry, AgentRunRegistry):
        return registry
    raise RuntimeError("Agent run registry is not initialized on app.state")
