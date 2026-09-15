from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_request_instance_runtime_context, get_request_run_registry
from app.models import Session, SubAgentTask
from app.schemas.sub_agents import (
    CreateSubAgentTaskRequest,
    InterjectRequest,
    SubAgentTaskListResponse,
    SubAgentTaskResponse,
)
from app.services.sub_agents.orchestrator import SubAgentOrchestrator
from app.services.sub_agents.accounting import inherited_model, task_usage
from app.services.sub_agents.messaging import send_message
from app.services.ws.ws_manager import ConnectionManager

router = APIRouter()


@router.post("/{id}/sub-agents", status_code=status.HTTP_202_ACCEPTED)
async def create_sub_agent_task(
    id: UUID,
    payload: CreateSubAgentTaskRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SubAgentTaskResponse:

    async with get_request_run_registry(request).workspace_change_guard():
        session = await _get_session_record(db, id)
        if await _active_task_count(db, session.id) >= 3:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many concurrent tasks"
            )

        task = SubAgentTask(
            session_id=session.id,
            objective=payload.name,
            context=payload.scope,
            constraints=[],
            allowed_tools=payload.allowed_tools,
            model=await inherited_model(db, id, payload.tier),
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
    return await _task_response(db, task)


@router.get("/{id}/sub-agents")
async def list_sub_agent_tasks(
    id: UUID,
    db: AsyncSession = Depends(get_db),
) -> SubAgentTaskListResponse:
    _ = await _get_session_record(db, id)
    result = await db.execute(select(SubAgentTask).where(SubAgentTask.session_id == id))
    tasks = result.scalars().all()
    tasks.sort(key=lambda item: item.created_at, reverse=True)
    return SubAgentTaskListResponse(
        items=[await _task_response(db, task) for task in tasks], total=len(tasks)
    )


@router.get("/{id}/sub-agents/{task_id}")
async def get_sub_agent_task(
    id: UUID,
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> SubAgentTaskResponse:
    _ = await _get_session_record(db, id)
    task = await _get_session_task(db, id, task_id)
    return await _task_response(db, task)


@router.post("/{id}/sub-agents/{task_id}/interject")
async def interject_sub_agent_task(
    id: UUID,
    task_id: UUID,
    payload: InterjectRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    _ = await _get_session_record(db, id)
    task = await _get_session_task(db, id, task_id)

    child_id = (task.result or {}).get("child_session_id")
    if not child_id:
        raise HTTPException(409, "Sub-agent is starting")
    return await send_message(
        db,
        sender_id=id,
        target=str(task.id),
        content=payload.message,
        orchestrator=_resolve_orchestrator(request),
    )


@router.delete("/{id}/sub-agents/{task_id}")
async def cancel_sub_agent_task(
    id: UUID,
    task_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    _ = await _get_session_record(db, id)
    task = await _get_session_task(db, id, task_id)
    if task.status not in {"pending", "running"}:
        return {"status": task.status}
    await _resolve_orchestrator(request).stop_task(task.id)
    await db.refresh(task)
    if task.status in {"pending", "running"}:
        task.status = "cancelled"
        task.completed_at = datetime.now(UTC)
        await db.commit()
    return {"status": task.status}


async def _get_session_record(db: AsyncSession, session_id: UUID) -> Session:
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalars().first()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


async def _get_session_task(db: AsyncSession, session_id: UUID, task_id: UUID) -> SubAgentTask:
    result = await db.execute(
        select(SubAgentTask).where(
            SubAgentTask.session_id == session_id, SubAgentTask.id == task_id
        )
    )
    task = result.scalars().first()
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Sub-agent task not found"
        )
    return task


async def _active_task_count(db: AsyncSession, session_id: UUID) -> int:
    result = await db.execute(select(SubAgentTask).where(SubAgentTask.session_id == session_id))
    tasks = result.scalars().all()
    return len([item for item in tasks if item.status in {"pending", "running"}])


async def _task_response(db, task: SubAgentTask) -> SubAgentTaskResponse:

    usage = await task_usage(db, task)
    return SubAgentTaskResponse(
        id=task.id,
        session_id=task.session_id,
        name=task.objective,
        scope=task.context,
        status=task.status,
        allowed_tools=task.allowed_tools or [],
        turns_used=task.turns_used or 0,
        tokens_used=usage["input_tokens"] + usage["output_tokens"],
        model=task.model,
        usage=usage,
        result=task.result,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
    )


def _resolve_orchestrator(request: Request) -> SubAgentOrchestrator:
    return get_request_instance_runtime_context(request).sub_agent_orchestrator


def _resolve_ws_manager(request: Request) -> ConnectionManager | None:
    manager = getattr(request.app.state, "ws_manager", None)
    if isinstance(manager, ConnectionManager):
        return manager
    return None
