"""Runtime actions: sandboxed execution and native tmux targets."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.models import Session, Workspace
from app.services.modules.runtime_services import get_ws_manager, notify_runtime_job_completed
from app.services.runtime.panes import FOREGROUND_WAIT_SECONDS, TmuxPanes
from app.services.runtime.ssh_runtime import get_runtime_terminal_manager
from sentral.errors import ToolValidationError


def _session_key(runtime):
    value = runtime.runtime_session_id or runtime.session_id
    if value is None:
        raise ToolValidationError("Runtime requires an active session")
    return str(value)


async def handle_tmux(payload, runtime):
    session_id = _session_key(runtime)
    manager = await get_runtime_terminal_manager(
        session_id=session_id,
        instance_name=runtime.instance_name,
        session_factory=runtime.db_session_factory,
    )
    bridge = TmuxPanes(manager)
    action = payload.pop("_operation")

    async def publish():
        ws = get_ws_manager()
        if ws:
            await ws.broadcast(
                session_id,
                {
                    "type": "panes_changed",
                    "panes": [p for w in await bridge.tree(session_id) for p in w["panes"]],
                },
            )

    try:
        if action == "terminal_list":
            return {"windows": await bridge.tree(session_id)}
        if action == "window_create":
            return await bridge.create_window(session_id, payload.get("name"))
        if action == "window_rename":
            await bridge.rename_window(session_id, payload["window_id"], payload["name"])
        elif action == "pane_rename":
            await bridge.rename_pane(session_id, payload["pane_id"], payload["title"])
        elif action == "window_close":
            await bridge.close_window(session_id, payload["window_id"])
        elif action == "pane_split":
            return await bridge.split(
                session_id,
                payload["pane_id"],
                payload.get("direction", "horizontal"),
                payload.get("title"),
            )
        elif action == "pane_close":
            await bridge.close_pane(session_id, payload["pane_id"])
        elif action == "pane_read":
            return {
                "pane_id": payload["pane_id"],
                "output": await bridge.read(
                    session_id, payload["pane_id"], payload.get("lines", 100)
                ),
            }
        elif action == "pane_input":
            await bridge.input(
                session_id,
                payload["pane_id"],
                payload.get("text", ""),
                payload.get("key"),
            )
        elif action == "exec":

            async def completed(job, stdout, stderr):
                try:
                    await publish()
                finally:
                    # A failed UI refresh must not suppress the agent's report.
                    await notify_runtime_job_completed(
                        session_id,
                        job,
                        stdout_tail=stdout[-8000:],
                        stderr_tail=stderr[-8000:],
                    )

            return await bridge.execute(
                session_id,
                payload["shell_command"],
                pane_id=payload.get("pane_id"),
                cwd=payload.get("cwd"),
                env=payload.get("env"),
                timeout=payload.get("timeout_seconds", FOREGROUND_WAIT_SECONDS),
                background=payload.get("background", False),
                on_complete=completed,
            )
        return {"ok": True}
    except (ValueError, RuntimeError) as exc:
        raise ToolValidationError(str(exc)) from exc
    finally:
        if action != "terminal_list":
            await publish()


def action_handler(operation):
    async def handler(payload, runtime):
        return await handle_tmux({**payload, "_operation": operation}, runtime)

    return handler


async def handle_workspace(payload: dict, runtime) -> dict:
    """Describe the attachment without starting a shell or choosing a workspace."""

    session_id = _session_key(runtime)
    if runtime.db_session_factory is None:
        raise ToolValidationError("Workspace lookup requires a session database.")
    async with runtime.db_session_factory() as db:
        session = await db.get(Session, UUID(session_id))
        while session and session.parent_session_id:
            session = await db.get(Session, session.parent_session_id)
        row = (
            await db.get(Workspace, session.workspace_id)
            if session and session.workspace_id
            else None
        )
        available = list((await db.scalars(select(Workspace).order_by(Workspace.name))).all())

    def describe(value):
        return {
            "id": str(value.id),
            "name": value.name,
            "machine_id": str(value.machine_id),
            "directory": value.directory,
        }

    return {
        "attached": row is not None,
        "workspace": describe(row) if row else None,
        "available_workspaces": [describe(value) for value in available],
        "guidance": "Workspaces share files across chats; each chat has its own terminal state. Ask the user to use Attach in the session toolbar when a workspace is needed. Never assume or automatically attach a workspace.",
    }
