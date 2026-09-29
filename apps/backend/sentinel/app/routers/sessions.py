from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal
from uuid import UUID

import httpx
import websockets
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CHAT_DEFAULT_ITERATIONS
from app.dependencies import (
    get_db,
    get_request_db_factory,
    get_request_instance_runtime_context,
    get_request_run_registry,
    get_request_session_service as _resolve_session_service,
)
from app.models import Message, Session
from app.schemas.runtime import (
    SessionRuntimeFilePreviewResponse,
    SessionRuntimeFilesResponse,
    SessionRuntimeGitChangedFilesResponse,
    SessionRuntimeGitDiffResponse,
    SessionRuntimeGitRootsResponse,
)
from app.schemas.sessions import (
    ChatRequest,
    ChatResponse,
    CreateMessageRequest,
    CreateSessionRequest,
    MessageListResponse,
    MessageResponse,
    RetryMessageRequest,
    SessionContextUsageResponse,
    SessionListItemResponse,
    SessionListResponse,
    SessionResponse,
    SteeringRequest,
    UpdateSessionRequest,
)
from app.services.agent.agent_modes import (
    get_default_agent_mode,
    parse_agent_mode,
)
from sentral.llm.generic.types import AssistantMessage, ImageContent, TextContent, UserMessage
from sentral.llm.ids import TierName, parse_tier_name
from app.services.llm.session_selection import selection_model
from app.services.modules.builtins.form.contract import pending_form_sessions
from app.services.runtime.file_stream import stream_response
from app.services.runtime.files import (
    RuntimePathInvalidError,
    RuntimePathIsDirectoryError,
    RuntimePathNotFoundError,
)
from app.services.runtime.machines import InstanceRuntimeNotConfigured
from app.services.runtime.panes import TmuxPanes
from app.services.runtime.port_forwards import RuntimeForwardNotFound
from app.services.runtime.ssh_runtime import (
    get_runtime_port_forward_manager,
    get_runtime_terminal_manager,
    get_runtime_workspace_files,
)
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.attention import pending_approval_sessions
from app.services.sessions.errors import (
    AgentRuntimeUnavailableError,
    ChatPayloadRequiredError,
    MessageNotFoundError,
    SessionNotFoundError,
    SessionRenameNotAllowedError,
    SessionWorkspaceCleanupError,
    SteeringConflictError,
    SteeringValidationError,
)
from app.services.sessions.service import SessionService
from app.services.sessions.steering import enqueue_session_steering
from app.services.ws.ws_stream_service import run_agent_once

router = APIRouter()

_logger = logging.getLogger(__name__)
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_SENTINEL_PRIVATE_HEADERS = {"authorization", "cookie", "host", "content-length"}


def _raise_http_for_session_error(exc: Exception) -> None:
    if isinstance(exc, SteeringValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, SteeringConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, SessionNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        ) from exc
    if isinstance(exc, MessageNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        ) from exc
    if isinstance(exc, SessionRenameNotAllowedError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "Session cannot be renamed",
        ) from exc
    if isinstance(exc, AgentRuntimeUnavailableError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc) or "No LLM provider configured",
        ) from exc
    if isinstance(exc, SessionWorkspaceCleanupError):
        detail = "Machine workspace cleanup failed; session was not deleted."
        if exc.detail:
            detail = f"{detail} {exc.detail}"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        ) from exc
    if isinstance(exc, ChatPayloadRequiredError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="content or attachments required",
        ) from exc
    raise exc


def _raise_http_for_runtime_path_error(exc: Exception) -> None:
    if isinstance(exc, RuntimePathNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc) or "Machine path not found",
        ) from exc
    if isinstance(exc, (RuntimePathInvalidError, RuntimePathIsDirectoryError)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "Invalid runtime path",
        ) from exc
    raise exc


def _raise_http_for_session_or_runtime_error(exc: Exception) -> None:

    if isinstance(exc, InstanceRuntimeNotConfigured):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(
        exc,
        (
            RuntimePathNotFoundError,
            RuntimePathInvalidError,
            RuntimePathIsDirectoryError,
        ),
    ):
        _raise_http_for_runtime_path_error(exc)
        return
    _raise_http_for_session_error(exc)


