from __future__ import annotations
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Session
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolDefinition
from app.services.session_runtime import ensure_runtime_layout, runtime_workspace_dir


async def _ensure_session_exists(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: UUID,
) -> None:
    async with session_factory() as db:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalars().first()
        if session is None:
            raise ToolValidationError("Session not found")


def str_replace_editor_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        session_id_raw = payload.get("session_id")
        if not isinstance(session_id_raw, str) or not session_id_raw.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")
        try:
            session_id = UUID(session_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'session_id' must be a valid UUID string") from exc

        path_raw = payload.get("path")
        if not isinstance(path_raw, str) or not path_raw.strip():
            raise ToolValidationError("Field 'path' must be a non-empty string")
        
        old_str = payload.get("old_str")
        if not isinstance(old_str, str):
            raise ToolValidationError("Field 'old_str' must be a string")
            
        new_str = payload.get("new_str")
        if not isinstance(new_str, str):
            raise ToolValidationError("Field 'new_str' must be a string")

        if session_factory is not None:
            await _ensure_session_exists(session_factory, session_id)
            await ensure_runtime_layout(session_id)

        workspace_dir = runtime_workspace_dir(session_id)
        path = (workspace_dir / path_raw).resolve()
        
        if not path.is_relative_to(workspace_dir):
            raise ToolValidationError(f"Path outside allowed directory: {workspace_dir}")

        if not path.exists() or not path.is_file():
            raise ToolValidationError(f"File not found: {path_raw}")

        content = path.read_text(encoding="utf-8")
        
        count = content.count(old_str)
        if count == 0:
            raise ToolValidationError(f"The exact string to replace was not found in {path_raw}. Check for whitespace/indentation issues.")
        if count > 1:
            raise ToolValidationError(f"The string to replace occurs {count} times in {path_raw}. Please provide a more unique block of context.")

        new_content = content.replace(old_str, new_str)
        path.write_text(new_content, encoding="utf-8")

        return {
            "path": path_raw,
            "message": "File patched successfully",
            "old_str_count": 1
        }

    return ToolDefinition(
        name="str_replace_editor",
        description="Replace an exact string in a file with a new string. Requires an exact, unique match for the 'old_str'.",
        risk_level="high",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["path", "old_str", "new_str"],
            "properties": {
                "session_id": {"type": "string", "description": "Current session ID"},
                "path": {"type": "string", "description": "Path to the file relative to workspace"},
                "old_str": {"type": "string", "description": "The exact string to find in the file"},
                "new_str": {"type": "string", "description": "The string to replace it with"}
            },
        },
        execute=_execute,
    )
