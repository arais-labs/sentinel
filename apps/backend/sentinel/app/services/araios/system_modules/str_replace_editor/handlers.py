"""Native module: str_replace_editor — exact string replacement in runtime files."""

from __future__ import annotations

import json
from typing import Any

from app.services.runtime.files import (
    RuntimePathInvalidError,
    RuntimePathIsDirectoryError,
    RuntimePathNotFoundError,
)
from app.services.runtime.ssh_runtime import get_runtime_workspace_files, runtime_configured
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.runtime_context import require_runtime_session_id


def _parse_str_replace_output(stdout_text: str) -> dict[str, Any]:
    lines = [line.strip() for line in stdout_text.splitlines() if line.strip()]
    if not lines:
        raise ToolValidationError("str_replace_editor produced no output")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ToolValidationError("str_replace_editor returned invalid response payload") from exc
    if not isinstance(payload, dict):
        raise ToolValidationError("str_replace_editor returned invalid response payload")
    return payload


def _string_field(payload: dict[str, Any], key: str, *, required: bool = False) -> str:
    value = payload.get(key)
    if value is None:
        if required:
            raise ToolValidationError(f"Field '{key}' is required.")
        return ""
    if not isinstance(value, str):
        raise ToolValidationError(f"Field '{key}' must be a string.")
    if required and not value.strip():
        raise ToolValidationError(f"Field '{key}' must be a non-empty string.")
    return value


async def handle_edit(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    if not runtime_configured():
        raise ToolValidationError("Runtime SSH target is not configured.")

    session_id = require_runtime_session_id(runtime)
    path = _string_field(payload, "path", required=True).strip()
    old_str = _string_field(payload, "old_str")
    if old_str == "":
        raise ToolValidationError("Field 'old_str' must be a non-empty string.")
    new_str = _string_field(payload, "new_str")

    try:
        return await get_runtime_workspace_files().str_replace(
            str(session_id),
            path=path,
            old_str=old_str,
            new_str=new_str,
        )
    except RuntimePathNotFoundError as exc:
        raise ToolValidationError(str(exc) or "Runtime file not found.") from exc
    except RuntimePathIsDirectoryError as exc:
        raise ToolValidationError(str(exc) or "Runtime path is a directory.") from exc
    except RuntimePathInvalidError as exc:
        raise ToolValidationError(str(exc) or "Invalid runtime path.") from exc
