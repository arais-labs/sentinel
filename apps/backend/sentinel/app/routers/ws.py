from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_connection_instance_runtime_context, get_db, get_manager_db
from app.logging_context import reset_log_session, set_log_session
from app.middleware.auth import ACCESS_TOKEN_COOKIE_NAME, decode_and_validate_token
from app.models import Message, ToolApproval
from app.services.runtime import get_runtime
from app.services.runtime.terminal_manager import get_terminal_manager
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.compaction import CompactionService
from app.services.messages import normalize_generation_metadata, with_generation_metadata
from app.services.sessions.session_naming import SessionNamingService
from app.services.runtime.activation import queue_runtime_activation
from app.services.ws.ws_manager import ConnectionManager
from app.services.ws.ws_stream_parser import parse_ws_message
from app.services.ws.ws_stream_service import (
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


async def _persist_retryable_error(
    db: AsyncSession,
    *,
    message: Message,
    error: str,
) -> None:
    metadata = dict(message.metadata_json or {}) if isinstance(message.metadata_json, dict) else {}
    metadata["retryable_error"] = error
    message.metadata_json = metadata
    await db.commit()


async def _clear_retryable_error(
    db: AsyncSession,
    *,
    message: Message,
) -> None:
    metadata = dict(message.metadata_json or {}) if isinstance(message.metadata_json, dict) else {}
    if "retryable_error" not in metadata:
        return
    metadata.pop("retryable_error", None)
    message.metadata_json = metadata
    await db.commit()


@router.websocket("/{id}/stream")
async def stream_session(
    websocket: WebSocket,
    id: UUID,
    db: AsyncSession = Depends(get_db),
    manager_db: AsyncSession = Depends(get_manager_db),
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
        user = await decode_and_validate_token(token, manager_db, expected_type="access")
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
    instance_context = get_connection_instance_runtime_context(websocket)
    naming_service = SessionNamingService(
        provider=getattr(instance_context.agent_runtime_support, "provider", None),
        ws_manager=manager,
    )

    history = await load_history(db, id)
    unresolved_calls = unresolved_tool_calls_from_history(history)
    session_running = await run_registry.is_running(session_key)
    if unresolved_calls and not session_running:
        await _materialize_interrupted_tool_results(db, session_id=id, unresolved_calls=unresolved_calls)
        history = await load_history(db, id)
        unresolved_calls = []

    terminals_payload = await _initial_terminal_descriptors(session_key)

    if not await _try_send_json(
        websocket,
        {
            "type": "connected",
            "session_id": session_key,
            "history": history,
            "context_token_budget": int(settings.context_token_budget),
            "terminals": terminals_payload,
        },
    ):
        reset_log_session(session_log_token)
        await manager.disconnect(session_key, websocket)
        return

    queue_runtime_activation(websocket.app, session_key)

    logger.info(
        "ws_unresolved_tool_rehydrate session_id=%s unresolved_count=%s",
        session_key,
        len(unresolved_calls),
    )

    pending_approvals = await _load_pending_tool_approvals(db, session_id=id)

    for call in unresolved_calls:
        pending_metadata: dict[str, object] = {"rehydrated": True}
        pending_content: dict[str, object]
        pending_approval = _match_pending_tool_approval(call, pending_approvals)
        if pending_approval is not None:
            pending_metadata["pending"] = True
            pending_metadata["approval"] = _approval_payload(pending_approval)
            pending_content = {
                "status": "pending",
                "message": "Action requires approval.",
            }
        else:
            pending_content = {
                "status": "running",
                "message": "Tool call is still running or waiting for completion.",
            }
        if not await _try_send_json(
            websocket,
            {
                "type": "toolcall_start",
                "session_id": session_key,
                "tool_call": {
                    "id": call["id"],
                    "name": call["name"],
                    "arguments": call["arguments"],
                },
            },
        ):
            reset_log_session(session_log_token)
            await manager.disconnect(session_key, websocket)
            return
        if not await _try_send_json(
            websocket,
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
            },
        ):
            reset_log_session(session_log_token)
            await manager.disconnect(session_key, websocket)
            return
    current_phase = await run_registry.get_phase(session_key) if session_running else None
    if session_running and not unresolved_calls and (current_phase in {None, "thinking"}):
        await manager.broadcast_thinking_start(session_key)
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
                agent_mode=parsed.agent_mode,
            )
            session_has_messages = True

            await manager.broadcast_message_ack(
                session_key,
                str(message.id),
                message.content,
                message.created_at,
                metadata=message.metadata_json or {},
            )

            agent_runtime_support = get_connection_instance_runtime_context(websocket).agent_runtime_support
            if agent_runtime_support is None:
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
                agent_runtime_support=agent_runtime_support,
                payload=build_user_payload(parsed),
                tier=parsed.tier,
                max_iterations=parsed.max_iterations,
                agent_mode=parsed.agent_mode,
                persist_user_message=False,
            )
            if outcome.failed or (outcome.run_error and not outcome.cancelled):
                await _persist_retryable_error(
                    db,
                    message=message,
                    error=outcome.run_error or "Agent failed",
                )
                continue
            await _clear_retryable_error(db, message=message)
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
                    agent_runtime_support=agent_runtime_support,
                    tier=parsed.tier,
                    max_iterations=parsed.max_iterations,
                    agent_mode=parsed.agent_mode,
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


