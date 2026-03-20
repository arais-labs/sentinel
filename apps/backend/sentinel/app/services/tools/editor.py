from __future__ import annotations

import base64
import json
import os
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Session
from app.services.session_runtime import ensure_runtime_layout, runtime_workspace_dir
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolDefinition
from app.services.runtime import get_runtime

_STR_REPLACE_TIMEOUT_SECONDS = 120


def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
_STR_REPLACE_PAYLOAD_ENV = "SENTINEL_STR_REPLACE_EDITOR_PAYLOAD_B64"
_STR_REPLACE_WORKSPACE_ENV = "SENTINEL_STR_REPLACE_WORKSPACE"
_STR_REPLACE_SCRIPT = """python3 - <<'PY'
import base64
import json
import os
import pathlib
import sys


def fail(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}))
    raise SystemExit(1)


payload_b64 = os.environ.get("%s", "")
if not payload_b64:
    fail("Missing str_replace_editor payload")

try:
    payload_raw = base64.b64decode(payload_b64.encode("ascii"))
    payload = json.loads(payload_raw.decode("utf-8"))
except Exception as exc:
    fail(f"Invalid str_replace_editor payload: {exc}")

path_raw = payload.get("path")
old_str = payload.get("old_str")
new_str = payload.get("new_str")

if not isinstance(path_raw, str) or not path_raw.strip():
    fail("Field 'path' must be a non-empty string")
if not isinstance(old_str, str):
    fail("Field 'old_str' must be a string")
if not isinstance(new_str, str):
    fail("Field 'new_str' must be a string")
if old_str == "":
    fail("Field 'old_str' must be a non-empty string")

workspace = pathlib.Path(os.environ.get("%s", "/home/sentinel/workspace")).resolve()
target = (workspace / path_raw).resolve()
if target != workspace and workspace not in target.parents:
    fail(f"Path outside allowed directory: {path_raw}")
if not target.exists() or not target.is_file():
    fail(f"File not found: {path_raw}")

try:
    content = target.read_text(encoding="utf-8")
except UnicodeDecodeError:
    fail(f"File is not UTF-8 text: {path_raw}")

first = content.find(old_str)
if first < 0:
    fail(
        f"The exact string to replace was not found in {path_raw}. "
        "Check for whitespace/indentation issues."
    )

# Look for a second occurrence starting one char later to catch overlaps.
second = content.find(old_str, first + 1)
if second >= 0:
    fail(
        f"The string to replace occurs multiple times in {path_raw}. "
        "Please provide a more unique block of context."
    )

updated = content[:first] + new_str + content[first + len(old_str):]
target.write_text(updated, encoding="utf-8")
print(
    json.dumps(
        {
            "ok": True,
            "path": path_raw,
            "message": "File patched successfully",
            "old_str_count": 1,
        }
    )
)
PY
""" % (_STR_REPLACE_PAYLOAD_ENV, _STR_REPLACE_WORKSPACE_ENV)


async def _ensure_session_exists(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: UUID,
) -> None:
    async with session_factory() as db:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalars().first()
        if session is None:
            raise ToolValidationError("Session not found")


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


async def _run_str_replace_in_runtime_exec(
    *,
    session_id: UUID | str,
    workspace_dir: Any,
    path: str,
    old_str: str,
    new_str: str,
) -> dict[str, Any]:
    payload_b64 = base64.b64encode(
        json.dumps(
            {"path": path, "old_str": old_str, "new_str": new_str},
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).decode("ascii")

    runtime = await get_runtime().ensure(session_id)
    command = (
        f"export {_STR_REPLACE_PAYLOAD_ENV}={payload_b64}; "
        f"export {_STR_REPLACE_WORKSPACE_ENV}={runtime.workspace_path}; "
        f"{_STR_REPLACE_SCRIPT}"
    )

    try:
        result = await runtime.ssh.run(
            f"bash -lc {_shell_quote(command)}",
            cwd=runtime.workspace_path,
            timeout=_STR_REPLACE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        raise ToolValidationError("str_replace_editor timed out")

    stdout_text = result.stdout
    stderr_text = result.stderr
    payload = _parse_str_replace_output(stdout_text)

    if result.exit_status != 0:
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            raise ToolValidationError(error.strip())
        detail = stderr_text.strip() or stdout_text.strip() or "str_replace_editor failed"
        raise ToolValidationError(detail)

    ok = payload.get("ok")
    if ok is not True:
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            raise ToolValidationError(error.strip())
        raise ToolValidationError("str_replace_editor failed")

    return {
        "path": str(payload.get("path") or path),
        "message": str(payload.get("message") or "File patched successfully"),
        "old_str_count": int(payload.get("old_str_count") or 1),
    }


def str_replace_editor_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
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
        if old_str == "":
            raise ToolValidationError("Field 'old_str' must be a non-empty string")

        new_str = payload.get("new_str")
        if not isinstance(new_str, str):
            raise ToolValidationError("Field 'new_str' must be a string")

        await _ensure_session_exists(session_factory, session_id)
        await ensure_runtime_layout(session_id)
        workspace_dir = runtime_workspace_dir(session_id)
        return await _run_str_replace_in_runtime_exec(
            session_id=session_id,
            workspace_dir=workspace_dir,
            path=path_raw.strip(),
            old_str=old_str,
            new_str=new_str,
        )

    return ToolDefinition(
        name="str_replace_editor",
        description=(
            "Replace an exact string in a file with a new string. "
            "Runs through the same user sandbox runtime path as runtime_exec and requires a unique exact match."
        ),
        risk_level="high",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["path", "old_str", "new_str"],
            "properties": {
                "session_id": {"type": "string", "description": "Current session ID (auto-injected in agent loop)"},
                "path": {"type": "string", "description": "Path to the file relative to workspace"},
                "old_str": {"type": "string", "description": "The exact string to find in the file (must be unique)"},
                "new_str": {"type": "string", "description": "The replacement string"},
            },
        },
        execute=_execute,
    )