def _proxy_request_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        normalized = key.lower()
        if normalized in _HOP_BY_HOP_HEADERS or normalized in _SENTINEL_PRIVATE_HEADERS:
            continue
        headers[key] = value
    return headers


def _proxy_response_headers(response: httpx.Response) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in response.headers.items():
        normalized = key.lower()
        if normalized in _HOP_BY_HOP_HEADERS or normalized in {"content-length"}:
            continue
        headers[key] = value
    return headers


def _forward_query_without_auth(websocket: WebSocket) -> str:
    pairs = [
        (key, value)
        for key, value in websocket.query_params.multi_items()
        if key.lower() != "token"
    ]
    return str(httpx.QueryParams(pairs))


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
    if isinstance(raw, int) and raw >= 0:
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
    provider_id: str | None = None,
    reasoning_level: str | None = None,
    fast_mode: bool = False,
    message_id: UUID | None = None,
) -> None:
    async with db_factory() as db:
        message = None
        if message_id is not None:
            result = await db.execute(
                select(Message).where(Message.id == message_id, Message.session_id == session_id)
            )
            message = result.scalars().first()
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
            provider_id=provider_id,
            reasoning_level=reasoning_level,
            fast_mode=fast_mode,
            max_iterations=max_iterations,
            agent_mode=parse_agent_mode(agent_mode) or get_default_agent_mode(),
            persist_user_message=False,
            user_message=message,
        )