async def _load_pending_tool_approvals(
    db: AsyncSession,
    *,
    session_id: UUID,
) -> dict[str, list[ToolApproval]]:
    result = await db.execute(
        select(ToolApproval)
        .where(
            ToolApproval.session_id == session_id,
            ToolApproval.status == "pending",
        )
        .order_by(ToolApproval.created_at.asc())
    )
    approvals_by_tool: dict[str, list[ToolApproval]] = defaultdict(list)
    for row in result.scalars().all():
        tool_name = (row.tool_name or "").strip()
        if tool_name:
            approvals_by_tool[tool_name].append(row)
    return approvals_by_tool


async def _try_send_json(websocket: WebSocket, payload: dict[str, Any]) -> bool:
    try:
        await websocket.send_json(payload)
    except (WebSocketDisconnect, RuntimeError):
        return False
    return True


def _match_pending_tool_approval(
    call: dict[str, object],
    pending_approvals: dict[str, list[ToolApproval]],
) -> ToolApproval | None:
    tool_name = str(call.get("name") or "").strip()
    if not tool_name:
        return None

    candidates = pending_approvals.get(tool_name)
    if not candidates:
        return None

    expected_action = _expected_approval_action(call)
    if expected_action:
        for index, row in enumerate(candidates):
            if (row.action or "").strip() == expected_action:
                return candidates.pop(index)
        return None

    return candidates.pop(0)


def _expected_approval_action(call: dict[str, object]) -> str | None:
    tool_name = str(call.get("name") or "").strip()
    arguments = call.get("arguments")
    if not tool_name or not isinstance(arguments, dict):
        return None
    command = arguments.get("command")
    if not isinstance(command, str):
        return None
    normalized = command.strip()
    if not normalized:
        return None
    return f"{tool_name}.{normalized}"


def _approval_payload(row: ToolApproval) -> dict[str, Any]:
    return {
        "provider": row.provider,
        "approval_id": str(row.id),
        "status": row.status,
        "pending": row.status == "pending",
        "can_resolve": row.status == "pending",
        "label": f"{row.tool_name} approval",
        "action": row.action,
        "description": row.description,
        "session_id": str(row.session_id) if row.session_id else None,
    }

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
    call_ids = [str(call.get("id") or "").strip() for call in unresolved_calls if str(call.get("id") or "").strip()]
    existing_result = await db.execute(
        select(Message).where(
            Message.session_id == session_id,
            Message.role == "tool_result",
            Message.tool_call_id.in_(call_ids),
        )
    )
    existing_messages = {
        str(row.tool_call_id or "").strip(): row
        for row in existing_result.scalars().all()
        if str(row.tool_call_id or "").strip()
    }
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
        existing_message = existing_messages.get(call_id)
        if existing_message is not None:
            _apply_terminal_tool_result_update(
                existing_message,
                content=payload,
                metadata=metadata,
                approval_status="cancelled",
                decision_note="Cancelled automatically: backend run not active after reconnect",
            )
            continue
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


