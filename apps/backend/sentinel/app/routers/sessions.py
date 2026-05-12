from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CHAT_DEFAULT_ITERATIONS
from app.database import AsyncSessionLocal
from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.models import Message, Session
from app.schemas.sessions import (
    ChatRequest,
    ChatResponse,
    CreateMessageRequest,
    CreateSessionRequest,
    MessageListResponse,
    MessageResponse,
    UpdateSessionRequest,
    SessionListItemResponse,
    SessionListResponse,
    SessionContextUsageResponse,
    SessionRuntimeCleanupResponse,
    SessionRuntimeGitChangedFilesResponse,
    SessionRuntimeGitDiffResponse,
    SessionRuntimeGitRootsResponse,
    SessionRuntimeFilePreviewResponse,
    SessionRuntimeFilesResponse,
    SessionRuntimeResponse,
    SessionResponse,
)
from app.services.agent.agent_modes import (
    get_default_agent_mode,
    parse_agent_mode,
)
from app.services.llm.generic.types import ImageContent, TextContent
from app.services.llm.ids import TierName, parse_tier_name
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions import (
    AgentRuntimeUnavailableError,
    ChatPayloadRequiredError,
    MainSessionDeletionError,
    MainSessionTargetInvalidError,
    MessageNotFoundError,
    RuntimePathInvalidError,
    RuntimePathNotFoundError,
    SessionRenameNotAllowedError,
    SessionNotFoundError,
    SessionService,
)
from app.services.ws.ws_stream_service import run_agent_once

router = APIRouter()

_logger = logging.getLogger(__name__)


def _runtime_provision_tasks(request: Request) -> dict[str, asyncio.Task[None]]:
    tasks = getattr(request.app.state, "runtime_provision_tasks", None)
    if isinstance(tasks, dict):
        return tasks
    tasks = {}
    request.app.state.runtime_provision_tasks = tasks
    return tasks


def _schedule_runtime_provision(request: Request, session_id: UUID) -> None:
    tasks = _runtime_provision_tasks(request)
    key = str(session_id)
    existing = tasks.get(key)
    if existing is not None and not existing.done():
        return
    ws = getattr(request.app.state, "ws_manager", None)
    task = asyncio.create_task(_provision_runtime(session_id, ws_manager=ws))
    tasks[key] = task

    def _cleanup(_task: asyncio.Task[None]) -> None:
        current = tasks.get(key)
        if current is _task:
            tasks.pop(key, None)

    task.add_done_callback(_cleanup)


async def _provision_runtime(session_id: UUID, ws_manager: object | None = None) -> None:
    """Eagerly provision the runtime container so it's ready when the user needs it."""
    try:
        from app.services.runtime import get_runtime
        provider = get_runtime()
        await provider.ensure(session_id)
        _logger.info("Runtime provisioned for session %s", session_id)
        if ws_manager is not None and hasattr(ws_manager, "broadcast_runtime_ready"):
            await ws_manager.broadcast_runtime_ready(str(session_id))
    except Exception:
        _logger.warning("Background runtime provisioning failed for session %s", session_id, exc_info=True)


def _resolve_session_service(request: Request) -> SessionService:
    run_registry = getattr(request.app.state, "agent_run_registry", None)
    if not isinstance(run_registry, AgentRunRegistry):
        run_registry = AgentRunRegistry()
        request.app.state.agent_run_registry = run_registry
    return SessionService(
        run_registry=run_registry,
        agent_runtime_support=getattr(request.app.state, "agent_runtime_support", None),
        db_factory=getattr(request.app.state, "db_factory", AsyncSessionLocal),
    )


def _resolve_run_registry(request: Request) -> AgentRunRegistry:
    run_registry = getattr(request.app.state, "agent_run_registry", None)
    if not isinstance(run_registry, AgentRunRegistry):
        run_registry = AgentRunRegistry()
        request.app.state.agent_run_registry = run_registry
    return run_registry


