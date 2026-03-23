"""Native module: tasks — task tracking and management."""
from __future__ import annotations

import logging
from datetime import datetime, UTC
from typing import Any

from sqlalchemy import select

from app.database.database import AsyncSessionLocal
from app.models.araios import AraiosTask, araios_gen_id
from app.services.tools.executor import ToolValidationError

logger = logging.getLogger(__name__)
ALLOWED_TASK_COMMANDS = ("list", "create", "update", "delete")


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
    """Convert ORM model to camelCase dict for the response."""
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


# ---------------------------------------------------------------------------
# Handler functions (module-level)
# ---------------------------------------------------------------------------


async def handle_list(payload: dict[str, Any]) -> dict[str, Any]:
    status = payload.get("status")
    priority = payload.get("priority")
    async with AsyncSessionLocal() as db:
        stmt = select(AraiosTask)
        if status:
            stmt = stmt.where(AraiosTask.status == status)
        if priority:
            stmt = stmt.where(AraiosTask.priority == priority)
        result = await db.execute(stmt)
        rows = result.scalars().all()
        return {"tasks": [_to_dict(r) for r in rows]}


async def handle_create(payload: dict[str, Any]) -> dict[str, Any]:
    title = payload.get("title")
    if not title:
        raise ValueError("'title' is required")
    data = _map_input(
        {k: v for k, v in payload.items() if k != "session_id" and v is not None}
    )
    data.pop("id", None)
    task = AraiosTask(id=araios_gen_id(), **data)
    async with AsyncSessionLocal() as db:
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return _to_dict(task)


async def handle_update(payload: dict[str, Any]) -> dict[str, Any]:
    task_id = payload.get("id")
    if not task_id:
        raise ValueError("'id' is required")
    changes = _map_input(
        {k: v for k, v in payload.items() if k not in ("id", "session_id") and v is not None}
    )
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AraiosTask).where(AraiosTask.id == task_id)
        )
        task = result.scalars().first()
        if not task:
            raise ValueError(f"Task '{task_id}' not found")
        for key, val in changes.items():
            setattr(task, key, val)
        await db.commit()
        await db.refresh(task)
        return _to_dict(task)


async def handle_delete(payload: dict[str, Any]) -> dict[str, Any]:
    task_id = payload.get("id")
    if not task_id:
        raise ValueError("'id' is required")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AraiosTask).where(AraiosTask.id == task_id)
        )
        task = result.scalars().first()
        if not task:
            raise ValueError(f"Task '{task_id}' not found")
        await db.delete(task)
        await db.commit()
        return {"ok": True, "message": f"Task '{task_id}' deleted"}


# ---------------------------------------------------------------------------
# Unified tool dispatch
# ---------------------------------------------------------------------------

def _task_command(payload: dict[str, Any]) -> str:
    raw = payload.get("command")
    if not isinstance(raw, str) or not raw.strip():
        raise ToolValidationError("Field 'command' must be a non-empty string")
    normalized = raw.strip().lower()
    if normalized not in ALLOWED_TASK_COMMANDS:
        raise ToolValidationError(
            "Field 'command' must be one of: " + ", ".join(ALLOWED_TASK_COMMANDS)
        )
    return normalized


async def handle_run(payload: dict[str, Any]) -> dict[str, Any]:
    command = _task_command(payload)
    if command == "list":
        return await handle_list(payload)
    if command == "create":
        return await handle_create(payload)
    if command == "update":
        return await handle_update(payload)
    return await handle_delete(payload)
