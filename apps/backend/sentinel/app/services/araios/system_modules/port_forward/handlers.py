from __future__ import annotations

from typing import Any
from app.services.runtime.port_forwards import (
    close_runtime_forward,
    ensure_runtime_forward,
    list_runtime_forwards,
    serialize_forward,
)
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.runtime_context import require_runtime_session_id, require_session_id


async def handle_open(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    runtime_session_id = require_runtime_session_id(runtime)
    created_session_id = require_session_id(runtime)
    target_port = payload.get("port")
    if not isinstance(target_port, int) or isinstance(target_port, bool) or not (1 <= target_port <= 65535):
        raise ToolValidationError("Field 'port' must be an integer between 1 and 65535")
    target_host = payload.get("host", "127.0.0.1")
    if not isinstance(target_host, str) or not target_host.strip():
        raise ToolValidationError("Field 'host' must be a non-empty string")
    protocol = payload.get("protocol", "http")
    if not isinstance(protocol, str) or protocol.strip().lower() not in {"http", "tcp"}:
        raise ToolValidationError("Field 'protocol' must be either 'http' or 'tcp'")
    label = payload.get("label")
    if label is not None and not isinstance(label, str):
        raise ToolValidationError("Field 'label' must be a string")

    forward = await ensure_runtime_forward(
        runtime_session_id=runtime_session_id,
        created_session_id=created_session_id,
        target_host=target_host.strip(),
        target_port=target_port,
        protocol=protocol.strip().lower(),
        label=label.strip() if isinstance(label, str) and label.strip() else None,
    )
    payload = serialize_forward(forward)
    return {
        "forward_id": payload.forward_id,
        "status": payload.status,
        "url": payload.url,
        "host": payload.host,
        "host_port": payload.host_port,
        "label": payload.label,
        "port": payload.target_port,
        "relay_port": payload.relay_port,
        "protocol": payload.protocol,
        "runtime_session_id": str(payload.runtime_session_id),
    }


async def handle_list(_payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    runtime_session_id = require_runtime_session_id(runtime)
    items = await list_runtime_forwards(runtime_session_id=runtime_session_id)
    forwards = [serialize_forward(item) for item in items]
    return {
        "runtime_session_id": str(runtime_session_id),
        "forwards": [
            {
                "forward_id": item.forward_id,
                "status": item.status,
                "url": item.url,
                "host": item.host,
                "host_port": item.host_port,
                "label": item.label,
                "port": item.target_port,
                "relay_port": item.relay_port,
                "protocol": item.protocol,
            }
            for item in forwards
        ],
    }


async def handle_close(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    runtime_session_id = require_runtime_session_id(runtime)
    forward_id = payload.get("forward_id")
    if not isinstance(forward_id, str) or not forward_id.strip():
        raise ToolValidationError("Field 'forward_id' must be a non-empty string")
    closed = await close_runtime_forward(
        runtime_session_id=runtime_session_id,
        forward_id=forward_id.strip(),
    )
    if closed is None:
        raise ToolValidationError("Runtime forward not found")
    payload = serialize_forward(closed)
    return {
        "forward_id": payload.forward_id,
        "status": payload.status,
        "url": payload.url,
        "host": payload.host,
        "host_port": payload.host_port,
        "closed_at": payload.closed_at.isoformat() if payload.closed_at is not None else None,
    }
