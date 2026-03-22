from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import ipaddress
import json
import os
import re
import signal
import shlex
import socket
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models import Memory, Session, SubAgentTask
from app.services.approvals.tool_match import build_runtime_exec_match_key
from app.services.embeddings import EmbeddingService
from app.services.memory import (
    InvalidMemoryOperationError,
    MemoryNotFoundError,
    MemoryRepository,
    MemoryService,
    ParentMemoryNotFoundError,
)
from app.services.memory.search import MemorySearchService
from app.services.session_runtime import (
    finalize_detached_runtime_job,
    get_detached_runtime_job,
    list_detached_runtime_jobs,
    read_detached_runtime_job_logs,
    register_detached_runtime_job,
    ensure_runtime_layout,
    mark_runtime_state,
    runtime_logs_dir,
    stop_detached_runtime_job,
    runtime_workspace_dir,
)
from app.services.tools.approval_waiters import build_tool_db_approval_waiter
from app.services.tools.browser_pool import BrowserPool
from app.services.tools.browser_tool import BrowserManager
from app.services.tools.editor import str_replace_editor_tool
from app.services.tools.executor import ToolValidationError
from app.services.runtime import get_runtime
from app.services.runtime.ssh_client import SSHExecResult
from app.services.tools.registry import (
    ToolApprovalEvaluation,
    ToolApprovalGate,
    ToolApprovalMode,
    ToolApprovalRequirement,
    ToolDefinition,
    ToolRegistry,
)
from app.services.tools.trigger_tools import (
    trigger_create_tool,
    trigger_delete_tool,
    trigger_list_tool,
    trigger_update_tool,
)
from app.services.tools.git_accounts_available import git_accounts_available_tool
from app.services.tools.git_exec import git_exec_tool

_MAX_HTTP_RESPONSE_BYTES = 1_048_576
_ALLOWED_MEMORY_CATEGORIES = {"core", "preference", "project", "correction"}
_MAX_RUNTIME_EXEC_OUTPUT_CHARS = 50_000
_RUNTIME_EXEC_STREAM_READ_BYTES = 65_536
_RUNTIME_EXEC_STREAM_DRAIN_SECONDS = 2.0
_RUNTIME_EXEC_TIMEOUT_KILL_WAIT_SECONDS = 3.0
_RUNTIME_BACKGROUND_AMPERSAND_RE = re.compile(r"(?<!&)&(?!&)")
_DEFAULT_RUNTIME_ROOT_APPROVAL_TIMEOUT_SECONDS = 600
_MAX_RUNTIME_ROOT_APPROVAL_TIMEOUT_SECONDS = 3600

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


def build_default_registry(
    *,
    memory_search_service: MemorySearchService | None = None,
    embedding_service: EmbeddingService | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    browser_pool: BrowserPool | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    pool = browser_pool or BrowserPool()
    registry.register(_file_read_tool())
    registry.register(_http_request_tool())
    if session_factory is not None:
        registry.register(str_replace_editor_tool(session_factory=session_factory))
        registry.register(git_accounts_available_tool(session_factory=session_factory))
        registry.register(_runtime_exec_tool(session_factory=session_factory))
        registry.register(_runtime_jobs_list_tool(session_factory=session_factory))
        registry.register(_runtime_job_status_tool(session_factory=session_factory))
        registry.register(_runtime_job_logs_tool(session_factory=session_factory))
        registry.register(_runtime_job_stop_tool(session_factory=session_factory))
        registry.register(git_exec_tool(session_factory=session_factory))
    registry.register(_browser_navigate_tool(pool))
    registry.register(_browser_screenshot_tool(pool))
    registry.register(_browser_click_tool(pool))
    registry.register(_browser_type_tool(pool))
    registry.register(_browser_select_tool(pool))
    registry.register(_browser_wait_for_tool(pool))
    registry.register(_browser_get_value_tool(pool))
    registry.register(_browser_fill_form_tool(pool))
    registry.register(_browser_press_key_tool(pool))
    registry.register(_browser_scroll_tool(pool))
    registry.register(_browser_get_text_tool(pool))
    registry.register(_browser_snapshot_tool(pool))
    registry.register(_browser_reset_tool(pool))
    registry.register(_browser_tabs_tool(pool))
    registry.register(_browser_tab_open_tool(pool))
    registry.register(_browser_tab_focus_tool(pool))
    registry.register(_browser_tab_close_tool(pool))
    registry.register(_browser_evaluate_tool(pool))
    registry.register(_browser_get_html_tool(pool))
    registry.register(_browser_get_cookies_tool(pool))
    registry.register(_browser_set_cookies_tool(pool))
    registry.register(_browser_console_logs_tool(pool))
    registry.register(_browser_network_intercept_tool(pool))
    registry.register(_browser_network_logs_tool(pool))
    registry.register(_browser_clear_network_intercepts_tool(pool))

    if session_factory is not None:
        from app.services.tools.araios_tools import (
            araios_modules_tool,
            araios_records_tool,
            araios_action_tool,
        )
        registry.register(araios_modules_tool(session_factory=session_factory))
        registry.register(araios_records_tool(session_factory=session_factory))
        registry.register(araios_action_tool(session_factory=session_factory))
        registry.register(
            _memory_store_tool(session_factory=session_factory, embedding_service=embedding_service)
        )
        registry.register(_memory_roots_tool(session_factory=session_factory))
        registry.register(_memory_tree_tool(session_factory=session_factory))
        registry.register(_memory_get_node_tool(session_factory=session_factory))
        registry.register(_memory_list_children_tool(session_factory=session_factory))
        registry.register(
            _memory_update_tool(
                session_factory=session_factory, embedding_service=embedding_service
            )
        )
        registry.register(_memory_move_tool(session_factory=session_factory))
        registry.register(_memory_touch_tool(session_factory=session_factory))
        registry.register(_memory_delete_tool(session_factory=session_factory))
        registry.register(trigger_create_tool(session_factory=session_factory))
        registry.register(trigger_list_tool(session_factory=session_factory))
        registry.register(trigger_update_tool(session_factory=session_factory))
        registry.register(trigger_delete_tool(session_factory=session_factory))
    if session_factory is not None and memory_search_service is not None:
        registry.register(
            _memory_search_tool(
                session_factory=session_factory,
                memory_search_service=memory_search_service,
            )
        )

    return registry


def _file_read_tool() -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        path_raw = payload.get("path")
        if not isinstance(path_raw, str) or not path_raw.strip():
            raise ToolValidationError("Field 'path' must be a non-empty string")
        max_bytes = payload.get("max_bytes", 4096)
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            raise ToolValidationError("Field 'max_bytes' must be a positive integer")

        allowed_base = (
            Path(os.environ.get("TOOL_FILE_READ_BASE_DIR", "/tmp/sentinel")).expanduser().resolve()
        )
        path = Path(path_raw).expanduser().resolve()
        if path != allowed_base and allowed_base not in path.parents:
            raise ToolValidationError(f"Path outside allowed directory: {allowed_base}")
        if not path.exists() or not path.is_file():
            raise ToolValidationError("File not found")

        data = path.read_bytes()
        chunk = data[:max_bytes]
        return {
            "path": str(path.resolve()),
            "content": chunk.decode("utf-8", errors="replace"),
            "bytes_read": len(chunk),
            "truncated": len(data) > max_bytes,
        }

    return ToolDefinition(
        name="file_read",
        description="Read text content from a local file path with byte limit.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "max_bytes": {"type": "integer"},
            },
        },
        execute=_execute,
    )


def _http_request_tool() -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        url = payload.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ToolValidationError("Field 'url' must be a non-empty string")
        parsed_url = urlparse(url.strip())
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise ToolValidationError("Field 'url' must be a valid http/https URL")
        await _validate_public_hostname(parsed_url.hostname)
        method = payload.get("method", "GET")
        if not isinstance(method, str):
            raise ToolValidationError("Field 'method' must be a string")
        method = method.upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ToolValidationError("Unsupported HTTP method")

        timeout_seconds = payload.get("timeout_seconds", 10)
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")

        headers = payload.get("headers", {})
        if headers is None:
            headers = {}
        if not isinstance(headers, dict):
            raise ToolValidationError("Field 'headers' must be an object")

        request_headers = {str(k): str(v) for k, v in headers.items()}
        request_kwargs: dict[str, Any] = {"headers": request_headers}
        if "body" in payload:
            body = payload["body"]
            if isinstance(body, (dict, list)):
                request_kwargs["json"] = body
            else:
                request_kwargs["content"] = str(body)

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(method, url, **request_kwargs)

        content_type = response.headers.get("content-type", "")
        response_bytes = response.content
        truncated = len(response_bytes) > _MAX_HTTP_RESPONSE_BYTES
        visible_bytes = response_bytes[:_MAX_HTTP_RESPONSE_BYTES]

        if "application/json" in content_type and not truncated:
            try:
                parsed_body: Any = response.json()
            except ValueError:
                parsed_body = response.text
        else:
            parsed_body = visible_bytes.decode("utf-8", errors="replace")
            if truncated:
                parsed_body += "\n... [truncated - response exceeded 1 MB]"

        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": parsed_body,
            "truncated": truncated,
        }

    return ToolDefinition(
        name="http_request",
        description="Make outbound HTTP requests to external endpoints.",
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["url"],
            "properties": {
                "url": {"type": "string"},
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                },
                "headers": {"type": "object"},
                "body": {"type": "object"},
                "timeout_seconds": {"type": "integer"},
            },
        },
        execute=_execute,
    )


