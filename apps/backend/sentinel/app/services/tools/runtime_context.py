from __future__ import annotations

from uuid import UUID

from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolRuntimeContext


def optional_session_id(runtime: ToolRuntimeContext | None) -> UUID | None:
    if runtime is None:
        return None
    return runtime.session_id


def require_session_id(runtime: ToolRuntimeContext | None) -> UUID:
    session_id = optional_session_id(runtime)
    if session_id is None:
        raise ToolValidationError("Missing runtime session context")
    return session_id
