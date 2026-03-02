from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.logging_context import reset_log_session, set_log_session
from app.middleware.auth import ACCESS_TOKEN_COOKIE_NAME, decode_and_validate_token
from app.models import GitPushApproval
from app.services.agent_run_registry import AgentRunRegistry
from app.services.compaction import CompactionService
from app.services.ws_manager import ConnectionManager
from app.services.ws_stream_parser import parse_ws_message
from app.services.ws_stream_service import (
    build_user_payload,
    get_owned_session,
    load_history,
    unresolved_tool_calls_from_history,
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
    pending_result = await db.execute(
        select(GitPushApproval).where(
            GitPushApproval.session_id == id,
            GitPushApproval.status == "pending",
        )
    )
    pending_rows = pending_result.scalars().all()
    pending_rows.sort(
        key=lambda item: item.created_at or item.updated_at,
        reverse=True,
    )
    pending_by_command: dict[str, list[GitPushApproval]] = {}
    pending_pool: list[GitPushApproval] = []
    for row in pending_rows:
        pending_pool.append(row)
        key = _normalize_git_command(row.command)
        pending_by_command.setdefault(key, []).append(row)

    def _match_pending_for_call(call: dict[str, object]) -> str | None:
        if call.get("name") != "git_exec":
            return None
        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            return None
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return None
        key = _normalize_git_command(command)
        bucket = pending_by_command.get(key)
        while bucket:
            candidate = bucket.pop(0)
            if candidate in pending_pool:
                pending_pool.remove(candidate)
                return str(candidate.id)
        return None

    for call in unresolved_tool_calls_from_history(history):
        if call.get("name") != "git_exec":
            continue
        approval_id = _match_pending_for_call(call)
        if approval_id is None:
            continue
        pending_metadata: dict[str, object] = {
            "pending": True,
            "rehydrated": True,
            "approval_id": approval_id,
        }
        pending_content = {
            "status": "pending",
            "message": "Tool call still running or waiting for approval.",
            "approval_id": approval_id,
        }
        await websocket.send_json(
            {
                "type": "toolcall_start",
                "session_id": session_key,
                "tool_call": {
                    "id": call["id"],
                    "name": call["name"],
                    "arguments": call["arguments"],
                },
            }
        )
        await websocket.send_json(
            {
                "type": "tool_result",
                "session_id": session_key,
                "tool_result": {
                    "tool_call_id": call["id"],
                    "tool_name": call["name"],
                    "content": pending_content,
                    "is_error": False,
                    "metadata": pending_metadata,
                },
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


def _normalize_git_command(command: str) -> str:
    return " ".join(command.strip().split()).lower()