async def _execute_via_ssh(
    *,
    session_id: UUID,
    command_text: str,
    privilege: str,
    workspace_dir: Path,
    cwd_raw: str | None,
    env_payload: dict[str, Any],
    timeout_seconds: int,
    detached: bool,
) -> dict[str, Any]:
    """Execute a command on the remote runtime via SSH."""
    runtime = await get_runtime().ensure(session_id)

    sandbox_workspace = runtime.workspace_path

    # Resolve cwd
    sandbox_cwd = sandbox_workspace
    if isinstance(cwd_raw, str) and cwd_raw.strip():
        requested = cwd_raw.strip()
        if Path(requested).is_absolute():
            # Absolute path — must be under the remote workspace
            if not requested.startswith(sandbox_workspace):
                raise ToolValidationError(f"Field 'cwd' must stay within session workspace ({sandbox_workspace})")
            sandbox_cwd = requested
        else:
            sandbox_cwd = f"{sandbox_workspace}/{requested}"

    # Build env for the remote runtime
    env: dict[str, str] = {
        "HOME": sandbox_workspace,
        "PWD": sandbox_cwd,
        "TMPDIR": "/tmp",
    }
    for key, value in env_payload.items():
        if not isinstance(key, str) or not key.strip():
            raise ToolValidationError("Environment variable keys must be non-empty strings")
        if value is None:
            env.pop(key, None)
            continue
        if not isinstance(value, (str, int, float, bool)):
            raise ToolValidationError(
                f"Environment variable '{key}' must be string/number/boolean/null"
            )
        env[key] = str(value)

    # Wrap command in bash; use sudo for root privilege
    if privilege == "root":
        full_command = f"sudo bash -lc {_ssh_shell_quote(command_text)}"
    else:
        full_command = f"bash -lc {_ssh_shell_quote(command_text)}"

    command_result_details: dict[str, Any] | None = None
    await mark_runtime_state(session_id, active=True, command=command_text, pid=None)

    try:
        if detached:
            logs_dir_vm = f"{sandbox_workspace}/.runtime/logs"
            log_token = uuid4().hex[:10]
            stdout_vm_path = f"{logs_dir_vm}/{log_token}.stdout.log"
            stderr_vm_path = f"{logs_dir_vm}/{log_token}.stderr.log"

            # Ensure logs dir exists on the remote
            await runtime.ssh.run(f"mkdir -p {logs_dir_vm}", timeout=10)

            pid = await runtime.ssh.run_detached(
                full_command,
                stdout_path=stdout_vm_path,
                stderr_path=stderr_vm_path,
                cwd=sandbox_cwd,
                env=env,
            )

            # Register job with host-side log paths (visible via 9p share)
            host_logs_dir = runtime_logs_dir(session_id)
            host_logs_dir.mkdir(parents=True, exist_ok=True)
            stdout_host = host_logs_dir / f"{log_token}.stdout.log"
            stderr_host = host_logs_dir / f"{log_token}.stderr.log"
            stdout_host.touch(exist_ok=True)
            stderr_host.touch(exist_ok=True)

            job = await register_detached_runtime_job(
                session_id,
                command=command_text,
                cwd=workspace_dir,
                pid=pid,
                stdout_path=stdout_host,
                stderr_path=stderr_host,
            )

            await mark_runtime_state(session_id, active=False, command=command_text, pid=pid)
            return {
                "ok": True,
                "detached": True,
                "job": job,
                "session_id": str(session_id),
                "workspace": sandbox_workspace,
                "cwd": sandbox_cwd,
                "privilege": privilege,
            }

        # Inline execution
        timeout_hint: str | None = None
        timed_out = False
        try:
            result = await runtime.ssh.run(
                full_command,
                cwd=sandbox_cwd,
                env=env,
                timeout=timeout_seconds,
            )
        except TimeoutError:
            timed_out = True
            timeout_hint = (
                f"Command timed out after {timeout_seconds}s. "
                "Use detached=true for long-running/background commands."
            )
            result = SSHExecResult(exit_status=-1, stdout="", stderr="[timed out]")

        stdout_text = _truncate_runtime_exec_text(result.stdout)
        stderr_text = _truncate_runtime_exec_text(result.stderr)
        ok = not timed_out and result.exit_status == 0

        command_result_details = {
            "ok": ok,
            "timed_out": timed_out,
            "returncode": result.exit_status,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "message": timeout_hint,
            "privilege": privilege,
        }
        return {
            **command_result_details,
            "session_id": str(session_id),
            "workspace": sandbox_workspace,
            "cwd": sandbox_cwd,
        }
    finally:
        await mark_runtime_state(
            session_id,
            active=False,
            command=command_text,
            pid=None,
            action_details=command_result_details,
        )


def _ssh_shell_quote(s: str) -> str:
    """POSIX single-quoting for SSH commands."""
    return "'" + s.replace("'", "'\\''") + "'"


def _runtime_exec_tool(*, session_factory: async_sessionmaker[AsyncSession]) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        session_id_raw = payload.get("session_id")
        if not isinstance(session_id_raw, str) or not session_id_raw.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")
        try:
            session_id = UUID(session_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'session_id' must be a valid UUID string") from exc

        command = payload.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolValidationError("Field 'command' must be a non-empty string")
        command_text = command.strip()

        privilege_raw = payload.get("privilege", "user")
        if not isinstance(privilege_raw, str) or not privilege_raw.strip():
            raise ToolValidationError("Field 'privilege' must be one of: user, root")
        privilege = privilege_raw.strip().lower()
        if privilege not in {"user", "root"}:
            raise ToolValidationError("Field 'privilege' must be one of: user, root")

        timeout_seconds = payload.get("timeout_seconds", 300)
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds < 1
        ):
            raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")
        timeout_seconds = min(timeout_seconds, 1800)

        detached = payload.get("detached", False)
        if not isinstance(detached, bool):
            raise ToolValidationError("Field 'detached' must be a boolean")
        if not detached and _command_requests_background_execution(command_text):
            raise ToolValidationError(
                "Background shell execution is not allowed for inline runtime_exec. "
                "Use detached=true for long-running/background commands."
            )

        cwd_raw = payload.get("cwd")
        if cwd_raw is not None and (not isinstance(cwd_raw, str) or not cwd_raw.strip()):
            raise ToolValidationError("Field 'cwd' must be a non-empty string when provided")

        env_payload = payload.get("env", {})
        if env_payload is None:
            env_payload = {}
        if not isinstance(env_payload, dict):
            raise ToolValidationError("Field 'env' must be an object")

        await _ensure_session_exists(session_factory, session_id)
        await ensure_runtime_layout(session_id)
        workspace_dir = runtime_workspace_dir(session_id)

        return await _execute_via_ssh(
            session_id=session_id,
            command_text=command_text,
            privilege=privilege,
            workspace_dir=workspace_dir,
            cwd_raw=cwd_raw,
            env_payload=env_payload,
            timeout_seconds=timeout_seconds,
            detached=detached,
        )

    return ToolDefinition(
        name="runtime_exec",
        description=(
            "Execute arbitrary shell commands in a per-session runtime workspace. "
            "privilege=user runs in a confined sandbox limited to workspace writes. "
            "privilege=root runs unconfined and requires explicit approval."
        ),
        risk_level="high",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["command"],
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Current session ID (auto-injected in agent loop)",
                },
                "command": {"type": "string"},
                "privilege": {
                    "type": "string",
                    "enum": ["user", "root"],
                    "description": "Execution privilege mode (default user). root requires approval.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional working directory inside the session workspace",
                },
                "env": {
                    "type": "object",
                    "description": "Optional environment variable overrides",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Execution timeout in seconds (default 300, max 1800)",
                },
                "approval_timeout_seconds": {
                    "type": "integer",
                    "description": "Root approval wait timeout in seconds (default 600, max 3600)",
                },
                "detached": {
                    "type": "boolean",
                    "description": "If true, starts command in background and returns a tracked job immediately",
                },
            },
        },
        execute=_execute,
        approval_gate=ToolApprovalGate(
            mode=ToolApprovalMode.CONDITIONAL,
            evaluator=_runtime_exec_approval_evaluator,
            waiter=build_tool_db_approval_waiter(session_factory=session_factory),
        ),
    )


def _runtime_exec_approval_timeout_from_payload(payload: dict[str, Any]) -> int:
    approval_timeout_seconds = payload.get(
        "approval_timeout_seconds",
        getattr(
            settings,
            "runtime_exec_root_approval_timeout_seconds",
            _DEFAULT_RUNTIME_ROOT_APPROVAL_TIMEOUT_SECONDS,
        ),
    )
    if (
        not isinstance(approval_timeout_seconds, int)
        or isinstance(approval_timeout_seconds, bool)
        or approval_timeout_seconds < 1
    ):
        raise ToolValidationError("Field 'approval_timeout_seconds' must be a positive integer")
    return min(approval_timeout_seconds, _MAX_RUNTIME_ROOT_APPROVAL_TIMEOUT_SECONDS)


def _runtime_exec_approval_evaluator(payload: dict[str, Any]) -> ToolApprovalEvaluation:
    privilege_raw = payload.get("privilege", "user")
    privilege = privilege_raw.strip().lower() if isinstance(privilege_raw, str) else "user"
    if privilege != "root":
        return ToolApprovalEvaluation.allow()

    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        return ToolApprovalEvaluation.allow()

    session_id = payload.get("session_id")
    requested_by = (
        f"session:{session_id.strip()}"
        if isinstance(session_id, str) and session_id.strip()
        else None
    )
    return ToolApprovalEvaluation.require(
        ToolApprovalRequirement(
            action="runtime_exec.root",
            description=f"Allow root runtime command: {command.strip()}",
            timeout_seconds=_runtime_exec_approval_timeout_from_payload(payload),
            match_key=build_runtime_exec_match_key(
                command=command,
                privilege="root",
            ),
            metadata={
                "tool_name": "runtime_exec",
                "privilege": "root",
                "command": command.strip(),
                "cwd": payload.get("cwd"),
                "detached": bool(payload.get("detached", False)),
            },
            requested_by=requested_by,
        )
    )


def _watch_detached_runtime_process(
    *,
    proc: asyncio.subprocess.Process,
    session_id: UUID,
    job_id: str,
) -> None:
    async def _watch() -> None:
        try:
            returncode = await proc.wait()
            await finalize_detached_runtime_job(
                session_id,
                job_id=job_id,
                returncode=returncode,
            )
        except Exception as exc:  # noqa: BLE001
            await finalize_detached_runtime_job(
                session_id,
                job_id=job_id,
                returncode=None,
                error=str(exc),
            )

    asyncio.create_task(_watch())


def _command_requests_background_execution(command: str) -> bool:
    normalized = command.strip().lower()
    if not normalized:
        return False
    if re.search(r"\b(?:nohup|disown)\b", normalized):
        return True
    return _RUNTIME_BACKGROUND_AMPERSAND_RE.search(normalized) is not None


async def _drain_runtime_exec_streams(
    proc: asyncio.subprocess.Process,
) -> tuple[bytes, bytes]:
    stdout = await _drain_runtime_exec_stream(proc.stdout)
    stderr = await _drain_runtime_exec_stream(proc.stderr)
    return stdout, stderr


