from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.logging_context import reset_log_session, set_log_session
from app.middleware.auth import ACCESS_TOKEN_COOKIE_NAME, decode_and_validate_token
from app.models import Message, ToolApproval
from app.services.approvals import ApprovalService
from app.services.agent_run_registry import AgentRunRegistry
from app.services.compaction import CompactionService
from app.services.messages import normalize_generation_metadata, with_generation_metadata
from app.services.session_naming import SessionNamingService
from app.services.ws_manager import ConnectionManager
from app.services.ws_stream_parser import parse_ws_message
from app.services.ws_stream_service import (
    build_user_payload,
    get_owned_session,
    load_history,
    unresolved_tool_calls_from_history,
    maybe_auto_compact_and_resume,
    persist_user_message,
    run_agent_once,
)

router = APIRouter()
logger = logging.getLogger(__name__)

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
    naming_service = SessionNamingService(
        provider=getattr(getattr(websocket.app.state, "agent_loop", None), "provider", None),
        ws_manager=manager,
    )

    history = await load_history(db, id)
    unresolved_calls = unresolved_tool_calls_from_history(history)
    session_running = await run_registry.is_running(session_key)
    if unresolved_calls and not session_running:
        await _materialize_interrupted_tool_results(db, session_id=id, unresolved_calls=unresolved_calls)
        history = await load_history(db, id)
        unresolved_calls = []

    await websocket.send_json(
        {
            "type": "connected",
            "session_id": session_key,
            "history": history,
            "context_token_budget": int(settings.context_token_budget),
        }
    )

    approval_service = _resolve_approval_service(websocket)
    pending_matches = await approval_service.match_pending_for_unresolved_calls(
        db,
        session_id=id,
        unresolved_calls=unresolved_calls,
    )
    logger.info(
        "ws_unresolved_tool_rehydrate session_id=%s unresolved_count=%s pending_matches=%s",
        session_key,
        len(unresolved_calls),
        len(pending_matches),
    )

    for call in unresolved_calls:
        call_id = str(call.get("id") or "")
        pending_approval = pending_matches.get(call_id)
        pending_metadata: dict[str, object] = {
            "rehydrated": True,
        }
        pending_content = {
            "status": "running",
            "message": "Tool call is still running or waiting for completion.",
        }
        if pending_approval is not None:
            pending_metadata["pending"] = True
            approval_payload = {
                "provider": pending_approval.provider,
                "approval_id": pending_approval.approval_id,
                "status": pending_approval.status,
                "pending": pending_approval.pending,
                "can_resolve": pending_approval.can_resolve,
                "label": pending_approval.label,
                "session_id": pending_approval.session_id,
                "match_key": pending_approval.match_key,
            }
            pending_metadata["approval"] = approval_payload
            pending_content["approval"] = approval_payload
            pending_content["status"] = "pending"
            pending_content["message"] = "Tool call still running or waiting for approval."
            logger.info(
                "ws_unresolved_tool_pending_match session_id=%s tool_call_id=%s tool_name=%s provider=%s approval_id=%s",
                session_key,
                call_id,
                call.get("name"),
                pending_approval.provider,
                pending_approval.approval_id,
            )
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
                    "tool_arguments": call["arguments"],
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
                requested_tier=parsed.tier,
                temperature=0.7,
                max_iterations=parsed.max_iterations,
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
                    naming_service.maybe_auto_rename(
                        session_id=id,
                        force=True,
                        first_message=parsed.content,
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
                await naming_service.maybe_auto_rename(session_id=id)
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


def _resolve_approval_service(websocket: WebSocket) -> ApprovalService:
    service = getattr(websocket.app.state, "approval_service", None)
    if isinstance(service, ApprovalService):
        return service
    raise RuntimeError("Approval service is not initialized on app.state")


async def _materialize_interrupted_tool_results(
    db: AsyncSession,
    *,
    session_id: UUID,
    unresolved_calls: list[dict[str, object]],
) -> None:
    now = datetime.now(UTC)
    tool_pending_result = await db.execute(
        select(ToolApproval).where(
            ToolApproval.session_id == session_id,
            ToolApproval.status == "pending",
        )
    )
    tool_pending_rows = tool_pending_result.scalars().all()
    for row in tool_pending_rows:
        row.status = "cancelled"
        row.decision_note = "Cancelled automatically: backend run not active after reconnect"
        row.resolved_at = now

    payload = json.dumps(
        {
            "status": "interrupted",
            "message": (
                "Tool call was interrupted because no active run was found for this session "
                "(server restart or stop). Please retry."
            ),
        }
    )
    for call in unresolved_calls:
        call_id = str(call.get("id") or "").strip()
        if not call_id:
            continue
        call_name = str(call.get("name") or "unknown").strip() or "unknown"
        call_generation = normalize_generation_metadata(
            call.get("generation") if isinstance(call.get("generation"), dict) else None
        )
        metadata = with_generation_metadata(
            {
                "pending": False,
                "interrupted": True,
                "interrupted_reason": "run_not_active",
            },
            generation=call_generation,
        )
        db.add(
            Message(
                session_id=session_id,
                role="tool_result",
                content=payload,
                metadata_json=metadata,
                tool_call_id=call_id,
                tool_name=call_name,
            )
        )
    await db.commit()
