from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.config import settings
from app.middleware.auth import TokenPayload, require_auth
from app.models import PlaywrightTask
from app.routers import admin as admin_router
from app.schemas.playwright import (
    PlaywrightBrowserResetResponse,
    CreatePlaywrightTaskRequest,
    PlaywrightLiveViewResponse,
    PlaywrightScreenshotResponse,
    PlaywrightTaskResponse,
)
from app.services.browser_live_view import build_live_view_url, is_live_view_available
from app.services.playwright_runner import PlaywrightRunner
from app.services.tools.browser_tool import BrowserManager

router = APIRouter()
_runner = PlaywrightRunner()


@router.post("/tasks", response_model=PlaywrightTaskResponse)
async def create_playwright_task(
    payload: CreatePlaywrightTaskRequest,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> PlaywrightTaskResponse:
    if await admin_router.is_estop_active(db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Playwright disabled while ESTOP active")

    task = PlaywrightTask(
        user_id=user.sub,
        url=payload.url,
        action=payload.action,
        options=payload.options,
        status="pending",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    task = await _runner.execute_task(db, task)
    return _task_response(task)


@router.get("/live-view", response_model=PlaywrightLiveViewResponse)
async def get_live_view(
    request: Request,
    _user: TokenPayload = Depends(require_auth),
) -> PlaywrightLiveViewResponse:
    if not settings.browser_live_view_enabled:
        return PlaywrightLiveViewResponse(
            enabled=False,
            available=False,
            url=None,
            reason="Live browser view is disabled.",
        )
    url = build_live_view_url(request)
    available = is_live_view_available()
    return PlaywrightLiveViewResponse(
        enabled=True,
        available=available,
        url=url,
        reason=None if available else "Live browser runtime is not reachable.",
    )


@router.post("/reset-browser", response_model=PlaywrightBrowserResetResponse)
async def reset_browser_runtime(
    request: Request,
    _user: TokenPayload = Depends(require_auth),
) -> PlaywrightBrowserResetResponse:
    manager = _resolve_browser_manager(request)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Browser manager is not available",
        )
    payload = await manager.reset()
    return PlaywrightBrowserResetResponse(**payload)


@router.get("/tasks/{id}", response_model=PlaywrightTaskResponse)
async def get_playwright_task(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> PlaywrightTaskResponse:
    task = await _get_owned_task(db, id, user.sub)
    return _task_response(task)


@router.delete("/tasks/{id}")
async def cancel_playwright_task(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    task = await _get_owned_task(db, id, user.sub)
    task.status = "cancelled"
    task.completed_at = datetime.now(UTC)
    await db.commit()
    return {"status": "cancelled"}


@router.post("/tasks/{id}/screenshot", response_model=PlaywrightScreenshotResponse)
async def task_screenshot(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> PlaywrightScreenshotResponse:
    task = await _get_owned_task(db, id, user.sub)
    if task.status in {"failed", "cancelled"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task is not in a screenshot-ready state")
    screenshot = await _runner.capture_screenshot(db, task)
    return PlaywrightScreenshotResponse(**screenshot)


async def _get_owned_task(db: AsyncSession, task_id: UUID, user_id: str) -> PlaywrightTask:
    result = await db.execute(
        select(PlaywrightTask).where(PlaywrightTask.id == task_id, PlaywrightTask.user_id == user_id)
    )
    task = result.scalars().first()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playwright task not found")
    return task


def _task_response(task: PlaywrightTask) -> PlaywrightTaskResponse:
    return PlaywrightTaskResponse(
        id=task.id,
        user_id=task.user_id,
        url=task.url,
        action=task.action,
        status=task.status,
        result=task.result,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
    )


def _resolve_browser_manager(request: Request) -> BrowserManager | None:
    manager = getattr(request.app.state, "browser_manager", None)
    if isinstance(manager, BrowserManager):
        return manager
    return None