async def _drain_runtime_exec_stream(
    stream: asyncio.StreamReader | None,
) -> bytes:
    if stream is None:
        return b""
    chunks: list[bytes] = []
    remaining = _RUNTIME_EXEC_STREAM_READ_BYTES
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _RUNTIME_EXEC_STREAM_DRAIN_SECONDS
    while remaining > 0:
        remaining_timeout = deadline - loop.time()
        if remaining_timeout <= 0:
            break
        chunk_size = min(remaining, 8_192)
        try:
            chunk = await asyncio.wait_for(stream.read(chunk_size), timeout=remaining_timeout)
        except TimeoutError:
            break
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _runtime_jobs_list_tool(*, session_factory: async_sessionmaker[AsyncSession]) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        session_id_raw = payload.get("session_id")
        if not isinstance(session_id_raw, str) or not session_id_raw.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")
        try:
            session_id = UUID(session_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'session_id' must be a valid UUID string") from exc
        include_completed = payload.get("include_completed", True)
        if not isinstance(include_completed, bool):
            raise ToolValidationError("Field 'include_completed' must be a boolean")
        await _ensure_session_exists(session_factory, session_id)
        jobs = await list_detached_runtime_jobs(session_id, include_completed=include_completed)
        return {
            "session_id": str(session_id),
            "jobs": jobs,
            "total": len(jobs),
        }

    return ToolDefinition(
        name="runtime_jobs_list",
        description="List tracked detached runtime_exec background jobs for the current session.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string", "description": "Current session ID"},
                "include_completed": {"type": "boolean"},
            },
        },
        execute=_execute,
    )


def _runtime_job_status_tool(*, session_factory: async_sessionmaker[AsyncSession]) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        session_id_raw = payload.get("session_id")
        job_id = payload.get("job_id")
        if not isinstance(session_id_raw, str) or not session_id_raw.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")
        if not isinstance(job_id, str) or not job_id.strip():
            raise ToolValidationError("Field 'job_id' must be a non-empty string")
        try:
            session_id = UUID(session_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'session_id' must be a valid UUID string") from exc
        await _ensure_session_exists(session_factory, session_id)
        job = await get_detached_runtime_job(session_id, job_id=job_id.strip())
        if job is None:
            raise ToolValidationError("Detached runtime job not found")
        return {"session_id": str(session_id), "job": job}

    return ToolDefinition(
        name="runtime_job_status",
        description="Get status for a detached runtime_exec background job by job_id.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "job_id"],
            "properties": {
                "session_id": {"type": "string", "description": "Current session ID"},
                "job_id": {"type": "string"},
            },
        },
        execute=_execute,
    )


def _runtime_job_logs_tool(*, session_factory: async_sessionmaker[AsyncSession]) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        session_id_raw = payload.get("session_id")
        job_id = payload.get("job_id")
        if not isinstance(session_id_raw, str) or not session_id_raw.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")
        if not isinstance(job_id, str) or not job_id.strip():
            raise ToolValidationError("Field 'job_id' must be a non-empty string")
        try:
            session_id = UUID(session_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'session_id' must be a valid UUID string") from exc
        tail_bytes = payload.get("tail_bytes", 8000)
        if (
            not isinstance(tail_bytes, int)
            or isinstance(tail_bytes, bool)
            or tail_bytes < 256
        ):
            raise ToolValidationError("Field 'tail_bytes' must be an integer >= 256")
        await _ensure_session_exists(session_factory, session_id)
        data = await read_detached_runtime_job_logs(
            session_id,
            job_id=job_id.strip(),
            tail_bytes=tail_bytes,
        )
        if data is None:
            raise ToolValidationError("Detached runtime job not found")
        return {"session_id": str(session_id), **data}

    return ToolDefinition(
        name="runtime_job_logs",
        description="Read recent stdout/stderr logs for a detached runtime_exec job.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "job_id"],
            "properties": {
                "session_id": {"type": "string", "description": "Current session ID"},
                "job_id": {"type": "string"},
                "tail_bytes": {"type": "integer"},
            },
        },
        execute=_execute,
    )


def _runtime_job_stop_tool(*, session_factory: async_sessionmaker[AsyncSession]) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        session_id_raw = payload.get("session_id")
        job_id = payload.get("job_id")
        if not isinstance(session_id_raw, str) or not session_id_raw.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")
        if not isinstance(job_id, str) or not job_id.strip():
            raise ToolValidationError("Field 'job_id' must be a non-empty string")
        try:
            session_id = UUID(session_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'session_id' must be a valid UUID string") from exc
        force = payload.get("force", False)
        if not isinstance(force, bool):
            raise ToolValidationError("Field 'force' must be a boolean")
        await _ensure_session_exists(session_factory, session_id)
        job = await stop_detached_runtime_job(
            session_id,
            job_id=job_id.strip(),
            force=force,
            reason="Stopped by runtime_job_stop tool",
        )
        if job is None:
            raise ToolValidationError("Detached runtime job not found")
        return {"session_id": str(session_id), "job": job}

    return ToolDefinition(
        name="runtime_job_stop",
        description="Stop a detached runtime_exec background job by job_id.",
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "job_id"],
            "properties": {
                "session_id": {"type": "string", "description": "Current session ID"},
                "job_id": {"type": "string"},
                "force": {"type": "boolean"},
            },
        },
        execute=_execute,
    )


def python_tool(
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

        await _ensure_session_exists(session_factory, session_id)
        await ensure_runtime_layout(session_id)

        return await _run_python_in_runtime(
            session_id=session_id,
            code=code,
            requirements=normalized_requirements,
            venv_name=venv_name,
            timeout_seconds=timeout_seconds,
        )

    return ToolDefinition(
        name="python",
        description=(
            "Run Python code in a persistent virtualenv inside the session's runtime container. "
            "Assign to `result` to return a value. Use venv_name to manage separate named envs."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["code"],
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Current session ID (auto-injected in agent loop)",
                },
                "code": {
                    "type": "string",
                    "description": "Python code to execute. Assign to `result` to return a value.",
                },
                "requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "pip packages to install in the session venv before running",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Execution timeout in seconds (default 60, max 600)",
                },
                "venv_name": {
                    "type": "string",
                    "description": (
                        "Named venv to use (default: workspace/.venvs/default). "
                        "Pass a name to use workspace/.venvs/<name> instead."
                    ),
                },
            },
        },
        execute=_execute,
    )


def _memory_search_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    memory_search_service: MemorySearchService,
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolValidationError("Field 'query' must be a non-empty string")

        category = payload.get("category")
        if category is not None:
            if not isinstance(category, str) or category not in _ALLOWED_MEMORY_CATEGORIES:
                raise ToolValidationError(
                    "Field 'category' must be one of: core, preference, project, correction"
                )

        limit = payload.get("limit", 10)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ToolValidationError("Field 'limit' must be a positive integer")
        limit = min(limit, 200)

        root_id_raw = payload.get("root_id")
        root_id: UUID | None = None
        if root_id_raw is not None:
            if not isinstance(root_id_raw, str) or not root_id_raw.strip():
                raise ToolValidationError("Field 'root_id' must be a UUID string")
            try:
                root_id = UUID(root_id_raw.strip())
            except ValueError as exc:
                raise ToolValidationError("Field 'root_id' must be a valid UUID string") from exc

        auto_expand = payload.get("auto_expand", True)
        if not isinstance(auto_expand, bool):
            raise ToolValidationError("Field 'auto_expand' must be a boolean")

        memory_service = MemoryService(MemoryRepository())
        async with session_factory() as db:
            result = await memory_service.search_memories(
                db,
                query=query.strip(),
                category=category,
                root_id=root_id,
                limit=limit,
                memory_search_service=memory_search_service,
            )
            expanded: list[Memory] = []
            if auto_expand:
                expanded = await memory_service.expand_branches(
                    db, items=result.items, root_id=root_id
                )

        return {
            "items": [
                {
                    **_memory_as_dict(item),
                    "score": result.scores.get(item.id),
                }
                for item in result.items
            ],
            "expanded_items": [_memory_as_dict(item) for item in expanded],
            "total": result.total,
        }

    return ToolDefinition(
        name="memory_search",
        description="Search stored memories using hybrid semantic and keyword ranking.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "category": {
                    "type": "string",
                    "enum": ["core", "preference", "project", "correction"],
                },
                "limit": {"type": "integer"},
                "root_id": {"type": "string"},
                "auto_expand": {"type": "boolean"},
            },
        },
        execute=_execute,
    )


def _memory_store_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    embedding_service: EmbeddingService | None,
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ToolValidationError("Field 'content' must be a non-empty string")

        category = payload.get("category", "project")
        if not isinstance(category, str) or category not in _ALLOWED_MEMORY_CATEGORIES:
            raise ToolValidationError(
                "Field 'category' must be one of: core, preference, project, correction"
            )

        title = payload.get("title")
        if title is not None and not isinstance(title, str):
            raise ToolValidationError("Field 'title' must be a string")
        title = title.strip() if isinstance(title, str) else None
        if title == "":
            title = None

        summary = payload.get("summary")
        if summary is not None and not isinstance(summary, str):
            raise ToolValidationError("Field 'summary' must be a string")
        summary = summary.strip() if isinstance(summary, str) else None
        if summary == "":
            summary = None

        parent_id_raw = payload.get("parent_id")
        parent_id: UUID | None = None
        if parent_id_raw is not None:
            if not isinstance(parent_id_raw, str) or not parent_id_raw.strip():
                raise ToolValidationError("Field 'parent_id' must be a UUID string")
            try:
                parent_id = UUID(parent_id_raw.strip())
            except ValueError as exc:
                raise ToolValidationError("Field 'parent_id' must be a valid UUID string") from exc

        importance = payload.get("importance", 0)
        if (
            not isinstance(importance, int)
            or isinstance(importance, bool)
            or importance < 0
            or importance > 100
        ):
            raise ToolValidationError("Field 'importance' must be an integer between 0 and 100")

        pinned = payload.get("pinned", False)
        if not isinstance(pinned, bool):
            raise ToolValidationError("Field 'pinned' must be a boolean")

        metadata = payload.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ToolValidationError("Field 'metadata' must be an object")

        embedding = payload.get("embedding")
        if embedding is not None:
            if not isinstance(embedding, list) or not all(
                isinstance(x, (int, float)) for x in embedding
            ):
                raise ToolValidationError("Field 'embedding' must be a list of numbers")
            embedding = [float(x) for x in embedding]

        memory_service = MemoryService(MemoryRepository())
        try:
            async with session_factory() as db:
                memory = await memory_service.create_memory(
                    db,
                    content=content.strip(),
                    title=title,
                    summary=summary,
                    category=category,
                    parent_id=parent_id,
                    importance=importance,
                    pinned=pinned,
                    metadata=metadata,
                    embedding=embedding,
                    embedding_service=embedding_service,
                    ignore_embedding_errors=False,
                )
        except Exception as exc:  # noqa: BLE001
            _raise_memory_tool_validation_error(
                exc,
                not_found_detail="Memory node not found",
                parent_not_found_detail="Parent memory node not found",
            )
            raise

        return {
            **_memory_as_dict(memory),
            "embedded": memory.embedding is not None,
        }

    return ToolDefinition(
        name="memory_store",
        description="Store a new memory item and auto-generate embedding when configured.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["content"],
            "properties": {
                "content": {"type": "string"},
                "category": {
                    "type": "string",
                    "enum": ["core", "preference", "project", "correction"],
                },
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "parent_id": {"type": "string"},
                "importance": {"type": "integer"},
                "pinned": {"type": "boolean"},
                "metadata": {"type": "object"},
                "embedding": {"type": "array", "items": {"type": "number"}},
            },
        },
        execute=_execute,
    )


