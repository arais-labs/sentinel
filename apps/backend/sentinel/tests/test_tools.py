import asyncio
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("TOOL_FILE_READ_BASE_DIR", "/tmp")
os.environ.setdefault("SESSION_RUNTIME_BASE_DIR", "/tmp/sentinel-test-session-runtime")

from app.config import settings
from app.dependencies import get_db, get_manager_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import GitAccount
from app.models.araios import AraiosModule, AraiosModuleRecord, AraiosPermission
from app.models.system import SystemSetting
from app.services.araios.runtime_services import configure_runtime_services, reset_runtime_services
from app.services.araios.system_modules.git_tool import handlers as git_module
from app.services.araios.system_modules.module_manager import handlers as module_manager_module
from app.services.araios.system_modules.runtime_tool import handlers as runtime_tool_module
from app.services.instance_runtime_context import (
    InstanceRuntimeContext,
    instance_runtime_context_registry,
)
from app.services.runtime import session_runtime as session_runtime_module
from app.services.runtime.base import RuntimeExecResult
from app.services.runtime.base import RuntimeTerminalSession
from app.services.tools import ToolExecutor
from app.services.tools.executor import ToolExecutionError, ToolValidationError
from app.services.araios.system_modules.shared import validate_public_hostname as _validate_public_hostname
from app.services.tools.registry import ToolApprovalOutcome, ToolApprovalOutcomeStatus, ToolRuntimeContext
from app.services.tools.runtime_registry import build_runtime_registry
from tests.fake_db import FakeDB


