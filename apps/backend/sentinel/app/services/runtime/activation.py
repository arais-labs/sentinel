from __future__ import annotations

import asyncio
import logging
from collections.abc import MutableMapping

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _runtime_activation_tasks(app: FastAPI) -> MutableMapping[str, asyncio.Task[None]]:
    tasks = getattr(app.state, "runtime_activation_tasks", None)
    if isinstance(tasks, MutableMapping):
        return tasks
    tasks = {}
    app.state.runtime_activation_tasks = tasks
    return tasks


def queue_runtime_activation(app: FastAPI, session_id: str) -> bool:
    tasks = _runtime_activation_tasks(app)
    existing = tasks.get(session_id)
    if existing is not None and not existing.done():
        return False

    task = asyncio.create_task(_activate_runtime_session_task(app, session_id))
    tasks[session_id] = task

    def _cleanup(done_task: asyncio.Task[None]) -> None:
        current = tasks.get(session_id)
        if current is done_task:
            tasks.pop(session_id, None)

    task.add_done_callback(_cleanup)
    return True


async def _activate_runtime_session_task(app: FastAPI, session_id: str) -> None:
    from app.services.runtime import get_runtime

    ws = getattr(app.state, "ws_manager", None)

    try:
        provider = get_runtime()
        runtime = await provider.activate_session(session_id)
        logger.info("Runtime activated for session %s", session_id)
        if ws is not None and hasattr(ws, "broadcast_runtime_ready"):
            await ws.broadcast_runtime_ready(session_id)
        activation_state = getattr(app.state, "runtime_activation_state", None)
        if isinstance(activation_state, dict):
            activation_state[session_id] = runtime.host
    except Exception:
        logger.warning("Runtime activation failed for session %s", session_id, exc_info=True)