def _memory_roots_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        if payload:
            raise ToolValidationError("memory_roots does not accept input fields")
        memory_service = MemoryService(MemoryRepository())
        async with session_factory() as db:
            roots = await memory_service.list_root_memories(db)
        return {
            "items": [
                {
                    **_memory_as_dict(item, include_parent=False),
                }
                for item in roots
            ],
            "total": len(roots),
        }

    return ToolDefinition(
        name="memory_roots",
        description="List all root memory nodes (no limit).",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        execute=_execute,
    )


def _memory_tree_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        category = payload.get("category")
        if category is not None:
            if not isinstance(category, str) or category not in _ALLOWED_MEMORY_CATEGORIES:
                raise ToolValidationError(
                    "Field 'category' must be one of: core, preference, project, correction"
                )

        root_id_raw = payload.get("root_id")
        root_id: UUID | None = None
        if root_id_raw is not None:
            if not isinstance(root_id_raw, str) or not root_id_raw.strip():
                raise ToolValidationError("Field 'root_id' must be a UUID string")
            try:
                root_id = UUID(root_id_raw.strip())
            except ValueError as exc:
                raise ToolValidationError("Field 'root_id' must be a valid UUID string") from exc

        max_depth = payload.get("max_depth", 5)
        if (
            not isinstance(max_depth, int)
            or isinstance(max_depth, bool)
            or max_depth < 0
            or max_depth > 20
        ):
            raise ToolValidationError("Field 'max_depth' must be an integer between 0 and 20")

        include_content = payload.get("include_content", False)
        if not isinstance(include_content, bool):
            raise ToolValidationError("Field 'include_content' must be a boolean")

        memory_service = MemoryService(MemoryRepository())
        async with session_factory() as db:
            all_items = await memory_service.list_all_memories(db)
            if category is not None and root_id is None:
                all_items = [item for item in all_items if item.category == category]
            by_id = {item.id: item for item in all_items}
            children_by_parent: dict[UUID | None, list[Memory]] = {}
            for item in all_items:
                children_by_parent.setdefault(item.parent_id, []).append(item)
            for children in children_by_parent.values():
                children.sort(
                    key=lambda item: (
                        item.created_at or datetime.min.replace(tzinfo=UTC),
                        item.id,
                    ),
                    reverse=True,
                )

            if root_id is not None:
                root = by_id.get(root_id)
                if root is None:
                    raise ToolValidationError("root_id references unknown memory node")
                roots = [root]
            else:
                roots = await memory_service.list_root_memories(db, category=category)

        visible_nodes = 0
        truncated = False

        def _node_to_tree(node: Memory, depth: int) -> dict[str, Any]:
            nonlocal visible_nodes, truncated
            visible_nodes += 1
            direct_children = children_by_parent.get(node.id, [])
            has_more_children = depth >= max_depth and bool(direct_children)
            if has_more_children:
                truncated = True

            payload_node: dict[str, Any] = {
                "id": str(node.id),
                "parent_id": str(node.parent_id) if node.parent_id else None,
                "title": node.title,
                "summary": node.summary,
                "category": node.category,
                "importance": int(node.importance or 0),
                "pinned": bool(node.pinned),
                "depth": depth,
                "child_count": len(direct_children),
                "has_more_children": has_more_children,
                "children": [],
            }
            if include_content:
                payload_node["content"] = node.content

            if depth < max_depth and direct_children:
                payload_node["children"] = [
                    _node_to_tree(child, depth + 1)
                    for child in direct_children
                ]

            return payload_node

        tree_roots = [_node_to_tree(root, 0) for root in roots]
        return {
            "roots": tree_roots,
            "total_roots": len(tree_roots),
            "visible_nodes": visible_nodes,
            "max_depth": max_depth,
            "truncated": truncated,
        }

    return ToolDefinition(
        name="memory_tree",
        description=(
            "Return memories as a nested tree (roots with recursive children). "
            "Supports optional root_id subtree selection and depth limiting."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["core", "preference", "project", "correction"],
                },
                "root_id": {"type": "string"},
                "max_depth": {"type": "integer"},
                "include_content": {"type": "boolean"},
            },
        },
        execute=_execute,
    )