def _make_token(*, sub: str, role: str = "agent", agent_id: str = "agent-test") -> str:
    secret = os.getenv("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
    return jwt.encode(
        {
            "sub": sub,
            "role": role,
            "agent_id": agent_id,
            "exp": 1999999999,
            "iat": 1771810000,
            "jti": str(uuid.uuid4()),
            "token_type": "access",
        },
        secret,
        algorithm="HS256",
    )


class _FakeSessionContext:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSessionFactory:
    def __init__(self, db):
        self._db = db

    def __call__(self):
        return _FakeSessionContext(self._db)


class _FakeRuntimeSSH:
    _next_pid = 40_000

    async def run(
        self,
        command: str,
        *,
        timeout: int = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        as_root: bool = False,
    ):
        _ = cwd, env, as_root
        if "setsid nohup bash -lc" in command and "echo $!" in command:
            stdout_path_match = re.search(r">\s*'([^']+\.stdout\.log)'", command)
            stderr_path_match = re.search(r"2>\s*'([^']+\.stderr\.log)'", command)
            exitcode_path_match = re.search(r">\s*'([^']+\.exitcode)'", command)
            stdout_path = stdout_path_match.group(1) if stdout_path_match else None
            stderr_path = stderr_path_match.group(1) if stderr_path_match else None
            exitcode_path = exitcode_path_match.group(1) if exitcode_path_match else None
            if stdout_path:
                os.makedirs(os.path.dirname(stdout_path), exist_ok=True)
                with open(stdout_path, "w", encoding="utf-8") as handle:
                    handle.write("")
            if stderr_path:
                os.makedirs(os.path.dirname(stderr_path), exist_ok=True)
                with open(stderr_path, "w", encoding="utf-8") as handle:
                    handle.write("")
            if exitcode_path:
                os.makedirs(os.path.dirname(exitcode_path), exist_ok=True)
                with open(exitcode_path, "w", encoding="utf-8") as handle:
                    handle.write("0")
            pid = self._next_pid
            type(self)._next_pid += 1
            return RuntimeExecResult(exit_status=0, stdout=f"{pid}\n", stderr="")
        if "sleep 3" in command and timeout <= 1:
            raise TimeoutError()
        if "tail -c" in command:
            match = re.search(r"(/[^'\"\\s]+\\.log)", command)
            if match:
                path = match.group(1)
                if os.path.exists(path):
                    with open(path, encoding="utf-8") as handle:
                        return RuntimeExecResult(exit_status=0, stdout=handle.read(), stderr="")
                return RuntimeExecResult(exit_status=0, stdout="", stderr="")
        if "echo hello" in command:
            return RuntimeExecResult(exit_status=0, stdout="hello\n", stderr="")
        if "echo root-approved" in command:
            return RuntimeExecResult(exit_status=0, stdout="root-approved\n", stderr="")
        if "echo blocked" in command:
            return RuntimeExecResult(exit_status=0, stdout="blocked\n", stderr="")
        return RuntimeExecResult(exit_status=0, stdout="", stderr="")

    async def run_detached(
        self,
        command: str,
        *,
        stdout_path: str,
        stderr_path: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        as_root: bool = False,
    ) -> int:
        _ = cwd, env, as_root
        os.makedirs(os.path.dirname(stdout_path), exist_ok=True)
        os.makedirs(os.path.dirname(stderr_path), exist_ok=True)
        stdout = ""
        stderr = ""
        exitcode_path = None
        match = re.search(r">\s*'([^']+\.exitcode)'", command)
        if match:
            exitcode_path = match.group(1)
        if "echo hello" in command:
            stdout = "hello\n"
        elif "echo root-approved" in command:
            stdout = "root-approved\n"
        elif "echo blocked" in command:
            stdout = "blocked\n"
        with open(stdout_path, "w", encoding="utf-8") as handle:
            handle.write(stdout)
        with open(stderr_path, "w", encoding="utf-8") as handle:
            handle.write(stderr)
        if exitcode_path:
            os.makedirs(os.path.dirname(exitcode_path), exist_ok=True)
            with open(exitcode_path, "w", encoding="utf-8") as handle:
                handle.write("0")
        pid = self._next_pid
        type(self)._next_pid += 1
        return pid


class _FakeRuntimeInstance:
    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        self.client = _FakeRuntimeSSH()
        self.host = "127.0.0.1"
        self.metadata = {"provider": "fake"}
        self.terminal = RuntimeTerminalSession(
            ssh=self.client,
            session_user="sentinel",
            workspace_path=workspace_path,
        )


class _FakeRuntimeProvider:
    def __init__(self):
        self._instances: dict[str, _FakeRuntimeInstance] = {}

    async def ensure(self, session_id):
        key = str(session_id)
        instance = self._instances.get(key)
        if instance is None:
            instance = _FakeRuntimeInstance("/tmp/fake-runtime/workspace")
            self._instances[key] = instance
        return instance

    def get(self, session_id):
        return self._instances.get(str(session_id))

    def get_host(self, session_id):
        instance = self.get(session_id)
        return instance.host if instance is not None else None

    def get_public_host(self, session_id):
        _ = session_id
        return "localhost"

    def resolve_port(self, session_id, internal_port):
        _ = session_id
        return int(internal_port)


def _install_app_tool_runtime(fake_db: FakeDB, *, approval_waiter=None):
    previous_registry = getattr(app.state, "tool_registry", None)
    previous_executor = getattr(app.state, "tool_executor", None)
    previous_agent_runtime_support = getattr(app.state, "agent_runtime_support", None)
    previous_db_factory = getattr(app.state, "db_factory", None)
    previous_get_runtime = runtime_tool_module.get_runtime
    previous_runtime_base_dir = session_runtime_module._RUNTIME_BASE_DIR
    from app.services.runtime import terminal_manager as _tm

    terminal_manager = _tm.get_terminal_manager()
    previous_terminal_run_command = terminal_manager.run_command
    previous_terminal_run_command_background = terminal_manager.run_command_background
    session_factory = _FakeSessionFactory(fake_db)
    runtime_tool_module.AsyncSessionLocal = session_factory
    git_module.AsyncSessionLocal = session_factory
    module_manager_module.AsyncSessionLocal = session_factory
    runtime_tool_module.get_runtime = lambda: _FakeRuntimeProvider()
    session_runtime_module._RUNTIME_BASE_DIR = Path(os.environ["SESSION_RUNTIME_BASE_DIR"]).expanduser()
    # The new unified runtime model routes all `runtime.user` calls through
    # TerminalManager → tmux pane in a real guest VM. In unit tests we don't
    # have a guest, so we monkeypatch the singleton's run_command path to
    # bypass tmux entirely and call the fake SSH client directly. This keeps
    # the test surface focused on the tool-layer behavior (validation,
    # approvals, schemas, result shape) without dragging in tmux mock plumbing.
    from app.services.runtime import terminal_manager as _tm
    from app.services.runtime.base import RuntimeExecResult as _RuntimeExecResult

    async def _fake_terminal_run(
        *,
        runtime,
        session_id,
        terminal_id,
        command,
        timeout,
        env=None,
        cwd=None,
        label_hint=None,
        created_by="agent",
        auto=False,
    ):
        _ = session_id, terminal_id, label_hint, created_by, auto
        # Delegate straight to the fake SSH stub, which has pattern-matched
    # responses for the canned commands used in tests.
        return await runtime.terminal.ssh.run(
            command,
            cwd=cwd or runtime.terminal.workspace_path,
            env=env or None,
            timeout=timeout,
            as_root=runtime.terminal.session_user == "root",
        )

    terminal_manager.run_command = _fake_terminal_run  # type: ignore[method-assign]

    async def _fake_terminal_run_background(
        *,
        runtime,
        session_id,
        terminal_id,
        command,
        timeout,
        env=None,
        cwd=None,
        label_hint=None,
        created_by="agent",
        auto=False,
    ):
        _ = runtime, session_id, command, timeout, env, cwd, label_hint, created_by, auto
        return {
            "ok": True,
            "background": True,
            "terminal_id": terminal_id,
            "started_at": "2026-05-11T00:00:00+00:00",
        }

    terminal_manager.run_command_background = _fake_terminal_run_background  # type: ignore[method-assign]
    reset_runtime_services()
    configure_runtime_services(app_state=app.state)
    registry = asyncio.run(build_runtime_registry(session_factory=session_factory))
    app.state.tool_registry = registry
    app.state.tool_executor = ToolExecutor(registry, approval_waiter=approval_waiter)
    app.state.db_session_factory = session_factory
    return previous_registry, previous_executor, (
        previous_get_runtime,
        previous_agent_runtime_support,
        previous_db_factory,
        previous_runtime_base_dir,
        terminal_manager,
        previous_terminal_run_command,
        previous_terminal_run_command_background,
    )


def _restore_app_tool_runtime(previous_registry, previous_executor, previous_runtime_state) -> None:
    (
        previous_get_runtime,
        previous_agent_runtime_support,
        previous_db_factory,
        previous_runtime_base_dir,
        terminal_manager,
        previous_terminal_run_command,
        previous_terminal_run_command_background,
    ) = previous_runtime_state
    reset_runtime_services()
    runtime_tool_module.get_runtime = previous_get_runtime
    session_runtime_module._RUNTIME_BASE_DIR = previous_runtime_base_dir
    terminal_manager.run_command = previous_terminal_run_command  # type: ignore[method-assign]
    terminal_manager.run_command_background = previous_terminal_run_command_background  # type: ignore[method-assign]
    app.state.tool_registry = previous_registry
    app.state.tool_executor = previous_executor
    app.state.agent_runtime_support = previous_agent_runtime_support
    app.state.db_factory = previous_db_factory
    app.state.db_session_factory = None


def _runtime_input(
    *,
    shell_command: str,
    action: str = "user",
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {"command": action, "shell_command": shell_command}
    payload.update(extra)
    return payload


class _ToolResponse:
    def __init__(self, status_code: int, payload: dict[str, object]):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


def _tool_error(message: str) -> dict[str, object]:
    return {"detail": message, "error": {"message": message}}


def _execute_tool_for_test(
    name: str,
    payload: dict[str, object],
    *,
    session_id: str | None = None,
) -> _ToolResponse:
    runtime = ToolRuntimeContext(session_id=uuid.UUID(session_id) if session_id else None)
    executor = app.state.tool_executor
    try:
        result, duration_ms = asyncio.run(executor.execute(name, payload, runtime=runtime))
    except KeyError:
        return _ToolResponse(404, _tool_error("Tool not found"))
    except ToolValidationError as exc:
        return _ToolResponse(422, _tool_error(str(exc)))
    except ToolExecutionError as exc:
        return _ToolResponse(400, _tool_error(str(exc)))
    except PermissionError as exc:
        return _ToolResponse(403, _tool_error(str(exc)))
    return _ToolResponse(200, {"result": result, "duration_ms": duration_ms})


def _registered_tool_names() -> set[str]:
    return {tool.name for tool in app.state.tool_registry.list_all()}


def _registered_tool_schema(name: str) -> dict[str, object]:
    tool = app.state.tool_registry.get(name)
    assert tool is not None
    return tool.parameters_schema


def test_tools_registry_and_execution():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        names = _registered_tool_names()
        assert {
            "http_request",
            "runtime",
            "git",
            "str_replace_editor",
            "browser",
            "module_manager",
        } <= names

        schema = _registered_tool_schema("http_request")
        assert "url" in schema["properties"]

        invalid = _execute_tool_for_test("http_request", {})
        assert invalid.status_code == 422

        fake_db.add(
            GitAccount(
                name="primary-gh",
                host="github.com",
                scope_pattern="arais-labs/*",
                author_name="Ari",
                author_email="ari@arais.us",
                token_read="read-token",
                token_write="write-token",
            )
        )
        accounts_run = _execute_tool_for_test(
            "git",
            {"command": "accounts", "repo_url": "https://github.com/arais-labs/sentinel.git"},
        )
        assert accounts_run.status_code == 200
        payload = accounts_run.json()["result"]
        assert payload["total"] == 1
        assert payload["accounts"][0]["name"] == "primary-gh"
        assert payload["accounts"][0]["matches_repo"] is True

    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_tools_http_api_is_not_exposed():
    client = TestClient(app)
    response = client.get("/api/v1/tools")
    assert response.status_code == 404

    user_token = _make_token(sub="standard-user")
    auth = {"Authorization": f"Bearer {user_token}"}
    response = client.get("/api/v1/tools", headers=auth)
    assert response.status_code == 404


def test_runtime_runs_command():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/instances/main/sessions",
            json={"title": "runtime-exec-smoke"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = _execute_tool_for_test(
            "runtime",
            _runtime_input(shell_command="echo hello"),
            session_id=session_id,
        )
        assert run.status_code == 200, run.json()
        payload = run.json()["result"]
        assert payload["ok"] is True
        assert "hello" in payload["stdout"]

        runtime = client.get(f"/api/v1/instances/main/sessions/{session_id}/runtime", headers=headers)
        assert runtime.status_code == 200
        actions = runtime.json().get("actions", [])
        finished_actions = [item for item in actions if item.get("action") == "command_finished"]
        assert finished_actions
        details = finished_actions[-1].get("details", {})
        assert details.get("ok") is True
        assert details.get("timed_out") is False
        assert details.get("returncode") == 0
        assert "hello" in str(details.get("stdout", ""))
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_background_returns_immediately_with_handle():
    """`background=true` returns a terminal handle synchronously — the agent
    doesn't wait for completion. Completion arrives later as a wakeup turn.
    """
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/instances/main/sessions",
            json={"title": "runtime-exec-background"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = _execute_tool_for_test(
            "runtime",
            _runtime_input(
                shell_command="sleep 30",
                background=True,
                terminal_id="bg-test",
            ),
            session_id=session_id,
        )
        assert run.status_code == 200
        payload = run.json()["result"]
        assert payload["ok"] is True
        assert payload["background"] is True
        assert payload["terminal_id"] == "bg-test"
        assert "DO NOT poll" in payload["message"]
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_background_auto_allocates_bg_terminal_id():
    """Background without terminal_id auto-allocates `bg-<token>`."""
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/instances/main/sessions",
            json={"title": "runtime-bg-auto"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = _execute_tool_for_test(
            "runtime",
            _runtime_input(shell_command="sleep 1", background=True),
            session_id=session_id,
        )
        assert run.status_code == 200
        payload = run.json()["result"]
        assert payload["background"] is True
        assert payload["terminal_id"].startswith("bg-")
        assert payload["terminal_auto"] is True
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_background_rejects_terminal_zero():
    """background=true + terminal_id='0' must be refused — the user's main shell
    is shared and a long-running command would clobber it.
    """
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/instances/main/sessions",
            json={"title": "runtime-bg-zero-reject"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = _execute_tool_for_test(
            "runtime",
            _runtime_input(shell_command="sleep 1", background=True, terminal_id="0"),
            session_id=session_id,
        )
        assert run.status_code == 422
        assert "terminal_id='0'" in run.json()["error"]["message"]
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_user_rejects_reserved_root_terminal():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/instances/main/sessions",
            json={"title": "runtime-root-terminal-reserved"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = _execute_tool_for_test(
            "runtime",
            _runtime_input(shell_command="whoami", terminal_id="root"),
            session_id=session_id,
        )
        assert run.status_code == 422
        assert "reserved for runtime.root" in run.json()["error"]["message"]
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_terminal_close_refuses_terminal_zero():
    """Terminal '0' is the user's primary shared shell — closing it from the
    agent would yank the user's main interactive context out from under them.
    The handler must refuse before touching the manager.
    """
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/instances/main/sessions",
            json={"title": "terminal-close-zero"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = _execute_tool_for_test(
            "runtime",
            {"command": "terminal_close", "terminal_id": "0"},
            session_id=session_id,
        )
        assert run.status_code == 422
        message = run.json()["error"]["message"]
        assert "terminal '0'" in message.lower() or "primary" in message.lower()
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_terminal_close_calls_manager_terminate():
    """Closing a named terminal forwards to TerminalManager.terminate exactly
    once with the right (session_id, terminal_id), and reports `closed` based
    on whether the record existed before the call.
    """
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    from app.services.runtime import terminal_manager as _tm

    manager = _tm.get_terminal_manager()
    calls: list[tuple[str, str]] = []

    async def _fake_terminate(session_id, *, terminal_id=None):
        calls.append((str(session_id), str(terminal_id)))

    # Make the "did it exist?" probe report a known terminal so the response
    # carries closed=true. The handler reads from list_terminals; we substitute
    # a minimal stub for that pair only.
    class _FakeRecord:
        def __init__(self, tid: str) -> None:
            self.terminal_id = tid

    original_list = manager.list_terminals
    original_terminate = manager.terminate
    manager.list_terminals = lambda _sid: [_FakeRecord("build")]  # type: ignore[method-assign]
    manager.terminate = _fake_terminate  # type: ignore[method-assign]

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/instances/main/sessions",
            json={"title": "terminal-close-named"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = _execute_tool_for_test(
            "runtime",
            {"command": "terminal_close", "terminal_id": "build"},
            session_id=session_id,
        )
        assert run.status_code == 200
        result = run.json()["result"]
        assert result["terminal_id"] == "build"
        assert result["closed"] is True
        assert len(calls) == 1
        assert calls[0][1] == "build"

        # When the terminal isn't currently known, the call still succeeds but
        # reports closed=false. Switch the stub to return no records and try
        # again to lock that branch down.
        manager.list_terminals = lambda _sid: []  # type: ignore[method-assign]
        run2 = _execute_tool_for_test(
            "runtime",
            {"command": "terminal_close", "terminal_id": "ghost"},
            session_id=session_id,
        )
        assert run2.status_code == 200
        result2 = run2.json()["result"]
        assert result2["closed"] is False
    finally:
        manager.list_terminals = original_list  # type: ignore[method-assign]
        manager.terminate = original_terminate  # type: ignore[method-assign]
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_terminal_close_rejects_invalid_terminal_id():
    """terminal_id must match the [a-zA-Z0-9_-]{1,32} pattern. The tool
    validation layer should fail before we reach the manager.
    """
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/instances/main/sessions",
            json={"title": "terminal-close-invalid"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = _execute_tool_for_test(
            "runtime",
            {"command": "terminal_close", "terminal_id": "../escape"},
            session_id=session_id,
        )
        assert run.status_code == 422
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_rejects_shell_background_without_background_flag():
    """A trailing `&` in shell_command should be refused unless background=true.

    The unified model wants the agent to be EXPLICIT about background work
    via the tool flag, not via shell trickery. The rejection message points
    at the right escape hatch.
    """
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/instances/main/sessions",
            json={"title": "runtime-exec-bg-reject"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = _execute_tool_for_test(
            "runtime",
            {"command": "user", "shell_command": "sleep 1 &"},
            session_id=session_id,
        )
        assert run.status_code == 422
        assert "background=true" in run.json()["error"]["message"]
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_background_detector_allows_fd_redirection():
    assert runtime_tool_module._command_requests_background_execution("whoami") is False
    assert runtime_tool_module._command_requests_background_execution("echo hi && whoami") is False
    assert runtime_tool_module._command_requests_background_execution("which git python3 node npm curl docker 2>&1") is False
    assert runtime_tool_module._command_requests_background_execution("cat /etc/os-release && which git python3 node npm curl docker 2>&1") is False
    assert runtime_tool_module._command_requests_background_execution("command 1>&2") is False


def test_runtime_background_detector_real_backgrounding():
    # The actually-backgrounded shapes — every one of these has to keep being flagged.
    assert runtime_tool_module._command_requests_background_execution("sleep 30 &") is True
    assert runtime_tool_module._command_requests_background_execution("sleep 30 >/dev/null 2>&1 &") is True
    assert runtime_tool_module._command_requests_background_execution("cmd1 & cmd2") is True
    assert runtime_tool_module._command_requests_background_execution("nohup ./run.sh") is True
    assert runtime_tool_module._command_requests_background_execution("./run.sh; disown") is True


def test_runtime_background_detector_ignores_ampersand_in_quotes_and_heredocs():
    # The three false-positive shapes reported by the user: `&` is inside a
    # quoted string or heredoc body, so bash treats it as literal — we must too.

    # Single-quoted ampersand inside a sentence
    assert runtime_tool_module._command_requests_background_execution(
        "echo 'AT&T plus bash quoting'"
    ) is False

    # Double-quoted Python -c containing & in a string literal
    assert runtime_tool_module._command_requests_background_execution(
        "python3 -c \"print('!@#$%^&*()')\""
    ) is False

    # `python3 << 'EOF' … EOF` heredoc body with & inside
    heredoc = (
        "python3 << 'EOF'\n"
        "import sys\n"
        "print('!@#%^&*()')\n"
        "EOF"
    )
    assert runtime_tool_module._command_requests_background_execution(heredoc) is False

    # `<<-EOF` indented heredoc form, & inside body
    heredoc_dash = (
        "python3 <<-EOF\n"
        "\tprint('&')\n"
        "\tEOF"
    )
    assert runtime_tool_module._command_requests_background_execution(heredoc_dash) is False

    # `nohup` mentioned inside a quoted string is NOT a real nohup invocation
    assert runtime_tool_module._command_requests_background_execution(
        "echo 'the nohup pattern'"
    ) is False

    # Escaped \& outside quotes is not the operator either
    assert runtime_tool_module._command_requests_background_execution(
        "echo a \\& b"
    ) is False


def test_runtime_allows_inline_fd_redirection():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/instances/main/sessions",
            json={"title": "runtime-inline-redirection"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = _execute_tool_for_test(
            "runtime",
            {"command": "user", "shell_command": "which git python3 node npm curl docker 2>&1"},
            session_id=session_id,
        )
        assert run.status_code == 200
        assert run.json()["result"]["ok"] is True
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_timeout_returns_result():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/instances/main/sessions",
            json={"title": "runtime-exec-timeout"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = _execute_tool_for_test(
            "runtime",
            _runtime_input(shell_command="sleep 3", timeout_seconds=1),
            session_id=session_id,
        )
        assert run.status_code == 200
        payload = run.json()["result"]
        assert payload["timed_out"] is True
        assert payload["ok"] is False
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_root_privilege_requires_approval():
    fake_db = FakeDB()
    waiter_seen: dict[str, object] = {}

    async def _fake_waiter(_tool_name, _payload, _runtime, requirement, _pending_callback=None):
        waiter_seen["action"] = requirement.action
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.APPROVED,
            approval={
                "provider": "runtime",
                "approval_id": "apr_runtime_root",
                "status": "approved",
                "pending": False,
                "can_resolve": False,
            },
            message="approved",
        )

    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(
        fake_db,
        approval_waiter=_fake_waiter,
    )

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/instances/main/sessions",
            json={"title": "runtime-exec-root-approval"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = _execute_tool_for_test(
            "runtime",
            {
                "command": "root",
                "shell_command": "echo root-approved",
                "terminal_id": "install-builder",
            },
            session_id=session_id,
        )
        assert run.status_code == 200
        payload = run.json()["result"]
        assert payload["ok"] is True
        assert "root-approved" in payload["stdout"]
        assert payload["terminal_id"] == "root"
        assert payload["terminal_auto"] is False
        assert payload["approval"]["provider"] == "runtime"
        assert payload["approval"]["approval_id"] == "apr_runtime_root"
        assert waiter_seen["action"] == "runtime.root"
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_removed_file_read_tool_returns_not_found():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = _execute_tool_for_test("file_read", {"path": "/etc/passwd"})
        assert response.status_code == 404
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_http_request_ssrf_blocked():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = _execute_tool_for_test(
            "http_request",
            {"url": "http://127.0.0.1:8000/health", "method": "GET"},
        )
        assert response.status_code == 422
        assert "SSRF blocked" in response.json()["error"]["message"]
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_http_request_ssrf_allow_hosts_bypasses_private_check():
    previous = os.environ.get("SSRF_ALLOW_HOSTS")
    os.environ["SSRF_ALLOW_HOSTS"] = "sentinel-backend"
    try:
        asyncio.run(_validate_public_hostname("sentinel-backend"))
    finally:
        if previous is None:
            os.environ.pop("SSRF_ALLOW_HOSTS", None)
        else:
            os.environ["SSRF_ALLOW_HOSTS"] = previous


class _FakeHttpxResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"content-type": "application/json"}
        self.content = json.dumps(payload).encode("utf-8")
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_module_manager_lists_db_modules():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        fake_db.add(AraiosModule(name="notes", label="Notes", description="d", icon="file-text"))
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = _execute_tool_for_test("module_manager", {"command": "list_modules"})
        assert response.status_code == 200
        modules = response.json()["result"]["modules"]
        assert any(item["name"] == "notes" for item in modules)
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_module_manager_requires_command():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = _execute_tool_for_test("module_manager", {})
        assert response.status_code == 422
        assert "command" in response.json()["error"]["message"]
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_module_manager_create_registers_dynamic_tool_and_permissions():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        create_payload = {
            "command": "create_module",
            "name": "clients",
            "label": "Clients",
            "description": "Track customers",
            "actions": [
                {
                    "id": "sync_hubspot",
                    "label": "Sync HubSpot",
                    "params": [{"key": "source", "label": "Source", "required": True}],
                    "code": "result = {'ok': True, 'source': params['source']}",
                },
                {
                    "id": "archive",
                    "label": "Archive",
                    "type": "record",
                    "code": "result = {'ok': True, 'record_id': record['id']}",
                },
            ],
            "page_title": "Clients",
            "page_content": "# Clients",
            "permissions": {
                "delete_records": "deny",
                "edit_page": "approval",
                "sync_hubspot": "allow",
                "archive": "approval",
            },
        }
        created = _execute_tool_for_test("module_manager", create_payload)
        assert created.status_code == 200
        result = created.json()["result"]
        assert result["module"] == "clients"
        assert result["permissions"]["delete_records"] == "deny"
        assert result["permissions"]["archive"] == "approval"

        permission_rows = {
            row.action: row.level
            for row in fake_db.storage[AraiosPermission]
            if row.action.startswith("clients.")
        }
        assert permission_rows["clients.list_records"] == "allow"
        assert permission_rows["clients.delete_records"] == "deny"
        assert permission_rows["clients.edit_page"] == "approval"
        assert permission_rows["clients.sync_hubspot"] == "allow"
        assert permission_rows["clients.archive"] == "approval"

        names = _registered_tool_names()
        assert "clients" in names

        command_enum = _registered_tool_schema("clients")["properties"]["command"]["enum"]
        assert "list_records" in command_enum
        assert "get_page" in command_enum
        assert "edit_page" in command_enum
        assert "sync_hubspot" in command_enum
        assert "archive" in command_enum

        created_record = _execute_tool_for_test(
            "clients",
            {"command": "create_records", "records": [{"name": "Acme"}]},
        )
        assert created_record.status_code == 200
        record_id = created_record.json()["result"]["records"][0]["id"]

        run_custom = _execute_tool_for_test("clients", {"command": "sync_hubspot", "source": "crm"})
        assert run_custom.status_code == 200
        assert run_custom.json()["result"]["source"] == "crm"

        get_page = _execute_tool_for_test("clients", {"command": "get_page"})
        assert get_page.status_code == 200
        assert get_page.json()["result"]["page_content"] == "# Clients"

        denied_delete = _execute_tool_for_test(
            "clients",
            {"command": "delete_records", "record_ids": [record_id]},
        )
        assert denied_delete.status_code == 400
        denied_payload = denied_delete.json()
        denied_message = denied_payload.get("detail") or denied_payload.get("error", {}).get("message", "")
        assert "denied" in str(denied_message).lower()
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_module_manager_create_refreshes_instance_runtime_context():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)
    context = InstanceRuntimeContext(
        name="main",
        database_name="sentinel_main_0d6e4079",
        instance_settings=settings,
        session_factory=app.state.db_session_factory,
        tool_registry=app.state.tool_registry,
        tool_executor=app.state.tool_executor,
        agent_runtime_support=None,
        trigger_scheduler=object(),
        sub_agent_orchestrator=object(),
        background_tasks=[],
    )
    instance_runtime_context_registry._contexts["main"] = context
    try:
        created = _execute_tool_for_test(
            "module_manager",
            {
                "command": "create_module",
                "name": "clients",
                "label": "Clients",
                "description": "Track customers",
                "actions": [
                    {
                        "id": "sync",
                        "label": "Sync",
                        "code": "result = {'ok': True}",
                    },
                ],
            },
        )
        assert created.status_code == 200

        rebuilt = instance_runtime_context_registry.get("main")
        assert rebuilt is not None
        assert rebuilt is not context
        assert rebuilt.tool_registry.get("clients") is not None
        assert app.state.tool_registry.get("clients") is None
    finally:
        asyncio.run(instance_runtime_context_registry.remove("main"))
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)


def test_module_manager_rejects_reserved_dynamic_action_names():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = _execute_tool_for_test(
            "module_manager",
            {
                "command": "create_module",
                "name": "badmodule",
                "label": "Bad Module",
                "actions": [{"id": "create_records", "label": "Nope", "code": "result={'ok': True}"}],
            },
        )
        assert response.status_code == 400
        payload = response.json()
        message = payload.get("detail") or payload.get("error", {}).get("message", "")
        assert "reserved command name" in str(message).lower()
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_registry_skips_invalid_dynamic_module():
    fake_db = FakeDB()
    bad = AraiosModule(
        name="broken_module",
        label="Broken Module",
        description="bad schema merge",
        icon="box",
        actions=[],
    )
    fake_db.add(bad)
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    try:
        conflict_actions = [
            {
                "id": "one",
                "label": "One",
                "params": [{"key": "data", "label": "Data", "required": True}],
                "code": "result = {'ok': True}",
            },
            {
                "id": "two",
                "label": "Two",
                "params": [{"key": "data", "label": "Patch Data", "required": True}],
                "code": "result = {'ok': True}",
            },
        ]
        bad.actions = conflict_actions
        registry = asyncio.run(build_runtime_registry(session_factory=_FakeSessionFactory(fake_db)))
        assert registry.get("broken_module") is None
        assert registry.get("module_manager") is not None
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)


def test_runtime_registry_excludes_system_modules_from_dynamic_loader():
    fake_db = FakeDB()
    system_module = AraiosModule(
        name="system_only_module",
        label="System Only Module",
        description="should not compile through dynamic loader",
        icon="box",
        actions=[
            {
                "id": "ping",
                "label": "Ping",
                "code": "result = {'ok': True}",
            }
        ],
        system=True,
    )
    fake_db.add(system_module)
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    try:
        registry = asyncio.run(build_runtime_registry(session_factory=_FakeSessionFactory(fake_db)))
        assert registry.get("system_only_module") is None
        assert registry.get("module_manager") is not None
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)


def test_permissions_api_lists_static_system_and_dynamic_actions():
    fake_db = FakeDB()
    fake_db.add(
        AraiosModule(
            name="clients",
            label="Clients",
            description="Track customers",
            icon="box",
            actions=[
                {
                    "id": "sync_hubspot",
                    "label": "Sync HubSpot",
                    "code": "result = {'ok': True}",
                },
                {
                    "id": "archive",
                    "label": "Archive",
                    "type": "record",
                    "code": "result = {'ok': True}",
                },
            ],
            system=False,
        )
    )
    fake_db.add(AraiosPermission(action="clients.archive", level="deny"))

    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.get("/api/v1/instances/main/permissions", headers=headers)
        assert response.status_code == 200
        permissions = {item["action"]: item["level"] for item in response.json()["permissions"]}

        assert permissions["modules.create"] == "approval"
        assert permissions["browser.navigate"] == "allow"
        assert permissions["clients.list_records"] == "allow"
        assert permissions["clients.create_records"] == "allow"
        assert permissions["clients.delete_records"] == "approval"
        assert permissions["clients.sync_hubspot"] == "allow"
        assert permissions["clients.archive"] == "deny"
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_permissions_api_updates_known_action_and_rejects_unknown_action():
    fake_db = FakeDB()
    fake_db.add(
        AraiosModule(
            name="clients",
            label="Clients",
            description="Track customers",
            icon="box",
            actions=[],
            system=False,
        )
    )
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        update = client.patch("/api/v1/instances/main/permissions/clients.create_records", json={"level": "approval"}, headers=headers)
        assert update.status_code == 200
        assert update.json() == {"action": "clients.create_records", "level": "approval"}

        stored = next(row for row in fake_db.storage[AraiosPermission] if row.action == "clients.create_records")
        assert stored.level == "approval"

        missing = client.patch("/api/v1/instances/main/permissions/clients.not_real", json={"level": "deny"}, headers=headers)
        assert missing.status_code == 404
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_modules_import_package_creates_module_records_and_permissions():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        package = {
            "schema_version": 1,
            "package_version": "0.1.0",
            "module": {
                "name": "lead_manager",
                "label": "Lead Manager",
                "description": "Track leads",
                "icon": "Users",
                "fields": [
                    {"key": "full_name", "label": "Full Name", "type": "text", "required": True},
                    {"key": "company", "label": "Company", "type": "text", "required": True},
                ],
                "fields_config": {"titleField": "full_name", "subtitleField": "company"},
                "actions": [
                    {
                        "id": "qualify_lead",
                        "label": "Qualify Lead",
                        "type": "record",
                        "code": "result = {'ok': True, 'record_id': record['id']}",
                    }
                ],
                "page_title": "Lead Manager Guide",
                "page_content": "# Lead Manager",
            },
            "records": [
                {"full_name": "Dana Brooks", "company": "Northstar"},
                {"full_name": "Luis Ortega", "company": "Veritas"},
            ],
            "permissions": {
                "delete_records": "approval",
                "qualify_lead": "allow",
            },
        }

        response = client.post("/api/v1/instances/main/modules/import", json=package, headers=headers)
        assert response.status_code == 201
        payload = response.json()
        assert payload["module"]["name"] == "lead_manager"
        assert payload["imported_records"] == 2
        assert payload["permissions"]["qualify_lead"] == "allow"

        stored_module = next(row for row in fake_db.storage[AraiosModule] if row.name == "lead_manager")
        assert stored_module.label == "Lead Manager"
        assert len([row for row in fake_db.storage[AraiosPermission] if row.action.startswith("lead_manager.")]) >= 1
        records = [row for row in fake_db.storage[AraiosModuleRecord] if row.module_name == "lead_manager"]
        assert len(records) == 2
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_modules_import_package_rejects_system_true():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.post(
            "/api/v1/instances/main/modules/import",
            json={
                "schema_version": 1,
                "module": {
                    "name": "bad_system_import",
                    "label": "Bad System Import",
                    "system": True,
                },
            },
            headers=headers,
        )
        assert response.status_code == 400
        payload = response.json()
        message = payload.get("detail") or payload.get("error", {}).get("message", "")
        assert "system=true" in str(message)
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_module_manager_grouped_tool_accepts_runtime_context_for_non_session_commands():
    fake_db = FakeDB()
    previous_registry, previous_executor, previous_get_runtime = _install_app_tool_runtime(fake_db)

    try:
        registry = asyncio.run(build_runtime_registry(session_factory=_FakeSessionFactory(fake_db)))
        executor = ToolExecutor(registry)
        result, _duration = asyncio.run(
            executor.execute(
                "module_manager",
                {"command": "list_modules"},
                runtime=ToolRuntimeContext(session_id=uuid.UUID("7eeab26f-1f5e-4964-98d8-201bf66c38a1")),
            )
        )
        assert result == {"modules": []}
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
