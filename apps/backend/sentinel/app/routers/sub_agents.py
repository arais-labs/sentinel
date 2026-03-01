from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.models import Session, SubAgentTask
from app.schemas.sub_agents import CreateSubAgentTaskRequest, InterjectRequest, SubAgentTaskListResponse, SubAgentTaskResponse
from app.services.sub_agents import SubAgentOrchestrator
from app.services.ws_manager import ConnectionManager

router = APIRouter()
_orchestrator = SubAgentOrchestrator()


@router.post("/{id}/sub-agents", status_code=status.HTTP_202_ACCEPTED)
async def create_sub_agent_task(
    id: UUID,
    payload: CreateSubAgentTaskRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SubAgentTaskResponse:
    session = await _get_owned_session(db, id, user.sub)
    if await _active_task_count(db, session.id) >= 3:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many concurrent tasks")

    task = SubAgentTask(
        session_id=session.id,
        objective=payload.name,
        context=payload.scope,
        constraints=(
            [{"type": "browser_tab", "tab_id": payload.browser_tab_id}]
            if payload.browser_tab_id
            else []
        ),
        allowed_tools=payload.allowed_tools,
        max_turns=payload.max_steps,
        timeout_seconds=payload.timeout_seconds,
        status="pending",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    ws_manager = _resolve_ws_manager(request)
    if ws_manager is not None:
        await ws_manager.broadcast_sub_agent_started(str(session.id), str(task.id), task.objective)

    orchestrator = _resolve_orchestrator(request)
    started = orchestrator.start_task(task.id)
    if not started:
        task = await orchestrator.complete_task(db, task)
    return _task_response(task)


@router.get("/{id}/sub-agents")
async def list_sub_agent_tasks(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SubAgentTaskListResponse:
    _ = await _get_owned_session(db, id, user.sub)
    result = await db.execute(select(SubAgentTask).where(SubAgentTask.session_id == id))
    tasks = result.scalars().all()
    tasks.sort(key=lambda item: item.created_at, reverse=True)
    return SubAgentTaskListResponse(items=[_task_response(task) for task in tasks], total=len(tasks))


@router.get("/{id}/sub-agents/{task_id}")
async def get_sub_agent_task(
    id: UUID,
    task_id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SubAgentTaskResponse:
    _ = await _get_owned_session(db, id, user.sub)
    task = await _get_session_task(db, id, task_id)
    return _task_response(task)


@router.post("/{id}/sub-agents/{task_id}/interject")
async def interject_sub_agent_task(
    id: UUID,
    task_id: UUID,
    payload: InterjectRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    _ = await _get_owned_session(db, id, user.sub)
    task = await _get_session_task(db, id, task_id)
    if task.status != "running":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task is not running")
    orchestrator = _resolve_orchestrator(request)
    if not orchestrator.inject_message(task.id, payload.message):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task not accepting messages")
    return {"status": "injected"}


@router.delete("/{id}/sub-agents/{task_id}")
async def cancel_sub_agent_task(
    id: UUID,
    task_id: UUID,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    _ = await _get_owned_session(db, id, user.sub)
    task = await _get_session_task(db, id, task_id)
    task.status = "cancelled"
    task.completed_at = datetime.now(UTC)
    await db.commit()
    orchestrator = _resolve_orchestrator(request)
    orchestrator.cancel_task(task.id)
    return {"status": "cancelled"}


async def _get_owned_session(db: AsyncSession, session_id: UUID, user_id: str) -> Session:
    result = await db.execute(select(Session).where(Session.id == session_id, Session.user_id == user_id))
    session = result.scalars().first()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


async def _get_session_task(db: AsyncSession, session_id: UUID, task_id: UUID) -> SubAgentTask:
    result = await db.execute(
        select(SubAgentTask).where(SubAgentTask.session_id == session_id, SubAgentTask.id == task_id)
    )
    task = result.scalars().first()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sub-agent task not found")
    return task


async def _active_task_count(db: AsyncSession, session_id: UUID) -> int:
    result = await db.execute(select(SubAgentTask).where(SubAgentTask.session_id == session_id))
    tasks = result.scalars().all()
    return len([item for item in tasks if item.status in {"pending", "running"}])


def _task_response(task: SubAgentTask) -> SubAgentTaskResponse:
    turns_used = int(task.turns_used or 0)
    max_steps = int(task.max_turns or 0)
    grace_turns_used = max(0, turns_used - max_steps)
    return SubAgentTaskResponse(
        id=task.id,
        session_id=task.session_id,
        name=task.objective,
        scope=task.context,
        browser_tab_id=_extract_browser_tab_id(task),
        max_steps=max_steps,
        status=task.status,
        allowed_tools=task.allowed_tools if isinstance(task.allowed_tools, list) else [],
        turns_used=turns_used,
        grace_turns_used=grace_turns_used,
        tokens_used=task.tokens_used or 0,
        result=task.result,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
    )


def _extract_browser_tab_id(task: SubAgentTask) -> str | None:
    constraints = task.constraints if isinstance(task.constraints, list) else []
    for item in constraints:
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "")).strip().lower() != "browser_tab":
            continue
        tab_id = item.get("tab_id")
        if isinstance(tab_id, str) and tab_id.strip():
            return tab_id.strip()
    return None


def _resolve_orchestrator(request: Request) -> SubAgentOrchestrator:
    orchestrator = getattr(request.app.state, "sub_agent_orchestrator", None)
    if isinstance(orchestrator, SubAgentOrchestrator):
        return orchestrator
    return _orchestrator


def _resolve_ws_manager(request: Request) -> ConnectionManager | None:
    manager = getattr(request.app.state, "ws_manager", None)
    if isinstance(manager, ConnectionManager):
        return manager
    return None