def _raise_http_for_session_error(exc: Exception) -> None:
    if isinstance(exc, SessionNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        ) from exc
    if isinstance(exc, MessageNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        ) from exc
    if isinstance(exc, MainSessionDeletionError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Main session cannot be deleted",
        ) from exc
    if isinstance(exc, MainSessionTargetInvalidError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "Invalid main session target",
        ) from exc
    if isinstance(exc, SessionRenameNotAllowedError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "Session cannot be renamed",
        ) from exc
    if isinstance(exc, AgentRuntimeUnavailableError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No LLM provider configured",
        ) from exc
    if isinstance(exc, ChatPayloadRequiredError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="content or attachments required",
        ) from exc
    if isinstance(exc, RuntimePathInvalidError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "Invalid runtime path",
        ) from exc
    if isinstance(exc, RuntimePathNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc) or "Runtime path not found",
        ) from exc
    raise exc


def _message_retry_attachments(message: Message) -> list[dict[str, Any]]:
    metadata = dict(message.metadata_json or {}) if isinstance(message.metadata_json, dict) else {}
    raw = metadata.get("attachments")
    if not isinstance(raw, list):
        return []
    attachments: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        mime_type = str(item.get("mime_type") or "").strip()
        base64_data = str(item.get("base64") or "").strip()
        if ";base64," in base64_data:
            _, _, base64_data = base64_data.partition(";base64,")
        if not mime_type or not base64_data:
            continue
        filename_raw = item.get("filename")
        filename = filename_raw.strip() if isinstance(filename_raw, str) else None
        size_bytes = item.get("size_bytes")
        attachments.append(
            {
                "mime_type": mime_type,
                "base64": base64_data,
                "filename": filename,
                "size_bytes": size_bytes if isinstance(size_bytes, int) else None,
            }
        )
    return attachments


def _message_retry_payload(
    message: Message,
) -> str | list[TextContent | ImageContent]:
    attachments = _message_retry_attachments(message)
    content = message.content.strip()
    if not attachments:
        return content

    blocks: list[TextContent | ImageContent] = []
    if content:
        blocks.append(TextContent(text=content))
    for item in attachments:
        blocks.append(
            ImageContent(
                media_type=item["mime_type"],
                data=item["base64"],
            )
        )
    return blocks


def _message_retry_tier(message: Message) -> TierName | None:
    metadata = dict(message.metadata_json or {}) if isinstance(message.metadata_json, dict) else {}
    generation = metadata.get("generation")
    if not isinstance(generation, dict):
        return None
    requested_tier = generation.get("requested_tier")
    return parse_tier_name(requested_tier if isinstance(requested_tier, str) else None)


def _message_retry_max_iterations(message: Message) -> int:
    metadata = dict(message.metadata_json or {}) if isinstance(message.metadata_json, dict) else {}
    generation = metadata.get("generation")
    if not isinstance(generation, dict):
        return CHAT_DEFAULT_ITERATIONS
    raw = generation.get("max_iterations")
    if isinstance(raw, int) and raw >= 1:
        return raw
    return CHAT_DEFAULT_ITERATIONS


def _message_retry_agent_mode(message: Message) -> str:
    metadata = dict(message.metadata_json or {}) if isinstance(message.metadata_json, dict) else {}
    parsed = parse_agent_mode(metadata.get("agent_mode"))
    return (parsed or get_default_agent_mode()).value


async def _retry_existing_user_message_run(
    *,
    db_factory: Any,
    session_id: UUID,
    manager: Any,
    run_registry: AgentRunRegistry,
    agent_runtime_support: Any,
    payload: str | list[TextContent | ImageContent],
    tier: TierName | None,
    max_iterations: int,
    agent_mode: str,
) -> None:
    async with db_factory() as db:
        await manager.broadcast_agent_thinking(str(session_id))
        await run_agent_once(
            db=db,
            session_id=session_id,
            session_key=str(session_id),
            manager=manager,
            run_registry=run_registry,
            agent_runtime_support=agent_runtime_support,
            payload=payload,
            tier=tier,
            max_iterations=max_iterations,
            agent_mode=parse_agent_mode(agent_mode) or get_default_agent_mode(),
            persist_user_message=False,
        )


