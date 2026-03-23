from __future__ import annotations

from typing import Any

from app.services.araios.runtime_services import get_browser_pool
from app.services.tools.executor import ToolValidationError


BROWSER_SESSION_PROP = {
    "session_id": {"type": "string", "description": "The agent session UUID"},
}

def optional_browser_tab_id(payload: dict[str, Any]) -> str | None:
    tab_id = payload.get("tab_id")
    if tab_id is None:
        return None
    if not isinstance(tab_id, str) or not tab_id.strip():
        raise ToolValidationError("Field 'tab_id' must be a non-empty string")
    return tab_id.strip()


async def resolve_browser_manager(payload: dict[str, Any]):
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ToolValidationError("Field 'session_id' must be a non-empty string")
    return await get_browser_pool().get(session_id.strip())


def extract_browser_tab_constraint(constraints: Any) -> str | None:
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
