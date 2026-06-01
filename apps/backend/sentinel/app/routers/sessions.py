from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
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
from app.database import ManagerSessionLocal
from app.dependencies import get_db, get_request_db_factory, get_request_instance_runtime_context
from app.middleware.auth import (
    ACCESS_TOKEN_COOKIE_NAME,
    TokenPayload,
    decode_and_validate_token,
    require_auth,
)
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
    MessageNotFoundError,
    SessionRenameNotAllowedError,
    SessionNotFoundError,
    SessionService,
    SessionWorkspaceCleanupError,
)
from app.schemas.runtime import (
    SessionRuntimeFilePreviewResponse,
    SessionRuntimeFilesResponse,
    SessionRuntimeGitChangedFilesResponse,
    SessionRuntimeGitDiffResponse,
    SessionRuntimeGitRootsResponse,
)
from app.services.runtime.files import (
    RuntimePathInvalidError,
    RuntimePathIsDirectoryError,
    RuntimePathNotFoundError,
)
from app.services.araios.runtime_services import get_browser_pool
from app.services.runtime.ssh_runtime import (
    get_runtime_desktop_manager,
    get_runtime_terminal_manager,
    get_runtime_workspace_files,
)
from app.services.runtime.port_forwards import RuntimeForwardNotFound
from app.services.runtime.ssh_runtime import get_runtime_port_forward_manager
from app.services.ws.ws_stream_service import run_agent_once

router = APIRouter()

_logger = logging.getLogger(__name__)
_TERMINAL_ID_HTTP_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
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


def _resolve_session_service(request: Request) -> SessionService:
    run_registry = getattr(request.app.state, "agent_run_registry", None)
    if not isinstance(run_registry, AgentRunRegistry):
        run_registry = AgentRunRegistry()
        request.app.state.agent_run_registry = run_registry
    try:
        agent_runtime_support = get_request_instance_runtime_context(request).agent_runtime_support
    except RuntimeError:
        agent_runtime_support = None
    return SessionService(
        run_registry=run_registry,
        agent_runtime_support=agent_runtime_support,
        db_factory=get_request_db_factory(request),
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
    if isinstance(exc, SessionWorkspaceCleanupError):
        detail = "Runtime workspace cleanup failed; session was not deleted."
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
            detail=str(exc) or "Runtime path not found",
        ) from exc
    if isinstance(exc, (RuntimePathInvalidError, RuntimePathIsDirectoryError)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "Invalid runtime path",
        ) from exc
    raise exc


def _raise_http_for_session_or_runtime_error(exc: Exception) -> None:
    if isinstance(
        exc, (RuntimePathNotFoundError, RuntimePathInvalidError, RuntimePathIsDirectoryError)
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
            item,
            service,
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
    return await _session_response(session, service)


@router.get("/{id:uuid}")
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
    return await _session_response(session, service)


@router.patch("/{id:uuid}")
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
    return await _session_response(session, service)


@router.get("/{id:uuid}/context-usage", response_model=SessionContextUsageResponse)
async def get_session_context_usage(
    id: UUID,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionContextUsageResponse:
    service = _resolve_session_service(request)
    try:
        usage = await service.get_context_usage(db, session_id=id, user_id=user.sub)
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return SessionContextUsageResponse(**usage)


@router.get("/{id:uuid}/runtime/files", response_model=SessionRuntimeFilesResponse)
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
        await service.get_session(db, session_id=id, user_id=user.sub)
        files = await get_runtime_workspace_files(instance_name=_request_instance_name(request))
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
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeFilePreviewResponse:
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id=user.sub)
        files = await get_runtime_workspace_files(instance_name=_request_instance_name(request))
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
async def download_session_runtime_path(
    id: UUID,
    request: Request,
    path: str = Query(..., min_length=1),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id=user.sub)
        files = await get_runtime_workspace_files(instance_name=_request_instance_name(request))
        payload = await files.download(str(id), path=path)
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_or_runtime_error(exc)
        raise
    headers = {"Content-Disposition": f'attachment; filename="{payload.download_name}"'}
    return Response(content=payload.content, media_type=payload.media_type, headers=headers)


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
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id=user.sub)
        forwards = await get_runtime_port_forward_manager(
            instance_name=_request_instance_name(request)
        )
        forward = await forwards.get_forward(
            session_id=str(id),
            forward_id=forward_id,
        )
    except RuntimeForwardNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Runtime forward not found"
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
    token = websocket.query_params.get("token")
    if not token:
        token = websocket.cookies.get(ACCESS_TOKEN_COOKIE_NAME)
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return
    try:
        async with ManagerSessionLocal() as manager_db:
            user = await decode_and_validate_token(token, manager_db, expected_type="access")
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return
    result = await db.execute(select(Session).where(Session.id == id, Session.user_id == user.sub))
    if result.scalar_one_or_none() is None:
        await websocket.close(code=4004, reason="Session not found")
        return
    try:
        forwards = await get_runtime_port_forward_manager(
            instance_name=str(websocket.path_params["instance_name"])
        )
        forward = await forwards.get_forward(
            session_id=str(id),
            forward_id=forward_id,
        )
    except RuntimeForwardNotFound:
        await websocket.close(code=4004, reason="Runtime forward not found")
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
            await websocket.close(code=4005, reason="Runtime forward unavailable")
        except Exception:
            return


