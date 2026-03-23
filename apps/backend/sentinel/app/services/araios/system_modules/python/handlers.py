"""Native module: python — run Python code in runtime container."""

from __future__ import annotations

import base64
import json
import re
from typing import Any
from uuid import UUID

from app.database.database import AsyncSessionLocal
from app.services.runtime import get_runtime
from app.services.runtime.session_runtime import ensure_runtime_layout
from app.services.tools.executor import ToolValidationError

# ---------------------------------------------------------------------------
# Constants (moved from builtin.py)
# ---------------------------------------------------------------------------

_PYTHON_PAYLOAD_ENV = "SENTINEL_PYTHON_PAYLOAD_B64"
_PYTHON_WORKSPACE_ENV = "SENTINEL_PYTHON_WORKSPACE"
_PYTHON_VENV_ENV = "SENTINEL_PYTHON_VENV_PATH"
_VENV_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
_PYTHON_EXEC_SCRIPT = """\
set -e
_VENV="$%s"
if [ ! -f "$_VENV/bin/python" ]; then
    mkdir -p "$(dirname "$_VENV")"
    python3 -m venv "$_VENV"
fi
"$_VENV/bin/python" - <<'__SENTINEL_PYEOF__'
import base64, contextlib, io, json, os, subprocess, sys, traceback

_MAX_CHARS = 20000


def _trunc(s):
    if not s:
        return ""
    return s[:_MAX_CHARS] + "\\n...[truncated]" if len(s) > _MAX_CHARS else s


def _fail(msg):
    print(json.dumps({"ok": False, "error": msg, "stdout": "", "stderr": "", "exception": None, "result": None, "result_repr": None}))
    sys.exit(1)


_payload_b64 = os.environ.get("%s", "")
if not _payload_b64:
    _fail("Missing python payload")

try:
    _payload = json.loads(base64.b64decode(_payload_b64.encode("ascii")).decode("utf-8"))
except Exception as _exc:
    _fail(f"Invalid payload: {_exc}")

_code = _payload.get("code", "")
_requirements = _payload.get("requirements") or []
_workspace = os.environ.get("%s", "/home/sentinel/workspace")

if _requirements:
    _pip = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", "--no-input"] + _requirements,
        capture_output=True,
        text=True,
    )
    if _pip.returncode != 0:
        _fail(f"pip install failed: {_trunc(_pip.stderr or _pip.stdout)}")

os.chdir(_workspace)
_stdout_buf = io.StringIO()
_stderr_buf = io.StringIO()
_exception_text = None
_globals_map = {"__name__": "__main__"}

with contextlib.redirect_stdout(_stdout_buf), contextlib.redirect_stderr(_stderr_buf):
    try:
        exec(compile(_code, "<python>", "exec"), _globals_map, _globals_map)
    except Exception:
        _exception_text = traceback.format_exc()

_result_value = _globals_map.get("result")


def _to_json_or_repr(v):
    if v is None:
        return None, None
    try:
        json.dumps(v)
        return v, None
    except TypeError:
        return None, repr(v)


_result_json, _result_repr = _to_json_or_repr(_result_value)
print(
    json.dumps({
        "ok": _exception_text is None,
        "stdout": _trunc(_stdout_buf.getvalue()),
        "stderr": _trunc(_stderr_buf.getvalue()),
        "exception": _trunc(_exception_text),
        "result": _result_json,
        "result_repr": _result_repr,
    })
)
__SENTINEL_PYEOF__
""" % (_PYTHON_VENV_ENV, _PYTHON_PAYLOAD_ENV, _PYTHON_WORKSPACE_ENV)

# ---------------------------------------------------------------------------
# Internal helpers (moved from builtin.py)
# ---------------------------------------------------------------------------


def _ssh_shell_quote(s: str) -> str:
    """POSIX single-quoting for SSH commands."""
    return "'" + s.replace("'", "'\\''") + "'"


def _validate_python_venv_name(name: Any) -> str | None:
    if name is None:
        return None
    if not isinstance(name, str) or not name.strip():
        raise ToolValidationError("Field 'venv_name' must be a non-empty string when provided")
    cleaned = name.strip()
    if not _VENV_NAME_RE.match(cleaned):
        raise ToolValidationError(
            "Field 'venv_name' must start with a letter and contain only letters, digits, hyphens, and underscores"
        )
    return cleaned