@router.get("")
async def list_sessions(
    request: Request,
    include_sub_agents: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    service = _resolve_session_service(request)
    main_session_id = await service.get_main_session_id(db, user_id=user.sub)
    page = await service.list_sessions(
        db,
        user_id=user.sub,
        include_sub_agents=include_sub_agents,
        limit=limit,
        offset=offset,
    )
    unread_flags = await service.compute_unread_flags(db, page.items)
    items = [
        await _session_list_item_response(
            item, service, main_session_id=main_session_id,
            has_unread=unread_flags.get(item.id, False),
        )
        for item in page.items
    ]
    return SessionListResponse(items=items, total=page.total)


@router.post("")
async def create_session(
    request: Request,
    payload: CreateSessionRequest,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    service = _resolve_session_service(request)
    session = await service.create_session(
        db,
        user_id=user.sub,
        agent_id=user.agent_id,
        title=payload.title,
    )
    main_session_id = await service.get_main_session_id(db, user_id=user.sub)
    return await _session_response(session, service, main_session_id=main_session_id)


@router.get("/default")
async def get_default_session(
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    service = _resolve_session_service(request)
    session = await service.get_default_session(
        db, user_id=user.sub, agent_id=user.agent_id
    )
    return await _session_response(session, service, main_session_id=session.id)


@router.post("/default/reset")
async def reset_default_session(
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    service = _resolve_session_service(request)
    session = await service.reset_default_session(
        db, user_id=user.sub, agent_id=user.agent_id
    )
    return await _session_response(session, service, main_session_id=session.id)


@router.get("/{id}")
async def get_session(
    id: UUID,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    service = _resolve_session_service(request)
    try:
        session = await service.get_session(db, session_id=id, user_id=user.sub)
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    main_session_id = await service.get_main_session_id(db, user_id=user.sub)
    return await _session_response(session, service, main_session_id=main_session_id)


@router.patch("/{id}")
async def update_session(
    id: UUID,
    payload: UpdateSessionRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    service = _resolve_session_service(request)
    try:
        session = await service.rename_session(
            db,
            session_id=id,
            user_id=user.sub,
            title=payload.title,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    main_session_id = await service.get_main_session_id(db, user_id=user.sub)
    return await _session_response(session, service, main_session_id=main_session_id)


@router.post("/{id}/main")
async def set_session_as_main(
    id: UUID,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    service = _resolve_session_service(request)
    try:
        session = await service.set_main_session(
            db,
            session_id=id,
            user_id=user.sub,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return await _session_response(session, service, main_session_id=session.id)


@router.get("/{id}/runtime")
async def get_session_runtime(
    id: UUID,
    request: Request,
    action_limit: int = Query(default=40, ge=1, le=200),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeResponse:
    service = _resolve_session_service(request)
    try:
        snapshot = await service.get_runtime_snapshot(
            db, session_id=id, user_id=user.sub, action_limit=action_limit
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return SessionRuntimeResponse(**snapshot)


@router.get("/{id}/runtime/files", response_model=SessionRuntimeFilesResponse)
async def list_session_runtime_files(
    id: UUID,
    request: Request,
    path: str = Query(default=""),
    limit: int = Query(default=400, ge=1, le=2000),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeFilesResponse:
    service = _resolve_session_service(request)
    try:
        payload = await service.list_runtime_files(
            db,
            session_id=id,
            user_id=user.sub,
            path=path,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return SessionRuntimeFilesResponse(**payload)


@router.get("/{id}/runtime/file", response_model=SessionRuntimeFilePreviewResponse)
async def get_session_runtime_file(
    id: UUID,
    request: Request,
    path: str = Query(..., min_length=1),
    max_bytes: int = Query(default=32000, ge=256, le=200000),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeFilePreviewResponse:
    service = _resolve_session_service(request)
    try:
        payload = await service.get_runtime_file_preview(
            db,
            session_id=id,
            user_id=user.sub,
            path=path,
            max_bytes=max_bytes,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return SessionRuntimeFilePreviewResponse(**payload)


@router.get("/{id}/runtime/download")
async def download_session_runtime_path(
    id: UUID,
    request: Request,
    path: str = Query(..., min_length=1),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    service = _resolve_session_service(request)
    try:
        payload = await service.get_runtime_download(
            db,
            session_id=id,
            user_id=user.sub,
            path=path,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return FileResponse(
        path=payload.host_path,
        media_type=payload.media_type,
        filename=payload.download_name,
        background=BackgroundTask(payload.cleanup_path.unlink)
        if payload.cleanup_path is not None
        else None,
    )


@router.get("/{id}/runtime/git/roots", response_model=SessionRuntimeGitRootsResponse)
async def list_session_runtime_git_roots(
    id: UUID,
    request: Request,
    path: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=1000),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeGitRootsResponse:
    service = _resolve_session_service(request)
    try:
        payload = await service.list_runtime_git_roots(
            db,
            session_id=id,
            user_id=user.sub,
            path=path,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return SessionRuntimeGitRootsResponse(**payload)


@router.get("/{id}/runtime/git/diff", response_model=SessionRuntimeGitDiffResponse)
async def get_session_runtime_git_diff(
    id: UUID,
    request: Request,
    path: str = Query(..., min_length=1),
    base_ref: str = Query(default="HEAD"),
    staged: bool = Query(default=False),
    context_lines: int = Query(default=3, ge=0, le=20),
    max_bytes: int = Query(default=120000, ge=1024, le=500000),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeGitDiffResponse:
    service = _resolve_session_service(request)
    try:
        payload = await service.get_runtime_git_diff(
            db,
            session_id=id,
            user_id=user.sub,
            path=path,
            base_ref=base_ref,
            staged=staged,
            context_lines=context_lines,
            max_bytes=max_bytes,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return SessionRuntimeGitDiffResponse(**payload)


@router.get("/{id}/runtime/git/changed", response_model=SessionRuntimeGitChangedFilesResponse)
async def list_session_runtime_git_changed_files(
    id: UUID,
    request: Request,
    path: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=1000),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeGitChangedFilesResponse:
    service = _resolve_session_service(request)
    try:
        payload = await service.get_runtime_git_changed_files(
            db,
            session_id=id,
            user_id=user.sub,
            path=path,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return SessionRuntimeGitChangedFilesResponse(**payload)


@router.get("/{id}/context-usage", response_model=SessionContextUsageResponse)
async def get_session_context_usage(
    id: UUID,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionContextUsageResponse:
    service = _resolve_session_service(request)
    try:
        usage = await service.get_context_usage(
            db, session_id=id, user_id=user.sub
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return SessionContextUsageResponse(**usage)


@router.post("/{id}/runtime/cleanup")
async def cleanup_runtime(
    id: UUID,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeCleanupResponse:
    service = _resolve_session_service(request)
    try:
        session_id, result = await service.cleanup_runtime(
            db, session_id=id, user_id=user.sub
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return SessionRuntimeCleanupResponse(session_id=session_id, **result)


_TERMINAL_ID_HTTP_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


@router.delete("/{id}/terminals/{terminal_id}")
async def close_terminal(
    id: UUID,
    terminal_id: str,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not _TERMINAL_ID_HTTP_PATTERN.match(terminal_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="terminal_id must match [a-zA-Z0-9_-]{1,32}",
        )
    if terminal_id == "0":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Terminal '0' is the primary shell and cannot be closed.",
        )
    service = _resolve_session_service(request)
    try:
        session_id, tid, existed = await service.close_terminal(
            db,
            session_id=id,
            terminal_id=terminal_id,
            user_id=user.sub,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return {
        "session_id": str(session_id),
        "terminal_id": tid,
        "closed": existed,
    }


@router.delete("/{id}")
async def delete_session(
    id: UUID,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    service = _resolve_session_service(request)
    try:
        deleted_descendants = await service.delete_session(
            db, session_id=id, user_id=user.sub
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return {"status": "deleted", "deleted_descendants": deleted_descendants}


@router.post("/{id}/stop")
async def stop_session_generation(
    id: UUID,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    service = _resolve_session_service(request)
    try:
        cancelled = await service.stop_generation(db, session_id=id, user_id=user.sub)
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return {"status": "stopping" if cancelled else "idle"}


@router.post("/{id}/read")
async def mark_session_read(
    id: UUID,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    service = _resolve_session_service(request)
    try:
        await service.mark_as_read(db, session_id=id, user_id=user.sub)
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return {"status": "ok"}


@router.post("/{id}/messages")
async def create_message(
    id: UUID,
    request: Request,
    payload: CreateMessageRequest,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    service = _resolve_session_service(request)
    try:
        message = await service.create_message(
            db,
            session_id=id,
            user_id=user.sub,
            role=payload.role,
            content=payload.content,
            metadata=payload.metadata,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return _message_response(message)


@router.post("/{id}/messages/{message_id}/retry")
async def retry_message(
    id: UUID,
    message_id: UUID,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id=user.sub)
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise

    result = await db.execute(
        select(Message).where(
            Message.id == message_id,
            Message.session_id == id,
        )
    )
    message = result.scalars().first()
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )
    if message.role != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only user messages can be retried",
        )

    manager = getattr(request.app.state, "ws_manager", None)
    agent_runtime_support = getattr(request.app.state, "agent_runtime_support", None)
    if manager is None or agent_runtime_support is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent runtime unavailable",
        )

    run_registry = _resolve_run_registry(request)
    session_key = str(id)
    if await run_registry.is_running(session_key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent is already processing this session",
        )

    metadata = dict(message.metadata_json or {}) if isinstance(message.metadata_json, dict) else {}
    if "retryable_error" in metadata:
        metadata.pop("retryable_error", None)
        message.metadata_json = metadata
        await db.commit()

    db_factory = getattr(request.app.state, "db_factory", AsyncSessionLocal)
    asyncio.create_task(
        _retry_existing_user_message_run(
            db_factory=db_factory,
            session_id=id,
            manager=manager,
            run_registry=run_registry,
            agent_runtime_support=agent_runtime_support,
            payload=_message_retry_payload(message),
            tier=_message_retry_tier(message),
            max_iterations=_message_retry_max_iterations(message),
            agent_mode=_message_retry_agent_mode(message),
        )
    )
    return {"status": "retrying"}


@router.get("/{id}/messages")
async def list_messages(
    id: UUID,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    before: UUID | None = Query(default=None),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageListResponse:
    service = _resolve_session_service(request)
    try:
        page = await service.list_messages(
            db,
            session_id=id,
            user_id=user.sub,
            limit=limit,
            before=before,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return MessageListResponse(
        items=[_message_response(item) for item in page.items],
        has_more=page.has_more,
    )


@router.post("/{id}/chat", response_model=ChatResponse)
async def chat_session(
    id: UUID,
    payload: ChatRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    service = _resolve_session_service(request)
    try:
        result = await service.run_chat(
            db,
            session_id=id,
            user_id=user.sub,
            content=payload.content,
            attachments=payload.attachments,
            tier=payload.tier,
            agent_mode=payload.agent_mode,
            system_prompt=payload.system_prompt,
            temperature=payload.temperature,
            max_iterations=payload.max_iterations,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return ChatResponse(
        response=result.final_text,
        iterations=result.iterations,
        usage={
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        },
        error=result.error,
    )


async def _session_response(
    session: Session,
    service: SessionService,
    *,
    main_session_id: UUID | None = None,
    has_unread: bool = False,
) -> SessionResponse:
    is_running = await service.is_session_running(session.id)
    return SessionResponse(
        id=session.id,
        user_id=session.user_id,
        agent_id=session.agent_id,
        parent_session_id=session.parent_session_id,
        title=session.title,
        initial_prompt=session.initial_prompt,
        latest_system_prompt=session.latest_system_prompt,
        started_at=session.started_at,
        is_running=is_running,
        is_main=bool(main_session_id and session.id == main_session_id),
        has_unread=has_unread,
    )


async def _session_list_item_response(
    session: Session,
    service: SessionService,
    *,
    main_session_id: UUID | None = None,
    has_unread: bool = False,
) -> SessionListItemResponse:
    is_running = await service.is_session_running(session.id)
    return SessionListItemResponse(
        id=session.id,
        user_id=session.user_id,
        agent_id=session.agent_id,
        parent_session_id=session.parent_session_id,
        title=session.title,
        started_at=session.started_at,
        is_running=is_running,
        is_main=bool(main_session_id and session.id == main_session_id),
        has_unread=has_unread,
    )


def _message_response(message: Message) -> MessageResponse:
    metadata = dict(message.metadata_json or {})
    runtime_context_structured: dict | None = None
    if metadata.get("source") == "runtime_context":
        run_context = metadata.get("run_context")
        if isinstance(run_context, dict):
            candidate = run_context.get("structured_context")
            if isinstance(candidate, dict):
                runtime_context_structured = candidate

    return MessageResponse(
        id=message.id,
        session_id=message.session_id,
        role=message.role,
        content=message.content,
        metadata=metadata,
        token_count=message.token_count,
        tool_call_id=message.tool_call_id,
        tool_name=message.tool_name,
        runtime_context_structured=runtime_context_structured,
        created_at=message.created_at,
    )