def _memory_get_node_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        node_id_raw = payload.get("id")
        if not isinstance(node_id_raw, str) or not node_id_raw.strip():
            raise ToolValidationError("Field 'id' must be a non-empty UUID string")
        try:
            node_id = UUID(node_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'id' must be a valid UUID string") from exc
        memory_service = MemoryService(MemoryRepository())
        try:
            async with session_factory() as db:
                node = await memory_service.touch_memory(db, node_id)
        except Exception as exc:  # noqa: BLE001
            _raise_memory_tool_validation_error(exc, not_found_detail="Memory node not found")
            raise
        return {
            **_memory_as_dict(node),
            "metadata": node.metadata_json or {},
        }

    return ToolDefinition(
        name="memory_get_node",
        description="Get a memory node by ID and mark it as recently accessed.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
        execute=_execute,
    )


def _memory_list_children_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        parent_id_raw = payload.get("parent_id")
        if not isinstance(parent_id_raw, str) or not parent_id_raw.strip():
            raise ToolValidationError("Field 'parent_id' must be a non-empty UUID string")
        try:
            parent_id = UUID(parent_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'parent_id' must be a valid UUID string") from exc
        memory_service = MemoryService(MemoryRepository())
        try:
            async with session_factory() as db:
                result = await memory_service.list_children(db, parent_id=parent_id)
        except Exception as exc:  # noqa: BLE001
            _raise_memory_tool_validation_error(
                exc,
                not_found_detail="Memory node not found",
                parent_not_found_detail="Parent memory node not found",
            )
            raise
        return {
            "parent_id": str(parent_id),
            "items": [
                {
                    **_memory_as_dict(item, include_parent=False),
                }
                for item in result.items
            ],
            "total": result.total,
        }

    return ToolDefinition(
        name="memory_list_children",
        description="List direct child memory nodes for a parent node.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["parent_id"],
            "properties": {"parent_id": {"type": "string"}},
        },
        execute=_execute,
    )


def _memory_update_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    embedding_service: EmbeddingService | None,
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        node_id_raw = payload.get("id")
        if not isinstance(node_id_raw, str) or not node_id_raw.strip():
            raise ToolValidationError("Field 'id' must be a non-empty UUID string")
        try:
            node_id = UUID(node_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'id' must be a valid UUID string") from exc

        allowed_updates = {
            "content",
            "title",
            "summary",
            "category",
            "parent_id",
            "importance",
            "pinned",
            "metadata",
        }
        unknown = [key for key in payload if key not in allowed_updates and key != "id"]
        if unknown:
            raise ToolValidationError(f"Unknown update fields: {', '.join(sorted(unknown))}")

        updates: dict[str, Any] = {}

        if "content" in payload:
            content = payload.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ToolValidationError("Field 'content' must be a non-empty string")
            updates["content"] = content.strip()

        if "title" in payload:
            title = payload.get("title")
            if title is not None and not isinstance(title, str):
                raise ToolValidationError("Field 'title' must be a string or null")
            updates["title"] = title.strip() if isinstance(title, str) and title.strip() else None

        if "summary" in payload:
            summary = payload.get("summary")
            if summary is not None and not isinstance(summary, str):
                raise ToolValidationError("Field 'summary' must be a string or null")
            updates["summary"] = (
                summary.strip() if isinstance(summary, str) and summary.strip() else None
            )

        if "category" in payload:
            category = payload.get("category")
            if not isinstance(category, str) or category not in _ALLOWED_MEMORY_CATEGORIES:
                raise ToolValidationError(
                    "Field 'category' must be one of: core, preference, project, correction"
                )
            updates["category"] = category

        if "importance" in payload:
            importance = payload.get("importance")
            if (
                not isinstance(importance, int)
                or isinstance(importance, bool)
                or importance < 0
                or importance > 100
            ):
                raise ToolValidationError("Field 'importance' must be an integer between 0 and 100")
            updates["importance"] = importance

        if "pinned" in payload:
            pinned = payload.get("pinned")
            if not isinstance(pinned, bool):
                raise ToolValidationError("Field 'pinned' must be a boolean")
            updates["pinned"] = pinned

        if "metadata" in payload:
            metadata = payload.get("metadata")
            if metadata is None:
                metadata = {}
            if not isinstance(metadata, dict):
                raise ToolValidationError("Field 'metadata' must be an object")
            updates["metadata"] = metadata

        if "parent_id" in payload:
            parent_id_raw = payload.get("parent_id")
            if parent_id_raw is None:
                updates["parent_id"] = None
            else:
                if not isinstance(parent_id_raw, str) or not parent_id_raw.strip():
                    raise ToolValidationError("Field 'parent_id' must be a UUID string or null")
                try:
                    updates["parent_id"] = UUID(parent_id_raw.strip())
                except ValueError as exc:
                    raise ToolValidationError(
                        "Field 'parent_id' must be a valid UUID string"
                    ) from exc

        memory_service = MemoryService(MemoryRepository())
        try:
            async with session_factory() as db:
                node = await memory_service.update_memory(
                    db,
                    memory_id=node_id,
                    updates=updates,
                    embedding_service=embedding_service,
                    ignore_embedding_errors=False,
                )
        except Exception as exc:  # noqa: BLE001
            _raise_memory_tool_validation_error(
                exc,
                not_found_detail="Memory node not found",
                parent_not_found_detail="Parent memory node not found",
            )
            raise

        return {
            **_memory_as_dict(node),
        }

    return ToolDefinition(
        name="memory_update",
        description="Update an existing memory node (hierarchical fields included).",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["id"],
            "properties": {
                "id": {"type": "string"},
                "content": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "category": {
                    "type": "string",
                    "enum": ["core", "preference", "project", "correction"],
                },
                "parent_id": {"type": "string"},
                "importance": {"type": "integer"},
                "pinned": {"type": "boolean"},
                "metadata": {"type": "object"},
            },
        },
        execute=_execute,
    )


def _memory_touch_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        node_id_raw = payload.get("id")
        if not isinstance(node_id_raw, str) or not node_id_raw.strip():
            raise ToolValidationError("Field 'id' must be a non-empty UUID string")
        try:
            node_id = UUID(node_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'id' must be a valid UUID string") from exc
        memory_service = MemoryService(MemoryRepository())
        try:
            async with session_factory() as db:
                node = await memory_service.touch_memory(db, node_id)
        except Exception as exc:  # noqa: BLE001
            _raise_memory_tool_validation_error(exc, not_found_detail="Memory node not found")
            raise
        return {
            "id": str(node.id),
            "last_accessed_at": node.last_accessed_at.isoformat(),
        }

    return ToolDefinition(
        name="memory_touch",
        description="Mark a memory node as recently accessed.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
        execute=_execute,
    )


def _memory_move_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        node_ids_raw = payload.get("node_ids")
        if not isinstance(node_ids_raw, list) or not node_ids_raw:
            raise ToolValidationError("Field 'node_ids' must be a non-empty array of UUID strings")

        node_ids: list[UUID] = []
        seen_ids: set[UUID] = set()
        for raw in node_ids_raw:
            if not isinstance(raw, str) or not raw.strip():
                raise ToolValidationError("Each node_id must be a non-empty UUID string")
            try:
                parsed = UUID(raw.strip())
            except ValueError as exc:
                raise ToolValidationError(f"Invalid node_id UUID: {raw}") from exc
            if parsed in seen_ids:
                continue
            seen_ids.add(parsed)
            node_ids.append(parsed)
        if not node_ids:
            raise ToolValidationError("Field 'node_ids' must contain at least one UUID")

        to_root = payload.get("to_root", False)
        if not isinstance(to_root, bool):
            raise ToolValidationError("Field 'to_root' must be a boolean")

        target_parent_id_raw = payload.get("target_parent_id")
        target_parent_id: UUID | None = None
        if target_parent_id_raw is not None:
            if not isinstance(target_parent_id_raw, str) or not target_parent_id_raw.strip():
                raise ToolValidationError("Field 'target_parent_id' must be a UUID string")
            try:
                target_parent_id = UUID(target_parent_id_raw.strip())
            except ValueError as exc:
                raise ToolValidationError("Field 'target_parent_id' must be a valid UUID string") from exc

        if to_root and target_parent_id is not None:
            raise ToolValidationError("Provide either to_root=true or target_parent_id, not both")
        if not to_root and target_parent_id is None:
            raise ToolValidationError("Provide target_parent_id or set to_root=true")

        memory_service = MemoryService(MemoryRepository())
        try:
            async with session_factory() as db:
                moved = await memory_service.move_memories(
                    db,
                    node_ids=node_ids,
                    target_parent_id=target_parent_id,
                    to_root=to_root,
                )
        except Exception as exc:  # noqa: BLE001
            _raise_memory_tool_validation_error(
                exc,
                not_found_detail="Memory node not found",
                parent_not_found_detail="Parent memory node not found",
            )
            raise

        return {
            "moved_node_ids": [str(item.id) for item in moved],
            "target_parent_id": None if to_root else str(target_parent_id),
            "to_root": to_root,
            "moved_count": len(moved),
        }

    return ToolDefinition(
        name="memory_move",
        description=(
            "Move one or more memory nodes (and their full subtrees) to a new parent or to root. "
            "Use for fast tree reorganization."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["node_ids"],
            "properties": {
                "node_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Node UUIDs to move. Pass top-level nodes only.",
                },
                "target_parent_id": {
                    "type": "string",
                    "description": "Destination parent UUID. Omit when moving to root.",
                },
                "to_root": {
                    "type": "boolean",
                    "description": "Set true to move selected nodes to root (parent_id=null).",
                },
            },
        },
        execute=_execute,
    )


def _memory_delete_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        node_id_raw = payload.get("id")
        if not isinstance(node_id_raw, str) or not node_id_raw.strip():
            raise ToolValidationError("Field 'id' must be a non-empty UUID string")
        try:
            node_id = UUID(node_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'id' must be a valid UUID string") from exc
        memory_service = MemoryService(MemoryRepository())
        try:
            async with session_factory() as db:
                await memory_service.delete_memory(db, node_id)
        except Exception as exc:  # noqa: BLE001
            _raise_memory_tool_validation_error(exc, not_found_detail="Memory node not found")
            raise
        return {
            "id": str(node_id),
            "deleted": True,
        }

    return ToolDefinition(
        name="memory_delete",
        description="Delete a memory node and all of its descendants.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
        execute=_execute,
    )


def _memory_as_dict(memory: Memory, *, include_parent: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": str(memory.id),
        "content": memory.content,
        "title": memory.title,
        "summary": memory.summary,
        "category": memory.category,
        "importance": int(memory.importance or 0),
        "pinned": bool(memory.pinned),
        "is_system": bool(getattr(memory, "is_system", False)),
        "system_key": getattr(memory, "system_key", None),
    }
    if include_parent:
        data["parent_id"] = str(memory.parent_id) if memory.parent_id else None
    return data


def _raise_memory_tool_validation_error(
    exc: Exception,
    *,
    not_found_detail: str,
    parent_not_found_detail: str = "Parent memory node not found",
) -> None:
    if isinstance(exc, MemoryNotFoundError):
        raise ToolValidationError(not_found_detail) from exc
    if isinstance(exc, ParentMemoryNotFoundError):
        raise ToolValidationError(parent_not_found_detail) from exc
    if isinstance(exc, InvalidMemoryOperationError):
        raise ToolValidationError(str(exc)) from exc
    raise exc


def spawn_sub_agent_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator: Any,
    ws_manager: Any | None = None,
    browser_pool: BrowserPool | None = None,
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        from uuid import UUID as _UUID

        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")
        objective = payload.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise ToolValidationError("Field 'objective' must be a non-empty string")

        scope = payload.get("scope")
        if scope is not None and not isinstance(scope, str):
            raise ToolValidationError("Field 'scope' must be a string")

        allowed_tools = payload.get("allowed_tools", [])
        if not isinstance(allowed_tools, list):
            raise ToolValidationError("Field 'allowed_tools' must be an array")
        normalized_allowed_tools = [str(t) for t in allowed_tools if isinstance(t, str)]
        browser_tab_id = payload.get("browser_tab_id")
        if browser_tab_id is not None and (
            not isinstance(browser_tab_id, str) or not browser_tab_id.strip()
        ):
            raise ToolValidationError("Field 'browser_tab_id' must be a non-empty string")
        normalized_browser_tab_id = (
            browser_tab_id.strip()
            if isinstance(browser_tab_id, str) and browser_tab_id.strip()
            else None
        )

        max_steps = payload.get("max_steps", 10)
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps < 1:
            raise ToolValidationError("Field 'max_steps' must be a positive integer")
        max_steps = min(max_steps, 50)

        timeout_seconds = payload.get("timeout_seconds", 300)
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds < 1
        ):
            raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")
        timeout_seconds = min(timeout_seconds, 3600)

        sid = _UUID(session_id.strip())
        auto_assigned_browser_tab = False

        async with session_factory() as db:
            from sqlalchemy import select as _select

            # Enforce max 3 concurrent tasks per session
            result = await db.execute(_select(SubAgentTask).where(SubAgentTask.session_id == sid))
            tasks = result.scalars().all()
            active = [t for t in tasks if t.status in {"pending", "running"}]
            if len(active) >= 3:
                raise ToolValidationError("Max 3 concurrent sub-agent tasks per session")
            if (
                normalized_browser_tab_id is None
                and browser_pool is not None
                and _sub_agent_may_use_browser(normalized_allowed_tools)
            ):
                reserved_tab_ids = _active_sub_agent_tab_ids(active)
                try:
                    _mgr = await browser_pool.get(sid)
                    normalized_browser_tab_id = await _select_sub_agent_browser_tab_id(
                        _mgr, reserved_tab_ids=reserved_tab_ids
                    )
                except Exception:
                    pass
                auto_assigned_browser_tab = normalized_browser_tab_id is not None

            task = SubAgentTask(
                session_id=sid,
                objective=objective.strip(),
                context=(scope.strip() if isinstance(scope, str) and scope.strip() else None),
                constraints=(
                    [{"type": "browser_tab", "tab_id": normalized_browser_tab_id}]
                    if normalized_browser_tab_id
                    else []
                ),
                allowed_tools=normalized_allowed_tools,
                max_turns=max_steps,
                timeout_seconds=timeout_seconds,
                status="pending",
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)
            task_id = task.id

        orchestrator.start_task(task_id)
        if ws_manager is not None and hasattr(ws_manager, "broadcast_sub_agent_started"):
            with contextlib.suppress(Exception):
                await ws_manager.broadcast_sub_agent_started(
                    str(sid),
                    str(task_id),
                    objective.strip(),
                )
        return {
            "task_id": str(task_id),
            "status": "pending",
            "objective": objective.strip(),
            "timeout_seconds": timeout_seconds,
            "browser_tab_id": normalized_browser_tab_id,
            "auto_assigned_browser_tab": auto_assigned_browser_tab,
            "note": (
                f"Sub-agent spawned (timeout: {timeout_seconds}s). "
                "Next steps: use check_sub_agent with this task_id before reporting delegated output. "
                "Do not block waiting in-turn; continue other work and check status later. "
                "The main session can be prompted when results are ready."
            ),
        }

    return ToolDefinition(
        name="spawn_sub_agent",
        description=(
            "Spawn a sub-agent for a bounded one-off task. "
            "Recommended workflow: list_sub_agents -> spawn_sub_agent -> check_sub_agent before reporting completion. "
            "Keep delegation non-blocking: continue main work and verify with check_sub_agent when needed. "
            "By default, sub-agents can use all tools when allowed_tools is omitted or empty. "
            "For browser delegation, pass browser_tab_id to pin the sub-agent to a single tab. "
            "If omitted and browser tools are available, a dedicated non-active tab may be auto-assigned."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "objective"],
            "properties": {
                "session_id": {"type": "string", "description": "Current session ID"},
                "objective": {
                    "type": "string",
                    "description": "Concrete one-off outcome the sub-agent should produce",
                },
                "scope": {
                    "type": "string",
                    "description": "Extra context or constraints for the sub-agent",
                },
                "allowed_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional allowlist of tool names. Omit or pass [] to allow all tools.",
                },
                "browser_tab_id": {
                    "type": "string",
                    "description": "Optional browser tab ID to pin this sub-agent's browser actions to one tab.",
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Maximum iterations (default 10, max 50). Typical range: 15-30 for research tasks — use more steps for tasks that require many browser calls or deep investigation.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 300)",
                },
            },
        },
        execute=_execute,
    )


