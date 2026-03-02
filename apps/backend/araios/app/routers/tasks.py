from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.models import Task, gen_id
from app.dependencies import get_db
from app.middleware.auth import TokenPayload, get_current_user, require_permission
from app.schemas import OkResponse, TaskCreate, TaskListResponse, TaskOut, TaskUpdate

router = APIRouter()

_FIELD_MAP = {
    "prUrl": "pr_url",
    "workPackage": "work_package",
    "detectedAt": "detected_at",
    "readyAt": "ready_at",
    "handedOffAt": "handed_off_at",
    "closedAt": "closed_at",
    "updatedAt": "updated_at",
    "createdBy": "created_by",
    "updatedBy": "updated_by",
    "handoffTo": "handoff_to",
}
_REVERSE_MAP = {v: k for k, v in _FIELD_MAP.items()}
_COLUMNS = {c.name for c in Task.__table__.columns}


def _actor(user: TokenPayload) -> str:
    return (user.label or user.agent_id or user.sub or "unknown").strip() or "unknown"


def _to_dict(obj: Task) -> dict:
    out = {}
    for col in Task.__table__.columns:
        value = getattr(obj, col.name)
        key = _REVERSE_MAP.get(col.name, col.name)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        out[key] = value
    return out


def _map_input(data: dict) -> dict:
    out = {}
    for key, value in data.items():
        col = _FIELD_MAP.get(key, key)
        if col in _COLUMNS and col not in {"id", "updated_at"}:
            out[col] = value
    return out


def _record_activity(item: Task, *, actor: str, patch: dict) -> None:
    snapshot = dict(item.work_package or {})
    activity = snapshot.get("activity")
    if not isinstance(activity, list):
        activity = []

    changed_fields = [field for field in ("status", "owner", "priority", "handoff_to") if field in patch]
    if not changed_fields:
        return

    entry = {
        "at": datetime.now(UTC).isoformat(),
        "actor": actor,
        "changes": {name: patch[name] for name in changed_fields},
    }
    activity.append(entry)
    snapshot["activity"] = activity[-50:]
    item.work_package = snapshot


@router.get(
    "",
    response_model=TaskListResponse,
    summary="List tasks",
    description="Returns all tasks. Supports filtering by client, status, and owner.",
)
async def list_tasks(
    client: Optional[str] = Query(None, description="Filter tasks by client name"),
    status: Optional[str] = Query(None, description="Filter tasks by status"),
    owner: Optional[str] = Query(None, description="Filter tasks by owner"),
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("tasks.list")),
):
    query = db.query(Task)
    if client:
        query = query.filter(Task.client == client)
    if status:
        query = query.filter(Task.status == status)
    if owner:
        query = query.filter(Task.owner == owner)
    query = query.order_by(Task.updated_at.desc())
    return {"tasks": [_to_dict(item) for item in query.all()]}


@router.post(
    "",
    status_code=201,
    response_model=TaskOut,
    summary="Create task",
    description="Create a collaborative task. Admin and agents can create tasks.",
)
async def create_task(
    body: TaskCreate,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_user),
    _: None = Depends(require_permission("tasks.create")),
):
    actor = _actor(user)
    payload = _map_input(body.model_dump(exclude_none=True))
    payload.setdefault("created_by", actor)
    payload.setdefault("updated_by", actor)
    payload.setdefault("owner", actor)

    item = Task(id=gen_id(), **payload)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _to_dict(item)


@router.patch(
    "/{task_id}",
    response_model=TaskOut,
    summary="Update task",
    description="Partially update a task. Work package is deep-merged with existing data.",
)
async def update_task(
    task_id: str,
    body: TaskUpdate,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_user),
    _: None = Depends(require_permission("tasks.update")),
):
    item = db.query(Task).filter(Task.id == task_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Task not found")

    actor = _actor(user)
    mapped = _map_input(body.model_dump(exclude_none=True))
    if "work_package" in mapped and isinstance(item.work_package, dict):
        mapped["work_package"] = {**(item.work_package or {}), **mapped["work_package"]}
    mapped["updated_by"] = actor

    for col, val in mapped.items():
        setattr(item, col, val)

    _record_activity(item, actor=actor, patch=mapped)
    db.commit()
    db.refresh(item)
    return _to_dict(item)


@router.delete(
    "/{task_id}",
    response_model=OkResponse,
    summary="Delete task",
    description="Delete a task by ID. Agent role requires admin approval.",
)
async def delete_task(
    task_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("tasks.delete")),
):
    count = db.query(Task).filter(Task.id == task_id).delete()
    db.commit()
    if not count:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}
