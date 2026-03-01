from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

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
    SessionListResponse,
    SessionContextUsageResponse,
    SessionRuntimeCleanupResponse,
    SessionRuntimeResponse,
    SessionResponse,
)
from app.services.agent_run_registry import AgentRunRegistry
from app.services.sessions import (
    AgentLoopUnavailableError,
    ChatPayloadRequiredError,
    MainSessionDeletionError,
    MainSessionTargetInvalidError,
    MessageNotFoundError,
    SessionNotFoundError,
    SessionService,
)

router = APIRouter()


def _resolve_session_service(request: Request) -> SessionService:
    run_registry = getattr(request.app.state, "agent_run_registry", None)
    if not isinstance(run_registry, AgentRunRegistry):
        run_registry = AgentRunRegistry()
        request.app.state.agent_run_registry = run_registry
    return SessionService(
        run_registry=run_registry,
        agent_loop=getattr(request.app.state, "agent_loop", None),
        db_factory=getattr(request.app.state, "db_factory", AsyncSessionLocal),
    )


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
    if isinstance(exc, AgentLoopUnavailableError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No LLM provider configured",
        ) from exc
    if isinstance(exc, ChatPayloadRequiredError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="content or attachments required",
        ) from exc
    raise exc


@router.get("/")
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
    items = [
        await _session_response(item, service, main_session_id=main_session_id)
        for item in page.items
    ]
    return SessionListResponse(items=items, total=page.total)


@router.post("/")
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
