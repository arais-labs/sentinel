"""Native module: sub_agents — spawn, check, list, and cancel sub-agent tasks."""
from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.services.araios.runtime_services import (
    get_browser_pool,
    get_sub_agent_orchestrator,
    get_ws_manager,
)
from app.database.database import AsyncSessionLocal
from app.models import SubAgentTask
from app.services.browser.manager import BrowserManager
from app.services.tools.executor import ToolValidationError

ALLOWED_SUB_AGENT_COMMANDS = ("spawn", "check", "list", "cancel")


# ── Helpers ──


def _extract_browser_tab_constraint(constraints: Any) -> str | None:
    items = constraints if isinstance(constraints, list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "")).strip().lower() != "browser_tab":
            continue
        tab_id = item.get("tab_id")
        if isinstance(tab_id, str) and tab_id.strip():
            return tab_id.strip()
    return None


def _active_sub_agent_tab_ids(tasks: list[SubAgentTask]) -> set[str]:
    reserved: set[str] = set()
    for task in tasks:
        tab_id = _extract_browser_tab_constraint(task.constraints)
        if tab_id:
            reserved.add(tab_id)
    return reserved


def _sub_agent_may_use_browser(allowed_tools: list[str]) -> bool:
    if not allowed_tools:
        return True
    return any(tool == "browser" or tool.startswith("browser_") for tool in allowed_tools)


async def _select_sub_agent_browser_tab_id(
    browser_manager: BrowserManager,
    *,
    reserved_tab_ids: set[str],
) -> str | None:
    try:
        tabs_payload = await browser_manager.list_tabs()
        tabs = tabs_payload.get("tabs", [])
        active_tab_id = tabs_payload.get("active_tab_id")
        active_tab_id = active_tab_id.strip() if isinstance(active_tab_id, str) else None

        for item in tabs:
            if not isinstance(item, dict):
                continue
            tab_id = item.get("tab_id")
            if not isinstance(tab_id, str) or not tab_id.strip():
                continue
            normalized_tab_id = tab_id.strip()
            if normalized_tab_id in reserved_tab_ids:
                continue
            if active_tab_id is not None and normalized_tab_id == active_tab_id:
                continue
            return normalized_tab_id

        opened = await browser_manager.open_tab("about:blank")
        tab_id = opened.get("tab_id")
        normalized_opened_tab_id = (
            tab_id.strip() if isinstance(tab_id, str) and tab_id.strip() else None
        )
        if (
            active_tab_id is not None
            and normalized_opened_tab_id is not None
            and active_tab_id != normalized_opened_tab_id
        ):
            with contextlib.suppress(Exception):
                await browser_manager.focus_tab(active_tab_id)
        return normalized_opened_tab_id
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Handler functions (module-level)
# ---------------------------------------------------------------------------


async def handle_spawn(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ToolValidationError("Field 'session_id' must be a non-empty string")
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
    browser_tab_id = payload.get("browser_tab_id")
    if browser_tab_id is not None and (
        not isinstance(browser_tab_id, str) or not browser_tab_id.strip()
    ):
        raise ToolValidationError("Field 'browser_tab_id' must be a non-empty string")
    normalized_browser_tab_id = (
        browser_tab_id.strip()
        if isinstance(browser_tab_id, str) and browser_tab_id.strip()
        else None
    )

    max_steps = payload.get("max_steps", 10)
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps < 1:
        raise ToolValidationError("Field 'max_steps' must be a positive integer")
    max_steps = min(max_steps, 50)

    timeout_seconds = payload.get("timeout_seconds", 300)
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds < 1
    ):
        raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")
    timeout_seconds = min(timeout_seconds, 3600)

    sid = UUID(session_id.strip())
    auto_assigned_browser_tab = False

    async with AsyncSessionLocal() as db:
        # Enforce max 3 concurrent tasks per session
        result = await db.execute(select(SubAgentTask).where(SubAgentTask.session_id == sid))
        tasks = result.scalars().all()
        active = [t for t in tasks if t.status in {"pending", "running"}]
        if len(active) >= 3:
            raise ToolValidationError("Max 3 concurrent sub-agent tasks per session")
        browser_pool = get_browser_pool()
        if (
            normalized_browser_tab_id is None
            and _sub_agent_may_use_browser(normalized_allowed_tools)
        ):
            reserved_tab_ids = _active_sub_agent_tab_ids(active)
            try:
                _mgr = await browser_pool.get(sid)
                normalized_browser_tab_id = await _select_sub_agent_browser_tab_id(
                    _mgr, reserved_tab_ids=reserved_tab_ids
                )
            except Exception:
                pass
            auto_assigned_browser_tab = normalized_browser_tab_id is not None

        task = SubAgentTask(
            session_id=sid,
            objective=objective.strip(),
            context=(scope.strip() if isinstance(scope, str) and scope.strip() else None),
            constraints=(
                [{"type": "browser_tab", "tab_id": normalized_browser_tab_id}]
                if normalized_browser_tab_id
                else []
            ),
            allowed_tools=normalized_allowed_tools,
            max_turns=max_steps,
            timeout_seconds=timeout_seconds,
            status="pending",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id

    orchestrator = get_sub_agent_orchestrator()
    if orchestrator is None:
        raise ToolValidationError("Sub-agent orchestrator is not configured")
    orchestrator.start_task(task_id)
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
        "timeout_seconds": timeout_seconds,
        "browser_tab_id": normalized_browser_tab_id,
        "auto_assigned_browser_tab": auto_assigned_browser_tab,
        "note": (
            f"Sub-agent spawned (timeout: {timeout_seconds}s). "
            "Next steps: use sub_agents with command=check and this task_id before reporting delegated output. "
            "Do not block waiting in-turn; continue other work and check status later. "
            "The main session can be prompted when results are ready."
        ),
    }


