"""Tell the agent that started preparing a workspace when it is ready or failed.

A first install takes 10-30 minutes and nothing pushes a workspace's state to the backend, so
one task per watched workspace reads the same status the Workspaces screen polls (this Mac's
desktop runtime or a remote Mac's worker), sparingly, until it settles. The report then reaches
the agent like a finished background job: a notice through the steering inbox that wakes an
idle agent. Only preparations an agent started through its `workspaces` tool are watched.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.models import Session
from app.services.sessions.job_reports import deliver_report
from app.services.workspaces import lifecycle

logger = logging.getLogger(__name__)

POLL_SECONDS = 15.0
LIMIT_SECONDS = 3 * 3600.0
STATUS_TIMEOUT_SECONDS = 30.0

# States on the way to the end of a preparation or a recovery.
_IN_PROGRESS = {"preparing", "recovering", "stopping"}

# (session, workspace) -> the task following that workspace for that agent.
_watches: dict[tuple[str, str], asyncio.Task] = {}


def watch(*, session_id, workspace_id, name: str, db_factory, run_registry) -> bool:
    """Follows a preparation for `session_id`'s agent; False when one is already followed."""
    key = (str(session_id), str(workspace_id))
    if key in _watches:
        return False
    task = asyncio.create_task(
        _follow(session_id, workspace_id, name, uuid4().hex, db_factory, run_registry)
    )
    _watches[key] = task
    task.add_done_callback(
        lambda done: _watches.pop(key, None) if _watches.get(key) is done else None
    )
    return True


async def close() -> None:
    tasks = list(_watches.values())
    _watches.clear()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _state(workspace_id) -> dict:
    try:
        states = await asyncio.wait_for(
            lifecycle.statuses(workspace_id), timeout=STATUS_TIMEOUT_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 - an unreachable runtime is a state too
        return {"state": "unavailable", "error": str(exc) or type(exc).__name__}
    return states.get(str(workspace_id)) or {"state": "stopped"}


async def settle(workspace_id) -> dict:
    """The workspace's state once it settles: running at once; anything else only when two
    reads in a row agree, so a slow remote Mac or a stale state is not taken for the end."""
    previous = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LIMIT_SECONDS
    while loop.time() < deadline:
        await asyncio.sleep(POLL_SECONDS)
        state = await _state(workspace_id)
        current = state.get("state")
        if current == "running" or (current not in _IN_PROGRESS and current == previous):
            return state
        previous = current
    return {"state": "preparing", "timed_out": True}


def report_text(name: str, workspace_id, state: dict) -> str:
    current = state.get("state") or "unknown"
    lines = [
        "[Workspace Report]",
        "A workspace you started preparing or recovering has settled.",
        "Finish the current step, then continue the task with it.",
        "",
        f"Workspace: {name}",
        f"Workspace ID: {workspace_id}",
        f"State: {current}",
    ]
    if state.get("timed_out"):
        lines.append(
            f"Still preparing after {LIMIT_SECONDS / 3600:g} hours; check it with workspaces.list "
            "and workspaces.log."
        )
    elif current == "stopped" and not state.get("error"):
        lines.append("It is stopped with its disk kept: start it to use it.")
    elif current != "running":
        if state.get("error"):
            lines.extend(["", "Error:", str(state["error"]).strip()])
        lines.extend(
            [
                "",
                "Call workspaces.log with this workspace_id for its consoles (console, build) to "
                "see why. A failed install: fix the distribution, then workspaces.reinstall this "
                "same workspace.",
            ]
        )
        if state.get("recovery_available"):
            lines.append(
                "It needs recovery: workspaces.recover keeps its disk; reinstall erases it."
            )
    return "\n".join(lines).strip()


async def deliver(db, session, workspace_id, name, preparation, state, *, run_registry, wake):
    message_id = uuid5(
        NAMESPACE_URL, f"sentinel:workspace-report:{session.id}:{workspace_id}:{preparation}"
    )
    return await deliver_report(
        db,
        session,
        message_id,
        report_text(name, workspace_id, state),
        {
            "source": "workspace_preparation",
            "workspace_id": str(workspace_id),
            "status": str(state.get("state") or ""),
        },
        title="Workspace report",
        run_registry=run_registry,
        wake=wake,
    )


async def _follow(session_id, workspace_id, name, preparation, db_factory, run_registry):
    try:
        state = await settle(workspace_id)
        async with db_factory() as db:
            session = await db.get(Session, UUID(str(session_id)))
            if session is None:
                return
            # As for job reports: a finished sub-agent reads it if resumed, it is not woken.
            wake = session.parent_session_id is None or await run_registry.is_running(
                str(session.id)
            )
            await deliver(
                db,
                session,
                workspace_id,
                name,
                preparation,
                state,
                run_registry=run_registry,
                wake=wake,
            )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a lost report must not take anything else down
        logger.warning("workspace report for %s failed", workspace_id, exc_info=True)
