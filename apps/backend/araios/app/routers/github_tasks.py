from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.dependencies import get_db
from app.middleware.auth import require_permission
from app.database.models import GithubTask, gen_id
from app.schemas import GithubTaskCreate, GithubTaskUpdate, GithubTaskOut, GithubTaskListResponse, OkResponse

router = APIRouter()

_FIELD_MAP = {
    "prUrl": "pr_url",
    "workPackage": "work_package",
    "detectedAt": "detected_at",
    "readyAt": "ready_at",
    "handedOffAt": "handed_off_at",
    "closedAt": "closed_at",
    "updatedAt": "updated_at",
}
_REVERSE_MAP = {v: k for k, v in _FIELD_MAP.items()}
_COLUMNS = {c.name for c in GithubTask.__table__.columns}


def _to_dict(obj: GithubTask) -> dict:
    d = {}
    for col in GithubTask.__table__.columns:
        val = getattr(obj, col.name)
        key = _REVERSE_MAP.get(col.name, col.name)
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        d[key] = val
    return d


def _map_input(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        col = _FIELD_MAP.get(k, k)
        if col in _COLUMNS and col not in ("id", "updated_at"):
            out[col] = v
    return out


@router.get(
    "",
    response_model=GithubTaskListResponse,
    summary="List GitHub tasks",
    description="Returns all GitHub tasks. Optionally filter by client name.",
)
async def list_github_tasks(
    client: Optional[str] = Query(None, description="Filter tasks by client name"),
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("github-tasks.list")),
):
    q = db.query(GithubTask)
    if client:
        q = q.filter(GithubTask.client == client)
    items = q.all()
    return {"tasks": [_to_dict(t) for t in items]}


@router.post(
    "",
    status_code=201,
    response_model=GithubTaskOut,
    summary="Create a GitHub task",
    description="Create a new GitHub task. Agent role: allowed without approval.",
)
async def create_github_task(
    body: GithubTaskCreate,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("github-tasks.create")),
):
    item = GithubTask(id=gen_id(), **_map_input(body.model_dump(exclude_none=True)))
    db.add(item)
    db.commit()
    db.refresh(item)
    return _to_dict(item)


@router.patch(
    "/{item_id}",
    response_model=GithubTaskOut,
    summary="Update a GitHub task",
    description="Partially update a GitHub task. Work package is deep-merged with existing data. Agent role: allowed without approval.",
)
async def update_github_task(
    item_id: str,
    body: GithubTaskUpdate,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("github-tasks.update")),
):
    item = db.query(GithubTask).filter(GithubTask.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="GitHub task not found")
    mapped = _map_input(body.model_dump(exclude_none=True))
    # Deep merge for work_package
    if "work_package" in mapped and isinstance(item.work_package, dict):
        merged = {**(item.work_package or {}), **mapped["work_package"]}
        mapped["work_package"] = merged
    for col, val in mapped.items():
        setattr(item, col, val)
    db.commit()
    db.refresh(item)
    return _to_dict(item)


@router.delete(
    "/{item_id}",
    response_model=OkResponse,
    summary="Delete a GitHub task",
    description="Delete a GitHub task by ID. Agent role: requires admin approval.",
)
async def delete_github_task(
    item_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("github-tasks.delete")),
):
    count = db.query(GithubTask).filter(GithubTask.id == item_id).delete()
    db.commit()
    if not count:
        raise HTTPException(status_code=404, detail="GitHub task not found")
    return {"ok": True}