def check_sub_agent_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        from uuid import UUID as _UUID

        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ToolValidationError("Field 'task_id' must be a non-empty string")

        tid = _UUID(task_id.strip())
        async with session_factory() as db:
            from sqlalchemy import select as _select

            result = await db.execute(_select(SubAgentTask).where(SubAgentTask.id == tid))
            task = result.scalars().first()
            if task is None:
                raise ToolValidationError("Sub-agent task not found")

            result_payload = task.result if isinstance(task.result, dict) else None
            status = str(task.status)
            next_action = "Continue other work and check_sub_agent again later."
            retry_recommended = False
            if status == "completed":
                next_action = (
                    "Evaluate whether the delegated output fully satisfies the objective. "
                    "If not, spawn_sub_agent again with a refined objective/scope."
                )
                final_text = (
                    result_payload.get("final_text") if isinstance(result_payload, dict) else None
                )
                if not isinstance(final_text, str) or not final_text.strip():
                    retry_recommended = True
            elif status in {"failed", "cancelled"}:
                retry_recommended = True
                next_action = (
                    "Retry by spawning a new sub-agent with a refined objective/scope "
                    "or adjusted max_steps/timeout."
                )
            turns_used = int(task.turns_used or 0)
            max_steps = int(task.max_turns or 0)
            grace_turns_used = max(0, turns_used - max_steps)

            return {
                "task_id": str(task.id),
                "objective": task.objective,
                "status": status,
                "max_steps": max_steps,
                "turns_used": turns_used,
                "grace_turns_used": grace_turns_used,
                "tokens_used": task.tokens_used or 0,
                "result": result_payload,
                "retry_recommended": retry_recommended,
                "next_action": next_action,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "completed_at": (task.completed_at.isoformat() if task.completed_at else None),
            }

    return ToolDefinition(
        name="check_sub_agent",
        description=(
            "Check the status and result of a sub-agent task. "
            "Use this before claiming delegated work is complete. "
            "If output is insufficient, refine objective/scope and spawn again."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["task_id"],
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The sub-agent task ID to check",
                },
            },
        },
        execute=_execute,
    )


def list_sub_agents_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        from uuid import UUID as _UUID

        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")

        sid = _UUID(session_id.strip())
        async with session_factory() as db:
            from sqlalchemy import select as _select

            result = await db.execute(_select(SubAgentTask).where(SubAgentTask.session_id == sid))
            tasks = result.scalars().all()
            tasks.sort(key=lambda t: t.created_at, reverse=True)

            return {
                "tasks": [
                    {
                        "task_id": str(t.id),
                        "objective": t.objective,
                        "status": t.status,
                        "max_steps": int(t.max_turns or 0),
                        "turns_used": int(t.turns_used or 0),
                        "grace_turns_used": max(0, int(t.turns_used or 0) - int(t.max_turns or 0)),
                        "tokens_used": t.tokens_used or 0,
                    }
                    for t in tasks
                ],
                "total": len(tasks),
            }

    return ToolDefinition(
        name="list_sub_agents",
        description=(
            "List all sub-agent tasks for the current session. "
            "Use this before spawning to avoid duplicate delegation."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                "session_id": {"type": "string", "description": "Current session ID"},
            },
        },
        execute=_execute,
    )


def cancel_sub_agent_tool(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    orchestrator: Any,
) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        from uuid import UUID as _UUID

        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")

        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ToolValidationError("Field 'task_id' must be a non-empty string")

        sid = _UUID(session_id.strip())
        tid = _UUID(task_id.strip())

        async with session_factory() as db:
            from sqlalchemy import select as _select

            result = await db.execute(
                _select(SubAgentTask).where(
                    SubAgentTask.id == tid,
                    SubAgentTask.session_id == sid,
                )
            )
            task = result.scalars().first()
            if task is None:
                raise ToolValidationError("Sub-agent task not found for this session")

            previous_status = str(task.status)
            if previous_status in {"completed", "failed", "cancelled"}:
                result_payload = task.result if isinstance(task.result, dict) else None
                return {
                    "task_id": str(task.id),
                    "session_id": str(task.session_id),
                    "cancelled": False,
                    "status": previous_status,
                    "previous_status": previous_status,
                    "message": "Task already terminal; no cancellation performed.",
                    "result": result_payload,
                }

            task.status = "cancelled"
            task.completed_at = datetime.now(UTC)
            current_result = task.result if isinstance(task.result, dict) else {}
            current_result = dict(current_result)
            current_result.setdefault("cancel_reason", "Cancelled by agent request")
            task.result = current_result
            await db.commit()
            await db.refresh(task)

        cancel_signal_sent = False
        if orchestrator is not None and hasattr(orchestrator, "cancel_task"):
            with contextlib.suppress(Exception):
                cancel_signal_sent = bool(orchestrator.cancel_task(tid))

        return {
            "task_id": str(task.id),
            "session_id": str(task.session_id),
            "cancelled": True,
            "status": str(task.status),
            "previous_status": previous_status,
            "cancel_signal_sent": cancel_signal_sent,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "result": task.result if isinstance(task.result, dict) else None,
        }

    return ToolDefinition(
        name="cancel_sub_agent",
        description=(
            "Cancel a pending or running sub-agent task for the current session. "
            "Use this to stop delegated work that is no longer needed."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "task_id"],
            "properties": {
                "session_id": {"type": "string", "description": "Current session ID"},
                "task_id": {"type": "string", "description": "Sub-agent task ID to cancel"},
            },
        },
        execute=_execute,
    )


async def _ensure_session_exists(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: UUID,
) -> None:
    async with session_factory() as db:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalars().first()
        if session is None:
            raise ToolValidationError("Session not found")


def _extract_browser_tab_constraint(constraints: Any) -> str | None:
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


def _active_sub_agent_tab_ids(tasks: list[SubAgentTask]) -> set[str]:
    reserved: set[str] = set()
    for task in tasks:
        tab_id = _extract_browser_tab_constraint(task.constraints)
        if tab_id:
            reserved.add(tab_id)
    return reserved


def _sub_agent_may_use_browser(allowed_tools: list[str]) -> bool:
    if not allowed_tools:
        return True
    return any(tool.startswith("browser_") for tool in allowed_tools)


async def _select_sub_agent_browser_tab_id(
    browser_manager: BrowserManager,
    *,
    reserved_tab_ids: set[str],
) -> str | None:
    try:
        tabs_payload = await browser_manager.list_tabs()
        tabs = tabs_payload.get("tabs", [])
        active_tab_id = tabs_payload.get("active_tab_id")
        active_tab_id = active_tab_id.strip() if isinstance(active_tab_id, str) else None

        for item in tabs:
            if not isinstance(item, dict):
                continue
            tab_id = item.get("tab_id")
            if not isinstance(tab_id, str) or not tab_id.strip():
                continue
            normalized_tab_id = tab_id.strip()
            if normalized_tab_id in reserved_tab_ids:
                continue
            if active_tab_id is not None and normalized_tab_id == active_tab_id:
                continue
            return normalized_tab_id

        opened = await browser_manager.open_tab("about:blank")
        tab_id = opened.get("tab_id")
        normalized_opened_tab_id = (
            tab_id.strip() if isinstance(tab_id, str) and tab_id.strip() else None
        )
        if (
            active_tab_id is not None
            and normalized_opened_tab_id is not None
            and active_tab_id != normalized_opened_tab_id
        ):
            with contextlib.suppress(Exception):
                await browser_manager.focus_tab(active_tab_id)
        return normalized_opened_tab_id
    except Exception:
        return None


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


def _truncate_runtime_exec_text(value: str | None) -> str:
    text = value or ""
    if len(text) <= _MAX_RUNTIME_EXEC_OUTPUT_CHARS:
        return text
    return f"{text[:_MAX_RUNTIME_EXEC_OUTPUT_CHARS]}\n...[truncated]"


async def _validate_public_hostname(hostname: str) -> None:
    normalized_hostname = hostname.strip().lower().rstrip(".")
    allowed_hosts_raw = os.environ.get("SSRF_ALLOW_HOSTS", "")
    allowed_hosts = {
        value.strip().lower().rstrip(".") for value in allowed_hosts_raw.split(",") if value.strip()
    }
    if normalized_hostname in allowed_hosts:
        return
    if os.environ.get("SSRF_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes"):
        return
    try:
        addr_info = socket.getaddrinfo(normalized_hostname, None)
    except socket.gaierror as exc:
        raise ToolValidationError(f"Cannot resolve hostname: {normalized_hostname}") from exc

    blocked: list[str] = []
    for item in addr_info:
        ip_text = item[4][0]
        ip_addr = ipaddress.ip_address(ip_text)
        if (
            ip_addr.is_private
            or ip_addr.is_loopback
            or ip_addr.is_link_local
            or ip_addr.is_reserved
            or ip_addr.is_multicast
            or ip_addr.is_unspecified
        ):
            blocked.append(ip_text)

    if blocked:
        raise ToolValidationError(
            f"SSRF blocked: {normalized_hostname} resolves to private/internal address {', '.join(sorted(set(blocked)))}"
        )


def _optional_browser_tab_id(payload: dict[str, Any]) -> str | None:
    tab_id = payload.get("tab_id")
    if tab_id is None:
        return None
    if not isinstance(tab_id, str) or not tab_id.strip():
        raise ToolValidationError("Field 'tab_id' must be a non-empty string")
    return tab_id.strip()


async def _resolve_browser_manager(pool: BrowserPool, payload: dict[str, Any]) -> BrowserManager:
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ToolValidationError("Field 'session_id' must be a non-empty string")
    return await pool.get(session_id.strip())


_BROWSER_SESSION_PROP = {"session_id": {"type": "string", "description": "The agent session UUID"}}


def _browser_navigate_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        url = payload.get("url")
        timeout_ms = payload.get("timeout_ms")
        tab_id = _optional_browser_tab_id(payload)
        if not isinstance(url, str) or not url.strip():
            raise ToolValidationError("Field 'url' must be a non-empty string")
        if timeout_ms is not None and (
            not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0
        ):
            raise ToolValidationError("Field 'timeout_ms' must be a positive integer")
        return await manager.navigate(
            url.strip(),
            timeout_ms=timeout_ms,
            tab_id=tab_id,
        )

    return ToolDefinition(
        name="browser_navigate",
        description=(
            "Navigate the browser to a URL. Returns page title and final URL. "
            "After navigating, use browser_snapshot to read page content."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "url"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "url": {"type": "string"},
                "timeout_ms": {"type": "integer", "minimum": 1},
                "tab_id": {"type": "string"},
            },
        },
        execute=_execute,
    )


