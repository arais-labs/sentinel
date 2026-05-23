from __future__ import annotations

from typing import Any

from app.services.runtime.port_forwards import RuntimeForwardError, RuntimeForwardNotFound
from app.services.runtime.ssh_runtime import get_runtime_port_forward_manager, runtime_configured
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.runtime_context import require_runtime_session_id


def _runtime_session_key(runtime: ToolRuntimeContext) -> str:
    return str(require_runtime_session_id(runtime))


async def handle_open(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    if not await runtime_configured(instance_name=runtime.instance_name, session_factory=runtime.db_session_factory):
        raise ToolValidationError("Runtime SSH target is not configured.")
    label = payload.get("label")
    if label is not None and not isinstance(label, str):
        raise ToolValidationError("Field 'label' must be a string.")
    try:
        forwards = await get_runtime_port_forward_manager(instance_name=runtime.instance_name, session_factory=runtime.db_session_factory)
        forward = await forwards.open_forward(
            session_id=_runtime_session_key(runtime),
            target_host=str(payload.get("host") or "127.0.0.1"),
            target_port=payload.get("port"),
            protocol=str(payload.get("protocol") or "http"),
            label=label,
        )
    except RuntimeForwardError as exc:
        raise ToolValidationError(str(exc)) from exc
    return forward.to_dict()


async def handle_list(_payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    if not await runtime_configured(instance_name=runtime.instance_name, session_factory=runtime.db_session_factory):
        raise ToolValidationError("Runtime SSH target is not configured.")
    session_id = _runtime_session_key(runtime)
    manager = await get_runtime_port_forward_manager(instance_name=runtime.instance_name, session_factory=runtime.db_session_factory)
    forwards = await manager.list_forwards(session_id=session_id)
    return {
        "session_id": session_id,
        "forwards": [item.to_dict() for item in forwards],
    }


async def handle_close(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    if not await runtime_configured(instance_name=runtime.instance_name, session_factory=runtime.db_session_factory):
        raise ToolValidationError("Runtime SSH target is not configured.")
    forward_id = payload.get("forward_id")
    if not isinstance(forward_id, str) or not forward_id.strip():
        raise ToolValidationError("Field 'forward_id' must be a non-empty string.")
    try:
        forwards = await get_runtime_port_forward_manager(instance_name=runtime.instance_name, session_factory=runtime.db_session_factory)
        forward = await forwards.close_forward(
            session_id=_runtime_session_key(runtime),
            forward_id=forward_id.strip(),
        )
    except RuntimeForwardNotFound as exc:
        raise ToolValidationError("Runtime forward not found.") from exc
    return forward.to_dict()