def _apply_terminal_tool_result_update(
    message: Message,
    *,
    content: str,
    metadata: dict[str, object],
    approval_status: str,
    decision_note: str,
) -> None:
    existing_metadata = dict(message.metadata_json or {}) if isinstance(message.metadata_json, dict) else {}
    approval = existing_metadata.get("approval")
    if isinstance(approval, dict):
        next_approval = dict(approval)
        next_approval["status"] = approval_status
        next_approval["pending"] = False
        next_approval["can_resolve"] = False
        next_approval["decision_note"] = decision_note
        metadata["approval"] = next_approval
    message.content = content
    message.metadata_json = metadata


async def _initial_terminal_descriptors(session_key: str) -> list[dict[str, Any]]:
    """Best-effort: tell the freshly-connected client which terminals are alive.

    Tried in this order so we never block the WS handshake:
      1. In-memory state from the TerminalManager singleton (fast path).
      2. Rehydrate via `tmux list-sessions` if the runtime is already ensured.
    Any failure here is silent — the client just sees an empty pill row until
    the agent runs something.
    """
    manager = get_terminal_manager()
    descriptors = manager.descriptors_for(session_key)
    if descriptors:
        return descriptors
    try:
        runtime = await get_runtime().ensure(session_key)
    except Exception:
        return descriptors
    try:
        await manager.rehydrate(runtime=runtime, session_id=session_key)
    except Exception:
        return manager.descriptors_for(session_key)
    return manager.descriptors_for(session_key)


@router.websocket("/{id}/terminals/{terminal_id}")
async def stream_terminal(
    websocket: WebSocket,
    id: UUID,
    terminal_id: str,
    db: AsyncSession = Depends(get_db),
    manager_db: AsyncSession = Depends(get_manager_db),
) -> None:
    """Bidirectional bridge between xterm.js and the tmux session in the guest VM.

    Output side: tail the `pipe-pane` log over SSH and stream binary frames.
    Input side: route WS messages to tmux send-keys (or `resize-window` for
    JSON `{type:"resize"}` control frames). Cookie/query token auth identical
    to the regular `/stream` endpoint so we don't open a credential side door.
    """
    token = websocket.query_params.get("token")
    if not token:
        token = websocket.cookies.get(ACCESS_TOKEN_COOKIE_NAME)
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    try:
        user = await decode_and_validate_token(token, manager_db, expected_type="access")
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    session = await get_owned_session(db, id, user.sub)
    if session is None:
        await websocket.close(code=4004, reason="Session not found")
        return

    try:
        runtime = await get_runtime().ensure(str(id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("terminal attach could not ensure runtime: %s", exc)
        await websocket.close(code=4005, reason="Runtime not ready")
        return

    await websocket.accept()
    logger.info("terminal WS attached session=%s terminal_id=%s", id, terminal_id)
    try:
        await get_terminal_manager().attach_ws(
            runtime=runtime,
            session_id=id,
            terminal_id=terminal_id,
            websocket=websocket,
        )
        logger.info("terminal WS attach_ws returned normally session=%s terminal_id=%s", id, terminal_id)
    except WebSocketDisconnect:
        logger.info("terminal WS disconnected by client session=%s terminal_id=%s", id, terminal_id)
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("terminal WS bridge crashed session=%s terminal_id=%s: %s", id, terminal_id, exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