def _browser_screenshot_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        full_page = payload.get("full_page", True)
        tab_id = _optional_browser_tab_id(payload)
        if not isinstance(full_page, bool):
            raise ToolValidationError("Field 'full_page' must be a boolean")
        return await manager.screenshot(full_page=full_page, tab_id=tab_id)

    return ToolDefinition(
        name="browser_screenshot",
        description=(
            "Capture a screenshot of the current browser page as a PNG image. "
            "The image is rendered directly in the user's chat — use this proactively to show progress, "
            "verify page state after navigation, confirm form submissions, or when the user would benefit "
            "from seeing what the browser looks like. Use full_page=false to capture only the visible viewport."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "full_page": {"type": "boolean"},
                "tab_id": {"type": "string"},
            },
        },
        execute=_execute,
    )


def _browser_click_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        selector = payload.get("selector")
        timeout_ms = payload.get("timeout_ms")
        tab_id = _optional_browser_tab_id(payload)
        if not isinstance(selector, str) or not selector.strip():
            raise ToolValidationError("Field 'selector' must be a non-empty string")
        if timeout_ms is not None and (
            not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0
        ):
            raise ToolValidationError("Field 'timeout_ms' must be a positive integer")
        return await manager.click(
            selector.strip(),
            timeout_ms=timeout_ms,
            tab_id=tab_id,
        )

    return ToolDefinition(
        name="browser_click",
        description=(
            "Click an element by selector. Supports CSS selectors and accessibility selectors "
            "from browser_snapshot like 'button: Accept' or 'link: Sign in'. "
            "Also supports 'aria=Name' and 'aria/Name'."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "selector"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "selector": {"type": "string"},
                "timeout_ms": {"type": "integer", "minimum": 1},
                "tab_id": {"type": "string"},
            },
        },
        execute=_execute,
    )


def _browser_type_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        selector = payload.get("selector")
        text = payload.get("text")
        timeout_ms = payload.get("timeout_ms")
        tab_id = _optional_browser_tab_id(payload)
        if not isinstance(selector, str) or not selector.strip():
            raise ToolValidationError("Field 'selector' must be a non-empty string")
        if not isinstance(text, str):
            raise ToolValidationError("Field 'text' must be a string")
        if timeout_ms is not None and (
            not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0
        ):
            raise ToolValidationError("Field 'timeout_ms' must be a positive integer")
        return await manager.type_text(
            selector.strip(),
            text,
            timeout_ms=timeout_ms,
            tab_id=tab_id,
        )

    return ToolDefinition(
        name="browser_type",
        description=(
            "Type text into an element. Supports CSS selectors and accessibility selectors "
            "from browser_snapshot like 'textbox: Email'. Also supports 'aria=Name' and 'aria/Name'."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "selector", "text"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "timeout_ms": {"type": "integer", "minimum": 1},
                "tab_id": {"type": "string"},
            },
        },
        execute=_execute,
    )


def _browser_select_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        selector = payload.get("selector")
        value = payload.get("value")
        label = payload.get("label")
        index = payload.get("index")
        timeout_ms = payload.get("timeout_ms")
        tab_id = _optional_browser_tab_id(payload)
        if not isinstance(selector, str) or not selector.strip():
            raise ToolValidationError("Field 'selector' must be a non-empty string")
        criteria_count = 0
        if value is not None:
            if not isinstance(value, str):
                raise ToolValidationError("Field 'value' must be a string")
            criteria_count += 1
        if label is not None:
            if not isinstance(label, str):
                raise ToolValidationError("Field 'label' must be a string")
            criteria_count += 1
        if index is not None:
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                raise ToolValidationError("Field 'index' must be a non-negative integer")
            criteria_count += 1
        if criteria_count == 0:
            raise ToolValidationError(
                "Provide one of 'value', 'label', or 'index' for browser_select"
            )
        if timeout_ms is not None and (
            not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0
        ):
            raise ToolValidationError("Field 'timeout_ms' must be a positive integer")
        return await manager.select_option(
            selector.strip(),
            value=value,
            label=label,
            index=index,
            timeout_ms=timeout_ms,
            tab_id=tab_id,
        )

    return ToolDefinition(
        name="browser_select",
        description=(
            "Select an option in a dropdown/select element. "
            "Use this for native selects (Month/Day/Year, country pickers, etc.) instead of clicking option rows."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "selector"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "selector": {"type": "string"},
                "value": {"type": "string"},
                "label": {"type": "string"},
                "index": {"type": "integer", "minimum": 0},
                "timeout_ms": {"type": "integer", "minimum": 1},
                "tab_id": {"type": "string"},
            },
        },
        execute=_execute,
    )


def _browser_wait_for_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        selector = payload.get("selector")
        condition = payload.get("condition", "visible")
        timeout_ms = payload.get("timeout_ms")
        tab_id = _optional_browser_tab_id(payload)
        if not isinstance(selector, str) or not selector.strip():
            raise ToolValidationError("Field 'selector' must be a non-empty string")
        if not isinstance(condition, str) or not condition.strip():
            raise ToolValidationError("Field 'condition' must be a non-empty string")
        if timeout_ms is not None:
            if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
                raise ToolValidationError("Field 'timeout_ms' must be a positive integer")
        return await manager.wait_for(
            selector.strip(),
            condition=condition.strip(),
            timeout_ms=timeout_ms,
            tab_id=tab_id,
        )

    return ToolDefinition(
        name="browser_wait_for",
        description=(
            "Wait for a selector state change before continuing. "
            "Useful for waiting until buttons become enabled or UI transitions finish."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "selector"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "selector": {"type": "string"},
                "condition": {
                    "type": "string",
                    "enum": [
                        "visible",
                        "hidden",
                        "attached",
                        "detached",
                        "enabled",
                        "disabled",
                    ],
                },
                "timeout_ms": {"type": "integer", "minimum": 1},
                "tab_id": {"type": "string"},
            },
        },
        execute=_execute,
    )


def _browser_get_value_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        selector = payload.get("selector")
        tab_id = _optional_browser_tab_id(payload)
        if not isinstance(selector, str) or not selector.strip():
            raise ToolValidationError("Field 'selector' must be a non-empty string")
        return await manager.get_value(selector.strip(), tab_id=tab_id)

    return ToolDefinition(
        name="browser_get_value",
        description=(
            "Read the live value/state of form controls and elements (input/textarea/select). "
            "Use this to verify what is actually filled or selected."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "selector"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "selector": {"type": "string"},
                "tab_id": {"type": "string"},
            },
        },
        execute=_execute,
    )


def _browser_fill_form_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        steps = payload.get("steps")
        continue_on_error = payload.get("continue_on_error", False)
        verify = payload.get("verify", False)
        tab_id = _optional_browser_tab_id(payload)
        if not isinstance(steps, list) or not steps:
            raise ToolValidationError("Field 'steps' must be a non-empty array")
        if not isinstance(continue_on_error, bool):
            raise ToolValidationError("Field 'continue_on_error' must be a boolean")
        if not isinstance(verify, bool):
            raise ToolValidationError("Field 'verify' must be a boolean")
        return await manager.fill_form(
            steps,
            continue_on_error=continue_on_error,
            verify=verify,
            tab_id=tab_id,
        )

    return ToolDefinition(
        name="browser_fill_form",
        description=(
            "Execute a full form flow in one call using ordered steps. "
            "Each step requires selector and supports action: type, select, click, or wait. "
            "If action is omitted, it is inferred from fields (text/value/label/index/click/condition). "
            "Use verify=true to read back input/select values after type/select steps."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "steps"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["selector"],
                        "properties": {
                            "selector": {"type": "string"},
                            "action": {
                                "type": "string",
                                "enum": ["type", "select", "click", "wait"],
                            },
                            "text": {"type": "string"},
                            "value": {"type": "string"},
                            "label": {"type": "string"},
                            "index": {"type": "integer", "minimum": 0},
                            "condition": {
                                "type": "string",
                                "enum": [
                                    "visible",
                                    "hidden",
                                    "attached",
                                    "detached",
                                    "enabled",
                                    "disabled",
                                ],
                            },
                            "timeout_ms": {"type": "integer", "minimum": 1},
                            "click": {"type": "boolean"},
                        },
                    },
                },
                "continue_on_error": {"type": "boolean"},
                "verify": {"type": "boolean"},
                "tab_id": {"type": "string"},
            },
        },
        execute=_execute,
    )


def _browser_press_key_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        key = payload.get("key")
        tab_id = _optional_browser_tab_id(payload)
        if not isinstance(key, str) or not key.strip():
            raise ToolValidationError("Field 'key' must be a non-empty string")
        return await manager.press_key(key.strip(), tab_id=tab_id)

    return ToolDefinition(
        name="browser_press_key",
        description="Press a keyboard key (e.g. Enter, Tab, Escape, ArrowDown). Uses Playwright key names.",
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "key"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "key": {"type": "string"},
                "tab_id": {"type": "string"},
            },
        },
        execute=_execute,
    )


def _browser_scroll_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        direction = payload.get("direction", "down")
        amount = payload.get("amount", 500)
        selector = payload.get("selector")
        tab_id = _optional_browser_tab_id(payload)
        if not isinstance(direction, str) or direction.strip().lower() not in {
            "up", "down", "left", "right",
        }:
            raise ToolValidationError("Field 'direction' must be one of: up, down, left, right")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ToolValidationError("Field 'amount' must be a positive integer (pixels)")
        if selector is not None and (not isinstance(selector, str) or not selector.strip()):
            raise ToolValidationError("Field 'selector' must be null or a non-empty string")
        return await manager.scroll(
            direction=direction.strip().lower(),
            amount=amount,
            selector=selector,
            tab_id=tab_id,
        )

    return ToolDefinition(
        name="browser_scroll",
        description=(
            "Scroll the page or a specific element. Direction can be up, down, left, or right. "
            "Amount is in pixels (default 500). Optionally provide a CSS selector to scroll "
            "within a specific container. Returns scroll position after scrolling."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                    "default": "down",
                },
                "amount": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 500,
                    "description": "Scroll distance in pixels",
                },
                "selector": {
                    "type": "string",
                    "description": "Optional CSS selector to scroll within a specific element",
                },
                "tab_id": {"type": "string"},
            },
        },
        execute=_execute,
    )