async def handle_check(payload: dict[str, Any]) -> dict[str, Any]:
    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToolValidationError("Field 'task_id' must be a non-empty string")

    tid = UUID(task_id.strip())
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SubAgentTask).where(SubAgentTask.id == tid))
        task = result.scalars().first()
        if task is None:
            raise ToolValidationError("Sub-agent task not found")

        result_payload = task.result if isinstance(task.result, dict) else None
        status = str(task.status)
        next_action = "Continue other work and call sub_agents with command=check again later."
        retry_recommended = False
        if status == "completed":
            next_action = (
                "Evaluate whether the delegated output fully satisfies the objective. "
                "If not, call sub_agents with command=spawn again with a refined objective/scope."
            )
            final_text = (
                result_payload.get("final_text") if isinstance(result_payload, dict) else None
            )
            if not isinstance(final_text, str) or not final_text.strip():
                retry_recommended = True
        elif status in {"failed", "cancelled"}:
            retry_recommended = True
            next_action = (
                "Retry by spawning a new sub-agent with a refined objective/scope "
                "or adjusted max_steps/timeout."
            )
        turns_used = int(task.turns_used or 0)
        max_steps = int(task.max_turns or 0)
        grace_turns_used = max(0, turns_used - max_steps)

        return {
            "task_id": str(task.id),
            "objective": task.objective,
            "status": status,
            "max_steps": max_steps,
            "turns_used": turns_used,
            "grace_turns_used": grace_turns_used,
            "tokens_used": task.tokens_used or 0,
            "result": result_payload,
            "retry_recommended": retry_recommended,
            "next_action": next_action,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "completed_at": (task.completed_at.isoformat() if task.completed_at else None),
        }


async def handle_list(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ToolValidationError("Field 'session_id' must be a non-empty string")

    sid = UUID(session_id.strip())
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
                    "max_steps": int(t.max_turns or 0),
                    "turns_used": int(t.turns_used or 0),
                    "grace_turns_used": max(0, int(t.turns_used or 0) - int(t.max_turns or 0)),
                    "tokens_used": t.tokens_used or 0,
                }
                for t in tasks
            ],
            "total": len(tasks),
        }


async def handle_cancel(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ToolValidationError("Field 'session_id' must be a non-empty string")

    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToolValidationError("Field 'task_id' must be a non-empty string")

    sid = UUID(session_id.strip())
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

        task.status = "cancelled"
        task.completed_at = datetime.now(UTC)
        current_result = task.result if isinstance(task.result, dict) else {}
        current_result = dict(current_result)
        current_result.setdefault("cancel_reason", "Cancelled by agent request")
        task.result = current_result
        await db.commit()
        await db.refresh(task)

    cancel_signal_sent = False
    orchestrator = get_sub_agent_orchestrator()
    if orchestrator is not None and hasattr(orchestrator, "cancel_task"):
        with contextlib.suppress(Exception):
            cancel_signal_sent = bool(orchestrator.cancel_task(tid))

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


def _sub_agent_command(payload: dict[str, Any]) -> str:
    raw = payload.get("command")
    if not isinstance(raw, str) or not raw.strip():
        raise ToolValidationError("Field 'command' must be a non-empty string")
    normalized = raw.strip().lower()
    if normalized not in ALLOWED_SUB_AGENT_COMMANDS:
        raise ToolValidationError(
            "Field 'command' must be one of: " + ", ".join(ALLOWED_SUB_AGENT_COMMANDS)
        )
    return normalized


async def handle_run(payload: dict[str, Any]) -> dict[str, Any]:
    command = _sub_agent_command(payload)
    if command == "spawn":
        return await handle_spawn(payload)
    if command == "check":
        return await handle_check(payload)
    if command == "list":
        return await handle_list(payload)
    return await handle_cancel(payload)
