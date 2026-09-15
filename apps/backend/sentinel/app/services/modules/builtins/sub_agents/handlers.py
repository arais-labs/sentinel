"""Native module: delegate — spawn, status, list, resume, and cancel delegated sub-agent tasks."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.database.database import AsyncSessionLocal
from app.models import SubAgentTask
from app.services.modules.runtime_services import (
    get_sub_agent_orchestrator,
    get_ws_manager,
)
from app.services.sub_agents.accounting import inherited_model, task_usage
from app.services.sub_agents.messaging import root_session
from sentral.errors import ToolValidationError
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.runtime_context import require_session_id

# ── Helpers ──


# ---------------------------------------------------------------------------
# Handler functions (module-level)
# ---------------------------------------------------------------------------


async def handle_spawn(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    sid = require_session_id(runtime)
    objective = payload.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise ToolValidationError("Field 'objective' must be a non-empty string")

    scope = payload.get("scope")
    if scope is not None and not isinstance(scope, str):
        raise ToolValidationError("Field 'scope' must be a string")

    allowed_tools = payload.get("allowed_tools", [])
    if not isinstance(allowed_tools, list):
        raise ToolValidationError("Field 'allowed_tools' must be an array")
    normalized_allowed_tools = [str(t) for t in allowed_tools if isinstance(t, str)]

    tier = payload.get("tier")
    if tier is not None and tier not in {"fast", "normal", "hard"}:
        raise ToolValidationError("Unknown model tier")

    orchestrator = runtime.sub_agent_orchestrator or get_sub_agent_orchestrator()
    if orchestrator is None:
        raise ToolValidationError("Sub-agent orchestrator is not configured")

    async with AsyncSessionLocal() as db:
        # Enforce max 3 concurrent tasks per session
        result = await db.execute(select(SubAgentTask).where(SubAgentTask.session_id == sid))
        tasks = result.scalars().all()
        active = [t for t in tasks if t.status in {"pending", "running"}]
        if len(active) >= 3:
            raise ToolValidationError("Max 3 concurrent sub-agent tasks per session")

        task = SubAgentTask(
            session_id=sid,
            objective=objective.strip(),
            context=(scope.strip() if isinstance(scope, str) and scope.strip() else None),
            constraints=[],
            allowed_tools=normalized_allowed_tools,
            model=await inherited_model(db, sid, tier),
            status="pending",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id

    if not orchestrator.start_task(task_id):
        async with AsyncSessionLocal() as db:
            task = await db.get(SubAgentTask, task_id)
            await orchestrator.complete_task(db, task)
        return {
            "task_id": str(task_id),
            "status": "failed",
            "error": "Agent runtime unavailable",
        }
    ws_manager = get_ws_manager()
    if ws_manager is not None and hasattr(ws_manager, "broadcast_sub_agent_started"):
        with contextlib.suppress(Exception):
            await ws_manager.broadcast_sub_agent_started(
                str(sid),
                str(task_id),
                objective.strip(),
            )
    return {
        "task_id": str(task_id),
        "status": "pending",
        "objective": objective.strip(),
    }


async def handle_status(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToolValidationError("Field 'task_id' must be a non-empty string")

    tid = UUID(task_id.strip())
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SubAgentTask).where(SubAgentTask.id == tid))
        task = result.scalars().first()
        if task is None:
            raise ToolValidationError("Sub-agent task not found")

        if await root_session(db, task.session_id) != await root_session(
            db, require_session_id(runtime)
        ):
            raise ToolValidationError("Sub-agent task not found for this conversation")
        usage = await task_usage(db, task)
        return {
            "task_id": str(task.id),
            "objective": task.objective,
            "status": task.status,
            "can_resume": task.status in {"cancelled", "failed", "completed"}
            and bool((task.result or {}).get("child_session_id")),
            "turns_used": task.turns_used or 0,
            "tokens_used": usage["input_tokens"] + usage["output_tokens"],
            "usage": usage,
            "model": task.model,
            "result": task.result,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "completed_at": (task.completed_at.isoformat() if task.completed_at else None),
        }


async def handle_list(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    sid = require_session_id(runtime)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SubAgentTask).where(SubAgentTask.session_id == sid))
        tasks = result.scalars().all()
        tasks.sort(key=lambda t: t.created_at, reverse=True)

        return {
            "tasks": [
                {
                    "task_id": str(t.id),
                    "objective": t.objective,
                    "status": t.status,
                    "can_resume": t.status in {"cancelled", "failed", "completed"}
                    and bool((t.result or {}).get("child_session_id")),
                    "turns_used": int(t.turns_used or 0),
                    "tokens_used": t.tokens_used or 0,
                }
                for t in tasks
            ],
            "total": len(tasks),
        }


async def handle_cancel(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToolValidationError("Field 'task_id' must be a non-empty string")

    sid = require_session_id(runtime)
    tid = UUID(task_id.strip())

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SubAgentTask).where(
                SubAgentTask.id == tid,
                SubAgentTask.session_id == sid,
            )
        )
        task = result.scalars().first()
        if task is None:
            raise ToolValidationError("Sub-agent task not found for this session")

        previous_status = str(task.status)
        if previous_status in {"completed", "failed", "cancelled"}:
            result_payload = task.result if isinstance(task.result, dict) else None
            return {
                "task_id": str(task.id),
                "session_id": str(task.session_id),
                "cancelled": False,
                "status": previous_status,
                "previous_status": previous_status,
                "message": "Task already terminal; no cancellation performed.",
                "result": result_payload,
            }

        orchestrator = runtime.sub_agent_orchestrator or get_sub_agent_orchestrator()
        cancel_signal_sent = await orchestrator.stop_task(tid) if orchestrator else False
        await db.refresh(task)
        if task.status in {"pending", "running"}:
            task.status = "cancelled"
            task.completed_at = datetime.now(UTC)
        task.result = {
            **(task.result or {}),
            "cancel_reason": "Cancelled by agent request",
        }
        await db.commit()

    return {
        "task_id": str(task.id),
        "session_id": str(task.session_id),
        "cancelled": True,
        "status": str(task.status),
        "previous_status": previous_status,
        "cancel_signal_sent": cancel_signal_sent,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "result": task.result if isinstance(task.result, dict) else None,
    }


async def handle_resume(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    task_id, message = payload.get("task_id"), payload.get("message")
    if not isinstance(task_id, str):
        raise ToolValidationError("Field 'task_id' must be a sub-agent task UUID")
    try:
        tid = UUID(task_id.strip())
    except ValueError as exc:
        raise ToolValidationError("Field 'task_id' must be a sub-agent task UUID") from exc
    if not isinstance(message, str) or not message.strip():
        raise ToolValidationError(
            "Field 'message' must describe what the agent should continue doing"
        )
    orchestrator = runtime.sub_agent_orchestrator or get_sub_agent_orchestrator()
    if orchestrator is None:
        raise ToolValidationError("Sub-agent orchestrator is not configured")
    return await orchestrator.resume_task(
        tid, parent_id=require_session_id(runtime), message=message.strip()
    )