def _browser_get_text_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        selector = payload.get("selector")
        tab_id = _optional_browser_tab_id(payload)
        if selector is not None and (not isinstance(selector, str) or not selector.strip()):
            raise ToolValidationError("Field 'selector' must be null or a non-empty string")
        return await manager.get_text(
            selector.strip() if isinstance(selector, str) else None,
            tab_id=tab_id,
        )

    return ToolDefinition(
        name="browser_get_text",
        description=(
            "Extract visible text from the current page. "
            "Without a selector: uses Playwright's AI-optimized snapshot (clean accessibility tree, no CSS/JS noise). "
            "With a selector: extracts innerText from that specific element only — prefer this to limit output size. "
            "Output is capped at 10K chars. If truncated, use a specific selector to target the section you need. "
            "For finding interactive elements (buttons, links, inputs), use browser_snapshot with interactive_only=true instead."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "selector": {
                    "type": "string",
                    "description": "CSS selector to extract text from a specific element. Omit to get full page content.",
                },
                "tab_id": {"type": "string"},
            },
        },
        execute=_execute,
    )


def _browser_snapshot_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        interactive_only = payload.get("interactive_only", False)
        max_depth = payload.get("max_depth")
        tab_id = _optional_browser_tab_id(payload)
        if not isinstance(interactive_only, bool):
            raise ToolValidationError("Field 'interactive_only' must be a boolean")
        if max_depth is not None and (
            not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1
        ):
            raise ToolValidationError("Field 'max_depth' must be a positive integer")
        return await manager.get_snapshot(
            interactive_only=interactive_only,
            max_depth=max_depth,
            tab_id=tab_id,
        )

    return ToolDefinition(
        name="browser_snapshot",
        description=(
            "Capture the accessibility tree of the current page as a structured snapshot. "
            "Returns roles, names, URLs, and values for all elements — clean, no CSS/JS noise, capped at 10K chars. "
            "Use interactive_only=true to see ONLY clickable/fillable elements (buttons, links, inputs) — "
            "this is the most token-efficient option when you just need to know what to interact with. "
            "The returned role/name entries can be used directly with browser_click/browser_type "
            "(for example: 'button: Accept', 'textbox: Email'). "
            "Prefer this over browser_get_text for discovering page structure and interactive elements. "
            "If the snapshot returns empty, fall back to browser_get_text for page content."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "interactive_only": {
                    "type": "boolean",
                    "description": "If true, return only interactive elements (buttons, links, inputs, etc.). Much smaller output.",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "Maximum depth of the accessibility tree to return. Use 6 for efficient mode.",
                },
                "tab_id": {"type": "string"},
            },
        },
        execute=_execute,
    )


def _browser_reset_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        return await manager.reset()

    return ToolDefinition(
        name="browser_reset",
        description="Reset browser session to a clean about:blank state for recovery.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {**_BROWSER_SESSION_PROP},
        },
        execute=_execute,
    )


def _browser_tabs_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        return await manager.list_tabs()

    return ToolDefinition(
        name="browser_tabs",
        description=(
            "List all open browser tabs and the current active tab. "
            "Use this before focus/close operations, and after popups/open-in-new-tab flows."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {**_BROWSER_SESSION_PROP},
        },
        execute=_execute,
    )


def _browser_tab_open_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        raw_url = payload.get("url", "about:blank")
        if not isinstance(raw_url, str):
            raise ToolValidationError("Field 'url' must be a string")
        return await manager.open_tab(raw_url)

    return ToolDefinition(
        name="browser_tab_open",
        description=(
            "Open a new browser tab and make it active. "
            "If no URL is provided, opens about:blank."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "url": {
                    "type": "string",
                    "description": "Optional URL to open in the new tab.",
                },
            },
        },
        execute=_execute,
    )


def _browser_tab_focus_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        tab_id = payload.get("tab_id")
        if not isinstance(tab_id, str) or not tab_id.strip():
            raise ToolValidationError("Field 'tab_id' must be a non-empty string")
        return await manager.focus_tab(tab_id.strip())

    return ToolDefinition(
        name="browser_tab_focus",
        description="Focus an existing tab by tab_id and make it the active tab.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "tab_id"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "tab_id": {
                    "type": "string",
                    "description": "Tab identifier returned by browser_tabs.",
                },
            },
        },
        execute=_execute,
    )


def _browser_tab_close_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        tab_id = payload.get("tab_id")
        if not isinstance(tab_id, str) or not tab_id.strip():
            raise ToolValidationError("Field 'tab_id' must be a non-empty string")
        return await manager.close_tab(tab_id.strip())

    return ToolDefinition(
        name="browser_tab_close",
        description="Close a tab by tab_id. If it was active, the manager picks a fallback tab.",
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "tab_id"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "tab_id": {
                    "type": "string",
                    "description": "Tab identifier returned by browser_tabs.",
                },
            },
        },
        execute=_execute,
    )


def _browser_evaluate_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        expression = payload.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ToolValidationError("Field 'expression' must be a non-empty string")
        tab_id = payload.get("tab_id")
        return await manager.evaluate(expression.strip(), tab_id=tab_id)

    return ToolDefinition(
        name="browser_evaluate",
        description=(
            "Execute JavaScript in the browser page and return the result. "
            "Use this to read DOM state, manipulate elements, or run any JS expression."
        ),
        risk_level="high",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "expression"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "expression": {
                    "type": "string",
                    "description": "JavaScript expression or statement to evaluate in the page context.",
                },
                "tab_id": {
                    "type": "string",
                    "description": "Optional tab identifier. Defaults to the active tab.",
                },
            },
        },
        execute=_execute,
    )


def _browser_get_html_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        tab_id = payload.get("tab_id")
        return await manager.get_html(tab_id=tab_id)

    return ToolDefinition(
        name="browser_get_html",
        description="Return the full HTML source of the current page (or a specific tab).",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "tab_id": {
                    "type": "string",
                    "description": "Optional tab identifier. Defaults to the active tab.",
                },
            },
        },
        execute=_execute,
    )


def _browser_get_cookies_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        tab_id = payload.get("tab_id")
        return await manager.get_cookies(tab_id=tab_id)

    return ToolDefinition(
        name="browser_get_cookies",
        description="Return all cookies for the current browser context.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "tab_id": {
                    "type": "string",
                    "description": "Optional tab identifier. Defaults to the active tab.",
                },
            },
        },
        execute=_execute,
    )


def _browser_set_cookies_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        cookies = payload.get("cookies")
        if not isinstance(cookies, list) or not cookies:
            raise ToolValidationError("Field 'cookies' must be a non-empty array")
        return await manager.set_cookies(cookies)

    return ToolDefinition(
        name="browser_set_cookies",
        description="Set one or more cookies in the browser context.",
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "cookies"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "cookies": {
                    "type": "array",
                    "description": "Array of cookie objects to set.",
                    "items": {
                        "type": "object",
                        "required": ["name", "value"],
                        "properties": {
                            "name": {"type": "string"},
                            "value": {"type": "string"},
                            "url": {"type": "string"},
                            "domain": {"type": "string"},
                            "path": {"type": "string"},
                            "httpOnly": {"type": "boolean"},
                            "secure": {"type": "boolean"},
                            "sameSite": {"type": "string", "enum": ["Strict", "Lax", "None"]},
                        },
                    },
                },
            },
        },
        execute=_execute,
    )


def _browser_console_logs_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        tab_id = payload.get("tab_id")
        return await manager.get_console_logs(tab_id=tab_id)

    return ToolDefinition(
        name="browser_console_logs",
        description=(
            "Return captured browser console log entries (log, warn, error, info, debug) "
            "for the active tab or a specific tab."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "tab_id": {
                    "type": "string",
                    "description": "Optional tab identifier. Defaults to the active tab.",
                },
            },
        },
        execute=_execute,
    )


def _browser_network_intercept_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        url_pattern = payload.get("url_pattern")
        if not isinstance(url_pattern, str) or not url_pattern.strip():
            raise ToolValidationError("Field 'url_pattern' must be a non-empty string")
        tab_id = payload.get("tab_id")
        action = payload.get("action", "log")
        response_body = payload.get("response_body")
        response_status = int(payload.get("response_status", 200))
        return await manager.setup_network_intercept(
            url_pattern.strip(),
            action=action,
            response_body=response_body,
            response_status=response_status,
            tab_id=tab_id,
        )

    return ToolDefinition(
        name="browser_network_intercept",
        description=(
            "Set up network request interception for URLs matching a glob pattern. "
            "action='log' records requests (retrieve with browser_network_logs). "
            "action='block' aborts matching requests. "
            "action='mock' returns a static response body."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id", "url_pattern"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "url_pattern": {
                    "type": "string",
                    "description": "Glob pattern to match request URLs (e.g. '**/api/**', 'https://example.com/*').",
                },
                "action": {
                    "type": "string",
                    "enum": ["log", "block", "mock"],
                    "description": "What to do with matching requests: 'log' (default), 'block', or 'mock'.",
                    "default": "log",
                },
                "response_body": {
                    "type": "string",
                    "description": "Response body to return when action='mock'.",
                },
                "response_status": {
                    "type": "integer",
                    "description": "HTTP status code to return when action='mock' (default 200).",
                    "default": 200,
                },
                "tab_id": {
                    "type": "string",
                    "description": "Optional tab identifier. Defaults to the active tab.",
                },
            },
        },
        execute=_execute,
    )


def _browser_network_logs_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        tab_id = payload.get("tab_id")
        return await manager.get_network_logs(tab_id=tab_id)

    return ToolDefinition(
        name="browser_network_logs",
        description=(
            "Return captured network request/response logs for intercepted URLs. "
            "Set up interception first with browser_network_intercept."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "tab_id": {
                    "type": "string",
                    "description": "Optional tab identifier. Defaults to the active tab.",
                },
            },
        },
        execute=_execute,
    )


def _browser_clear_network_intercepts_tool(pool: BrowserPool) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        manager = await _resolve_browser_manager(pool, payload)
        tab_id = payload.get("tab_id")
        return await manager.clear_network_intercepts(tab_id=tab_id)

    return ToolDefinition(
        name="browser_clear_network_intercepts",
        description="Remove all active network interception routes and clear captured network logs.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                **_BROWSER_SESSION_PROP,
                "tab_id": {
                    "type": "string",
                    "description": "Optional tab identifier. Defaults to the active tab.",
                },
            },
        },
        execute=_execute,
    )