def _python_venv_container_path(workspace_path: str, venv_name: str | None) -> str:
    base = f"{workspace_path}/.venvs"
    if not venv_name:
        return f"{base}/default"
    return f"{base}/{venv_name}"


def _parse_python_output(stdout_text: str) -> dict[str, Any]:
    lines = [line.strip() for line in stdout_text.splitlines() if line.strip()]
    if not lines:
        raise ToolValidationError("python tool produced no output")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ToolValidationError("python tool returned invalid response") from exc
    if not isinstance(payload, dict):
        raise ToolValidationError("python tool returned invalid response")
    return payload


async def _run_python_in_runtime(
    *,
    session_id: UUID | str,
    code: str,
    requirements: list[str],
    venv_name: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    runtime = await get_runtime().ensure(session_id)
    workspace = runtime.workspace_path
    venv_path = _python_venv_container_path(workspace, venv_name)

    payload_b64 = base64.b64encode(
        json.dumps(
            {"code": code, "requirements": requirements},
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).decode("ascii")

    command = (
        f"export {_PYTHON_VENV_ENV}={venv_path}; "
        f"export {_PYTHON_PAYLOAD_ENV}={payload_b64}; "
        f"export {_PYTHON_WORKSPACE_ENV}={workspace}; "
        f"{_PYTHON_EXEC_SCRIPT}"
    )

    try:
        result = await runtime.ssh.run(
            f"bash -lc {_ssh_shell_quote(command)}",
            cwd=workspace,
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        raise ToolValidationError(
            f"python tool timed out after {timeout_seconds}s"
        ) from exc

    payload = _parse_python_output(result.stdout)

    if result.exit_status != 0 or payload.get("error"):
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            raise ToolValidationError(error.strip())
        detail = result.stderr.strip() or result.stdout.strip() or "python tool failed"
        raise ToolValidationError(detail)

    return {
        "ok": bool(payload.get("ok")),
        "stdout": str(payload.get("stdout") or ""),
        "stderr": str(payload.get("stderr") or ""),
        "exception": payload.get("exception"),
        "result": payload.get("result"),
        "result_repr": payload.get("result_repr"),
        "workspace": workspace,
        "venv": venv_path,
    }


async def _ensure_session_exists(session_id: UUID) -> None:
    from sqlalchemy import select as sa_select
    from app.models import Session

    async with AsyncSessionLocal() as db:
        result = await db.execute(sa_select(Session).where(Session.id == session_id))
        session = result.scalars().first()
        if session is None:
            raise ToolValidationError("Session not found")


# ---------------------------------------------------------------------------
# Handler functions (module-level)
# ---------------------------------------------------------------------------


async def handle_run(payload: dict[str, Any]) -> dict[str, Any]:
    session_id_raw = payload.get("session_id")
    if not isinstance(session_id_raw, str) or not session_id_raw.strip():
        raise ToolValidationError("Field 'session_id' must be a non-empty string")
    try:
        session_id = UUID(session_id_raw.strip())
    except ValueError as exc:
        raise ToolValidationError("Field 'session_id' must be a valid UUID string") from exc

    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        raise ToolValidationError("Field 'code' must be a non-empty string")

    timeout_seconds = payload.get("timeout_seconds", 60)
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds < 1
    ):
        raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")
    timeout_seconds = min(timeout_seconds, 600)

    requirements = payload.get("requirements", [])
    if requirements is None:
        requirements = []
    if not isinstance(requirements, list):
        raise ToolValidationError("Field 'requirements' must be an array of strings")
    normalized_requirements = [
        item.strip() for item in requirements if isinstance(item, str) and item.strip()
    ]
    if len(normalized_requirements) > 20:
        raise ToolValidationError("At most 20 requirement entries are allowed")

    venv_name = _validate_python_venv_name(payload.get("venv_name"))

    await _ensure_session_exists(session_id)
    await ensure_runtime_layout(session_id)

    return await _run_python_in_runtime(
        session_id=session_id,
        code=code,
        requirements=normalized_requirements,
        venv_name=venv_name,
        timeout_seconds=timeout_seconds,
    )


# ---------------------------------------------------------------------------
# MODULE definition
# ---------------------------------------------------------------------------