@router.get("/{id:uuid}/runtime/git/roots", response_model=SessionRuntimeGitRootsResponse)
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
        await service.get_session(db, session_id=id, user_id=user.sub)
        files = await get_runtime_workspace_files(instance_name=_request_instance_name(request))
        payload = await files.git_roots(str(id), path=path, limit=limit)
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_or_runtime_error(exc)
        raise
    return SessionRuntimeGitRootsResponse(**payload)


@router.get("/{id:uuid}/runtime/git/changed", response_model=SessionRuntimeGitChangedFilesResponse)
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
        await service.get_session(db, session_id=id, user_id=user.sub)
        files = await get_runtime_workspace_files(instance_name=_request_instance_name(request))
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
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionRuntimeGitDiffResponse:
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id=user.sub)
        files = await get_runtime_workspace_files(instance_name=_request_instance_name(request))
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


@router.delete("/{id:uuid}/terminals/{terminal_id}")
async def close_terminal(
    id: UUID,
    terminal_id: str,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not _TERMINAL_ID_HTTP_PATTERN.fullmatch(terminal_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="terminal_id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}",
        )
    service = _resolve_session_service(request)
    try:
        await service.get_session(db, session_id=id, user_id=user.sub)
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    terminal_manager = await get_runtime_terminal_manager(
        instance_name=_request_instance_name(request)
    )
    status_result = await terminal_manager.close_terminal(
        str(id),
        terminal_id=terminal_id,
    )
    manager = getattr(request.app.state, "ws_manager", None)
    if manager is not None and hasattr(manager, "broadcast"):
        await manager.broadcast(
            str(id),
            {"type": "terminal_closed", "session_id": str(id), "terminal_id": terminal_id},
        )
    return {
        "session_id": str(id),
        "terminal_id": terminal_id,
        "closed": status_result.status == "stopped",
    }


async def _cleanup_runtime_for_deleted_sessions(
    session_ids: list[UUID], *, instance_name: str
) -> None:
    terminal_manager = await get_runtime_terminal_manager(instance_name=instance_name)
    for session_id in session_ids:
        session_key = str(session_id)
        try:
            await get_browser_pool().remove(session_key, instance_name=instance_name)
        except Exception:
            _logger.debug(
                "failed to close runtime browser for deleted session %s",
                session_key,
                exc_info=True,
            )
        try:
            forwards = await get_runtime_port_forward_manager(instance_name=instance_name)
            await forwards.close_session(session_key)
        except Exception:
            _logger.debug(
                "failed to close runtime forwards for deleted session %s",
                session_key,
                exc_info=True,
            )
        try:
            desktop = await get_runtime_desktop_manager(instance_name=instance_name)
            await desktop.close_session(session_key)
        except Exception:
            _logger.debug(
                "failed to close runtime desktop for deleted session %s",
                session_key,
                exc_info=True,
            )
        await terminal_manager.delete_workspace(session_key)


@router.delete("/{id:uuid}")
async def delete_session(
    id: UUID,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | int]:
    service = _resolve_session_service(request)
    instance_name = _request_instance_name(request)
    try:
        deleted_descendants = await service.delete_session(
            db,
            session_id=id,
            user_id=user.sub,
            before_delete=lambda ids: _cleanup_runtime_for_deleted_sessions(
                ids,
                instance_name=instance_name,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_for_session_error(exc)
        raise
    return {"status": "deleted", "deleted_descendants": deleted_descendants}


@router.post("/{id:uuid}/stop")
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


@router.post("/{id:uuid}/read")
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


@router.post("/{id:uuid}/messages")
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


@router.post("/{id:uuid}/messages/{message_id:uuid}/retry")
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
    try:
        agent_runtime_support = get_request_instance_runtime_context(request).agent_runtime_support
    except RuntimeError:
        agent_runtime_support = None
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

    db_factory = get_request_db_factory(request)
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


@router.get("/{id:uuid}/messages")
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


@router.post("/{id:uuid}/chat", response_model=ChatResponse)
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
        has_unread=has_unread,
    )


async def _session_list_item_response(
    session: Session,
    service: SessionService,
    *,
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


def _request_instance_name(request: Request) -> str:
    return str(
        getattr(request.state, "instance_name", request.path_params.get("instance_name", ""))
    )