@router.get("")
async def list_sessions(
    request: Request,
    include_sub_agents: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    service = _resolve_session_service(request)
    page = await service.list_sessions(
        db,
        user_id="local",
        include_sub_agents=include_sub_agents,
        limit=limit,
        offset=offset,
    )

    waiting = await pending_form_sessions(db, [item.id for item in page.items])

    approvals = await pending_approval_sessions(db, [item.id for item in page.items])
    completed = await service.completion_details(db, page.items)
    completions = {session_id: str(value[0]) for session_id, value in completed.items()}
    # Remaining work reads detached records and in-memory run state, not SQL.
    # Polling must not retain a pool connection while waiting on coordination.
    await db.close()
    unread_flags = await service.compute_unread_flags(
        db,
        page.items,
        latest_by_session={session_id: value[1] for session_id, value in completed.items()},
    )
    items = [
        await _session_list_item_response(
            item,
            service,
            has_unread=unread_flags.get(item.id, False),
            pending_form_id=waiting.get(item.id),
            awaiting_approval=item.id in approvals,
            completion_id=completions.get(item.id),
        )
        for item in page.items
    ]
    return SessionListResponse(items=items, total=page.total)


@router.post("")
async def create_session(
    request: Request,
    payload: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    service = _resolve_session_service(request)
    session = await service.create_session(
        db,
        user_id="local",
        agent_id=None,
        title=payload.title,
    )
    return await _session_response(session, service)


@router.post("/{id:uuid}/fork")
async def fork_session(
    id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    service = _resolve_session_service(request)
    try:
        session = await service.fork_session(db, session_id=id, user_id="local")
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return await _session_response(session, service)


@router.get("/{id:uuid}")
async def get_session(
    id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    service = _resolve_session_service(request)
    try:
        session = await service.get_session(db, session_id=id, user_id="local")
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return await _session_response(session, service)


@router.patch("/{id:uuid}")
async def update_session(
    id: UUID,
    payload: UpdateSessionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    service = _resolve_session_service(request)
    try:
        session = await service.rename_session(
            db,
            session_id=id,
            user_id="local",
            title=payload.title,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return await _session_response(session, service)


@router.get("/{id:uuid}/context-usage", response_model=SessionContextUsageResponse)
async def get_session_context_usage(
    id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SessionContextUsageResponse:
    service = _resolve_session_service(request)
    try:
        usage = await service.get_context_usage(db, session_id=id, user_id="local")
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return SessionContextUsageResponse(**usage)


@router.get("/{id:uuid}/usage")
async def get_session_usage(id: UUID, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    service = _resolve_session_service(request)
    try:
        return await service.get_usage(db, session_id=id, user_id="local")
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise


@router.post("/{id:uuid}/model-context")
async def check_model_context(
    id: UUID, payload: ChatRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    service = _resolve_session_service(request)
    await service.get_session(db, session_id=id, user_id="local")
    support = get_request_instance_runtime_context(request).agent_runtime_support
    if support is None or support.provider is None:
        raise HTTPException(409, "Configure a model provider before switching models.")

    tier = selection_model(
        payload.tier, payload.provider_id, payload.reasoning_level, payload.fast_mode
    )
    try:
        limits = support.provider.model_context(tier)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if limits["context_token_budget"] is None:
        raise HTTPException(409, "The selected model's context limit is unknown.")
    messages = await support.context_builder.build(
        db,
        id,
        system_prompt=payload.system_prompt,
        pending_user_message=payload.content,
        agent_mode=payload.agent_mode,
        include_full_history=True,
    )
    content = [TextContent(text=payload.content)] if payload.content else []
    content.extend(
        ImageContent(media_type=item.mime_type, data=item.base64) for item in payload.attachments
    )
    if content:
        messages.append(UserMessage(content=content))
    if not any(isinstance(item, (UserMessage, AssistantMessage)) for item in messages):
        # Nothing has been said yet, so no conversation can overflow the new model.
        return {**limits, "input_tokens": 0, "count_source": "empty", "requires_compaction": False}
    tools = support.tool_registry.list_schemas()
    count = None
    try:
        async with asyncio.timeout(15):
            count = await support.provider.count_input_tokens(messages, tier, tools)
    except (httpx.HTTPError, TimeoutError, KeyError, ValueError):
        _logger.info("Provider token preflight unavailable for %s", tier)
    source = "provider_count" if count is not None else "unavailable"
    return {
        **limits,
        "input_tokens": count,
        "count_source": source,
        "requires_compaction": (
            count > limits["context_token_budget"] if count is not None else None
        ),
    }


@router.get("/{id:uuid}/runtime/files", response_model=SessionRuntimeFilesResponse)
async def list_session_runtime_files(
    id: UUID,
    request: Request,
    path: str = Query(default=""),
    limit: int = Query(default=400, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeFilesResponse:
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id="local")
        await db.close()
        files = await get_runtime_workspace_files(
            session_id=id, instance_name=_request_instance_name(request)
        )
        payload = await files.list_files(str(id), path=path, limit=limit)
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_or_runtime_error(exc)
        raise
    return SessionRuntimeFilesResponse(**payload)


@router.get("/{id:uuid}/runtime/file", response_model=SessionRuntimeFilePreviewResponse)
async def get_session_runtime_file(
    id: UUID,
    request: Request,
    path: str = Query(..., min_length=1),
    max_bytes: int = Query(default=32000, ge=256, le=200000),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeFilePreviewResponse:
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id="local")
        await db.close()
        files = await get_runtime_workspace_files(
            session_id=id, instance_name=_request_instance_name(request)
        )
        payload = await files.preview_file(
            str(id),
            path=path,
            max_bytes=max_bytes,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_or_runtime_error(exc)
        raise
    return SessionRuntimeFilePreviewResponse(**payload)


@router.get("/{id:uuid}/runtime/download")
@router.head("/{id:uuid}/runtime/download", include_in_schema=False)
async def download_session_runtime_path(
    id: UUID,
    request: Request,
    path: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id="local")
        await db.close()
        files = await get_runtime_workspace_files(
            session_id=id, instance_name=_request_instance_name(request)
        )
        payload = await files.download(
            str(id),
            path=path,
            range_header=request.headers.get("range"),
            if_range=request.headers.get("if-range"),
            head=request.method == "HEAD",
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_or_runtime_error(exc)
        raise
    return await stream_response(payload, head=request.method == "HEAD", download=True)


@router.get("/{id:uuid}/runtime/forward-target/{forward_id}")
async def get_runtime_forward_target(
    id: UUID,
    forward_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Resolve an existing forward for the desktop's isolated preview window."""
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id="local")
        await db.close()
    except Exception as exc:
        _raise_http_for_session_error(exc)
        raise
    forwards = await get_runtime_port_forward_manager(
        session_id=id, instance_name=_request_instance_name(request)
    )
    try:
        forward = await forwards.get_forward(session_id=str(id), forward_id=forward_id)
    except RuntimeForwardNotFound as exc:
        raise HTTPException(status_code=404, detail="Workspace port forward not found") from exc
    return {
        "url": f"http://127.0.0.1:{forward.local_port}/",
        "label": forward.label or "Workspace preview",
    }


@router.api_route(
    "/{id:uuid}/runtime/forwards/{forward_id}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
@router.api_route(
    "/{id:uuid}/runtime/forwards/{forward_id}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_runtime_forward_http(
    id: UUID,
    forward_id: str,
    request: Request,
    path: str = "",
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id="local")
        await db.close()
        forwards = await get_runtime_port_forward_manager(
            session_id=id, instance_name=_request_instance_name(request)
        )
        forward = await forwards.get_forward(
            session_id=str(id),
            forward_id=forward_id,
        )
    except RuntimeForwardNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace port forward not found",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise

    suffix = path.strip("/")
    target_url = f"http://{forward.local_host}:{forward.local_port}/"
    if suffix:
        target_url += suffix
    if request.url.query:
        target_url += f"?{request.url.query}"

    async with httpx.AsyncClient(follow_redirects=False, timeout=60.0) as client:
        proxied = await client.request(
            request.method,
            target_url,
            headers=_proxy_request_headers(request),
            content=await request.body(),
        )
    return Response(
        content=proxied.content,
        status_code=proxied.status_code,
        headers=_proxy_response_headers(proxied),
        media_type=proxied.headers.get("content-type"),
    )


@router.websocket("/{id:uuid}/runtime/forwards/{forward_id}")
@router.websocket("/{id:uuid}/runtime/forwards/{forward_id}/{path:path}")
async def proxy_runtime_forward_websocket(
    websocket: WebSocket,
    id: UUID,
    forward_id: str,
    path: str = "",
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Session.id).where(Session.id == id))
    exists = result.scalar_one_or_none() is not None
    await db.close()
    if not exists:
        await websocket.close(code=4004, reason="Session not found")
        return
    try:
        forwards = await get_runtime_port_forward_manager(
            session_id=id, instance_name=str(websocket.path_params["instance_name"])
        )
        forward = await forwards.get_forward(
            session_id=str(id),
            forward_id=forward_id,
        )
    except RuntimeForwardNotFound:
        await websocket.close(code=4004, reason="Workspace port forward not found")
        return

    suffix = path.strip("/")
    target_url = f"ws://{forward.local_host}:{forward.local_port}/"
    if suffix:
        target_url += suffix
    query = _forward_query_without_auth(websocket)
    if query:
        target_url += f"?{query}"

    await websocket.accept()
    try:
        async with websockets.connect(target_url, proxy=None) as remote:

            async def _client_to_remote() -> None:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
                    if "text" in message and message["text"] is not None:
                        await remote.send(message["text"])
                    elif "bytes" in message and message["bytes"] is not None:
                        await remote.send(message["bytes"])

            async def _remote_to_client() -> None:
                async for message in remote:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            done, pending = await asyncio.wait(
                {
                    asyncio.create_task(_client_to_remote()),
                    asyncio.create_task(_remote_to_client()),
                },
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
    except WebSocketDisconnect:
        return
    except Exception:
        _logger.warning("runtime forward websocket proxy failed", exc_info=True)
        try:
            await websocket.close(code=4005, reason="Workspace port forward unavailable")
        except Exception:
            return


@router.get("/{id:uuid}/runtime/git/roots", response_model=SessionRuntimeGitRootsResponse)
async def list_session_runtime_git_roots(
    id: UUID,
    request: Request,
    path: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeGitRootsResponse:
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id="local")
        await db.close()
        files = await get_runtime_workspace_files(
            session_id=id, instance_name=_request_instance_name(request)
        )
        payload = await files.git_roots(str(id), path=path, limit=limit)
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_or_runtime_error(exc)
        raise
    return SessionRuntimeGitRootsResponse(**payload)


@router.get(
    "/{id:uuid}/runtime/git/changed",
    response_model=SessionRuntimeGitChangedFilesResponse,
)
async def list_session_runtime_git_changed_files(
    id: UUID,
    request: Request,
    path: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeGitChangedFilesResponse:
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id="local")
        await db.close()
        files = await get_runtime_workspace_files(
            session_id=id, instance_name=_request_instance_name(request)
        )
        payload = await files.git_changed(str(id), path=path, limit=limit)
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_or_runtime_error(exc)
        raise
    return SessionRuntimeGitChangedFilesResponse(**payload)


@router.get("/{id:uuid}/runtime/git/diff", response_model=SessionRuntimeGitDiffResponse)
async def get_session_runtime_git_diff(
    id: UUID,
    request: Request,
    path: str = Query(..., min_length=1),
    base_ref: str = Query(default="HEAD"),
    staged: bool = Query(default=False),
    context_lines: int = Query(default=3, ge=0, le=20),
    max_bytes: int = Query(default=120000, ge=1024, le=500000),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeGitDiffResponse:
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id="local")
        await db.close()
        files = await get_runtime_workspace_files(
            session_id=id, instance_name=_request_instance_name(request)
        )
        payload = await files.git_diff(
            str(id),
            path=path,
            base_ref=base_ref,
            staged=staged,
            context_lines=context_lines,
            max_bytes=max_bytes,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_or_runtime_error(exc)
        raise
    return SessionRuntimeGitDiffResponse(**payload)


@router.delete("/{id:uuid}/panes/{pane_id}")
async def close_pane(
    id: UUID, pane_id: str, request: Request, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:

    service = _resolve_session_service(request)
    await service.get_session(db, session_id=id, user_id="local")
    await db.close()
    terminal = await get_runtime_terminal_manager(
        session_id=id, instance_name=_request_instance_name(request)
    )
    bridge = TmuxPanes(terminal)
    try:
        await bridge.close_pane(str(id), pane_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    manager = getattr(request.app.state, "ws_manager", None)
    if manager:
        await manager.broadcast(
            str(id),
            {
                "type": "panes_changed",
                "panes": [p for w in await bridge.tree(str(id)) for p in w["panes"]],
            },
        )
    return {"pane_id": pane_id, "closed": True}


@router.delete("/{id:uuid}")
async def delete_session(
    id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    service = _resolve_session_service(request)
    try:
        deleted_descendants = await service.delete_session(
            db,
            session_id=id,
            user_id="local",
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return {"status": "deleted", "deleted_descendants": deleted_descendants}


@router.post("/{id:uuid}/close")
async def close_session(
    id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    service = _resolve_session_service(request)
    deleted = await service.discard_empty_session(
        db,
        session_id=id,
        user_id="local",
    )
    return {"status": "discarded" if deleted else "kept"}


@router.post("/{id:uuid}/stop")
async def stop_session_generation(
    id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    service = _resolve_session_service(request)
    try:
        cancelled = await service.stop_session(db, session_id=id, user_id="local")
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return {"status": "stopping" if cancelled else "idle"}


@router.post("/{id:uuid}/read")
async def mark_session_read(
    id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    service = _resolve_session_service(request)
    try:
        await service.mark_as_read(db, session_id=id, user_id="local")
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return {"status": "ok"}


@router.post("/{id:uuid}/messages")
async def create_message(
    id: UUID,
    request: Request,
    payload: CreateMessageRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    service = _resolve_session_service(request)
    try:
        message = await service.create_message(
            db,
            session_id=id,
            user_id="local",
            role=payload.role,
            content=payload.content,
            metadata=payload.metadata,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return _message_response(message)


@router.post("/{id:uuid}/messages/{message_id:uuid}/retry")
async def retry_message(
    id: UUID,
    message_id: UUID,
    request: Request,
    payload: RetryMessageRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id="local")
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
    try:
        agent_runtime_support = get_request_instance_runtime_context(request).agent_runtime_support
    except RuntimeError:
        agent_runtime_support = None
    if manager is None or agent_runtime_support is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent runtime unavailable",
        )

    run_registry = get_request_run_registry(request)
    session_key = str(id)
    if await run_registry.is_running(session_key):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent is already processing this session",
        )

    metadata = dict(message.metadata_json or {}) if isinstance(message.metadata_json, dict) else {}
    selection = (
        metadata.get("model_selection")
        or (metadata.get("generation") or {}).get("model_selection")
        or {}
    )
    retry_settings = {
        "tier": _message_retry_tier(message),
        "provider_id": selection.get("provider_id"),
        "reasoning_level": selection.get("reasoning_level"),
        "fast_mode": selection.get("fast_mode", False),
        "max_iterations": _message_retry_max_iterations(message),
        "agent_mode": _message_retry_agent_mode(message),
        **(metadata.get("retry_settings") or {}),
    }
    if payload is not None and payload.model_fields_set:
        retry_settings.update(payload.model_dump(mode="json", exclude_unset=True))
        # Validate against the current runtime before clearing the failure or
        # scheduling work. Explicit null clears a previous provider/effort pin.
        try:
            agent_runtime_support.provider.model_context(
                selection_model(
                    retry_settings["tier"],
                    retry_settings["provider_id"],
                    retry_settings["reasoning_level"],
                    retry_settings["fast_mode"],
                )
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Keep the failure durable until the retry actually succeeds.
    metadata["retry_settings"] = retry_settings
    message.metadata_json = metadata
    await db.commit()

    db_factory = get_request_db_factory(request)
    asyncio.create_task(
        _retry_existing_user_message_run(
            db_factory=db_factory,
            session_id=id,
            manager=manager,
            run_registry=run_registry,
            agent_runtime_support=agent_runtime_support,
            payload=_message_retry_payload(message),
            message_id=message.id,
            **retry_settings,
        )
    )
    return {"status": "retrying"}


@router.get("/{id:uuid}/messages")
async def list_messages(
    id: UUID,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    before: UUID | None = Query(default=None),
    final_only: bool = Query(default=False),
    view: Literal["full", "chat"] = Query(default="full"),
    db: AsyncSession = Depends(get_db),
) -> MessageListResponse:
    service = _resolve_session_service(request)
    try:
        page = await service.list_messages(
            db,
            session_id=id,
            user_id="local",
            limit=limit,
            before=before,
            final_only=final_only,
            chat_view=view == "chat",
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return MessageListResponse(
        items=[_message_response(item) for item in page.items],
        has_more=page.has_more,
    )


@router.post("/{id:uuid}/steer", response_model=MessageResponse)
async def steer_session(
    id: UUID,
    payload: SteeringRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    support = get_request_instance_runtime_context(request).agent_runtime_support
    try:
        message = await enqueue_session_steering(
            db,
            session_id=id,
            user_id="local",
            payload=payload,
            sessions=_resolve_session_service(request),
            provider=support.provider if support else None,
            registry=get_request_run_registry(request),
            manager=getattr(request.app.state, "ws_manager", None),
        )
    except Exception as exc:
        _raise_http_for_session_error(exc)
        raise
    return _message_response(message)


@router.post("/{id:uuid}/chat", response_model=ChatResponse)
async def chat_session(
    id: UUID,
    payload: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    service = _resolve_session_service(request)
    try:
        result = await service.run_chat(
            db,
            session_id=id,
            user_id="local",
            content=payload.content,
            attachments=payload.attachments,
            tier=payload.tier,
            provider_id=payload.provider_id,
            reasoning_level=payload.reasoning_level,
            fast_mode=payload.fast_mode,
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
    has_unread: bool = False,
) -> SessionResponse:
    is_running = await service.is_session_running(session.id)
    return SessionResponse(
        workspace_id=session.workspace_id,
        id=session.id,
        user_id=session.user_id,
        agent_id=session.agent_id,
        parent_session_id=session.parent_session_id,
        title=session.title,
        initial_prompt=session.initial_prompt,
        latest_system_prompt=session.latest_system_prompt,
        started_at=session.started_at,
        is_running=is_running,
        has_unread=has_unread,
    )


async def _session_list_item_response(
    session: Session,
    service: SessionService,
    *,
    has_unread: bool = False,
    pending_form_id: str | None = None,
    awaiting_approval: bool = False,
    completion_id: str | None = None,
) -> SessionListItemResponse:
    is_running = await service.is_session_running(session.id)
    return SessionListItemResponse(
        workspace_id=session.workspace_id,
        id=session.id,
        user_id=session.user_id,
        agent_id=session.agent_id,
        parent_session_id=session.parent_session_id,
        title=session.title,
        started_at=session.started_at,
        is_running=is_running,
        has_unread=has_unread,
        awaiting_input=pending_form_id is not None or awaiting_approval,
        pending_form_id=pending_form_id,
        completion_id=completion_id,
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


def _request_instance_name(request: Request) -> str:
    return str(
        getattr(request.state, "instance_name", request.path_params.get("instance_name", ""))
    )
