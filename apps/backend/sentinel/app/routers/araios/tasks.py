"""AraiOS Tasks router — async SQLAlchemy."""
from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.models.araios import AraiosTask, araios_gen_id
from app.schemas.araios import TaskCreate, TaskUpdate, TaskOut, TaskListResponse, OkResponse

router = APIRouter(tags=["araios-tasks"])


# ── Field mapping (camelCase schema <-> snake_case model) ──

_FIELD_MAP: dict[str, str] = {
    "createdBy": "created_by",
    "updatedBy": "updated_by",
    "handoffTo": "handoff_to",
    "prUrl": "pr_url",
    "workPackage": "work_package",
    "detectedAt": "detected_at",
    "readyAt": "ready_at",
    "handedOffAt": "handed_off_at",
    "closedAt": "closed_at",
}
_REVERSE_MAP: dict[str, str] = {v: k for k, v in _FIELD_MAP.items()}


def _map_input(data: dict[str, Any]) -> dict[str, Any]:
    """Convert camelCase keys to snake_case for the ORM model."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        out[_FIELD_MAP.get(k, k)] = v
    return out


def _to_dict(task: AraiosTask) -> dict[str, Any]:
    """Convert ORM model to camelCase dict for the response schema."""
    d: dict[str, Any] = {}
    for col in AraiosTask.__table__.columns:
        val = getattr(task, col.key)
        camel = _REVERSE_MAP.get(col.key, col.key)
        if camel == "updated_at":
            camel = "updatedAt"
        if isinstance(val, datetime):
            val = val.isoformat()
        d[camel] = val
    return d


def _get_agent_id(user: TokenPayload = Depends(require_auth)) -> str:
    return user.agent_id or user.sub


def _actor(agent_id: str) -> str:
    return agent_id


async def _record_activity(db: AsyncSession, task: AraiosTask, agent_id: str, changes: dict[str, Any]) -> None:
    """Stamp the task with who updated it and when."""
    task.updated_by = _actor(agent_id)
    # Activity is recorded inline on the task itself (updated_at is auto-set by onupdate).


# ── Routes ──


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    client: str | None = Query(None),
    status: str | None = Query(None),
    owner: str | None = Query(None),
    _user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AraiosTask)
    if client:
        stmt = stmt.where(AraiosTask.client == client)
    if status:
        stmt = stmt.where(AraiosTask.status == status)
    if owner:
        stmt = stmt.where(AraiosTask.owner == owner)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return TaskListResponse(tasks=[TaskOut(**_to_dict(r)) for r in rows])


@router.post("", response_model=TaskOut, status_code=201)
async def create_task(
    body: TaskCreate,
    agent_id: str = Depends(_get_agent_id),
    db: AsyncSession = Depends(get_db),
):
    data = _map_input(body.model_dump(exclude_none=True))
    data.setdefault("created_by", _actor(agent_id))
    data.setdefault("updated_by", _actor(agent_id))
    task = AraiosTask(id=araios_gen_id(), **data)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return TaskOut(**_to_dict(task))


@router.patch("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: str,
    body: TaskUpdate,
    agent_id: str = Depends(_get_agent_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AraiosTask).where(AraiosTask.id == task_id))
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    changes = _map_input(body.model_dump(exclude_none=True))
    for key, val in changes.items():
        setattr(task, key, val)

    await _record_activity(db, task, agent_id, changes)

    await db.commit()
    await db.refresh(task)
    return TaskOut(**_to_dict(task))


@router.delete("/{task_id}", response_model=OkResponse)
async def delete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AraiosTask).where(AraiosTask.id == task_id))
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.delete(task)
    await db.commit()
    return OkResponse()
