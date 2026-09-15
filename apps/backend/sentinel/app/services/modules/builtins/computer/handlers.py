"""Computer actions are always routed to the current session's workspace."""

from app.services.runtime.ssh_runtime import get_runtime_desktop_manager
from sentral.errors import ToolValidationError
from app.services.tools.runtime_context import require_runtime_session_id


async def manager_for(runtime):
    session_id = str(require_runtime_session_id(runtime))
    manager = await get_runtime_desktop_manager(
        session_id=session_id,
        instance_name=runtime.instance_name,
        session_factory=runtime.db_session_factory,
    )
    return session_id, manager


async def handle_status(payload, runtime):
    _, manager = await manager_for(runtime)
    return await manager.status()


async def handle_screenshot(payload, runtime):
    session_id, manager = await manager_for(runtime)
    return await manager.computer(session_id, {"actions": []})


async def handle_perform(payload, runtime):
    if not isinstance(payload.get("actions"), list) or not 1 <= len(payload["actions"]) <= 16:
        raise ToolValidationError("Supply 1–16 ordered computer actions")
    session_id, manager = await manager_for(runtime)
    return await manager.computer(
        session_id, {"actions": payload["actions"], "viewport": payload.get("viewport")}
    )
