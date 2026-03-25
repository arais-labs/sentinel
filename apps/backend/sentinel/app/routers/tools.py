from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.schemas.tools import (
    ToolDetailResponse,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolListResponse,
    ToolSummaryResponse,
)
from app.services.tools import ToolExecutor, ToolRegistry
from app.services.tools.executor import ToolExecutionError, ToolValidationError
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.registry_builder import build_default_registry

router = APIRouter()

_registry = build_default_registry()
_executor = ToolExecutor(_registry)


@router.get("")
async def list_tools(
    request: Request,
    user: TokenPayload = Depends(require_auth),
) -> ToolListResponse:
    _ = user
    registry, _ = _resolve_registry_and_executor(request)
    return ToolListResponse(items=[_summary(tool) for tool in registry.list_all()])


@router.get("/{name}")
async def get_tool(
    name: str,
    request: Request,
    user: TokenPayload = Depends(require_auth),
) -> ToolDetailResponse:
    _ = user
    registry, _ = _resolve_registry_and_executor(request)
    tool = registry.get(name)
    if tool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    return ToolDetailResponse(
        name=tool.name,
        description=tool.description,
        enabled=tool.enabled,
        parameters_schema=tool.parameters_schema,
    )


@router.post("/{name}/execute")
async def execute_tool(
    name: str,
    payload: ToolExecuteRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> ToolExecuteResponse:
    _ = user
    registry, executor = _resolve_registry_and_executor(request)
    tool = registry.get(name)
    if tool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")

    try:
        result, duration_ms = await executor.execute(
            name,
            payload.input,
            runtime=_runtime_context_from_request(payload.runtime_context),
        )
    except ToolValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    except ToolExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

    return ToolExecuteResponse(result=result, duration_ms=duration_ms)


def _summary(tool) -> ToolSummaryResponse:
    return ToolSummaryResponse(
        name=tool.name,
        description=tool.description,
        enabled=tool.enabled,
    )


def _resolve_registry_and_executor(request: Request) -> tuple[ToolRegistry, ToolExecutor]:
    registry = getattr(request.app.state, "tool_registry", None)
    executor = getattr(request.app.state, "tool_executor", None)
    if isinstance(registry, ToolRegistry) and isinstance(executor, ToolExecutor):
        return registry, executor
    return _registry, _executor


def _runtime_context_from_request(raw: dict[str, object]) -> ToolRuntimeContext:
    session_id_raw = raw.get("session_id")
    if session_id_raw is None:
        return ToolRuntimeContext()
    if not isinstance(session_id_raw, str) or not session_id_raw.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="runtime_context.session_id must be a non-empty UUID string",
        )
    try:
        return ToolRuntimeContext(session_id=UUID(session_id_raw.strip()))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="runtime_context.session_id must be a valid UUID string",
        ) from exc
