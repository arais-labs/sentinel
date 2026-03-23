import asyncio
import json
import os
import sys
import tempfile
import types
import uuid
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("TOOL_FILE_READ_BASE_DIR", "/tmp")
if "asyncssh" not in sys.modules:
    async def _asyncssh_connect_stub(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("asyncssh stub should not be called in test_tools")

    sys.modules["asyncssh"] = types.SimpleNamespace(
        SSHClientConnection=object,
        Error=RuntimeError,
        connect=_asyncssh_connect_stub,
    )

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import GitAccount
from app.models.araios import AraiosModule
from app.models.system import SystemSetting
from app.services.araios.runtime_services import configure_runtime_services, reset_runtime_services
from app.services.araios.system_modules.git_exec import handlers as git_exec_module
from app.services.araios.system_modules.module_manager import handlers as module_manager_module
from app.services.araios.system_modules.runtime_exec import handlers as runtime_exec_module
from app.services.runtime.ssh_client import SSHExecResult
from app.services.tools import ToolExecutor
from app.services.araios.system_modules.shared import validate_public_hostname as _validate_public_hostname
from app.services.tools.registry import ToolApprovalOutcome, ToolApprovalOutcomeStatus
from app.services.tools.registry_builder import build_default_registry
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

    async def run(self, command: str, *, timeout: int = 300, cwd: str | None = None, env: dict[str, str] | None = None):
        _ = cwd, env
        if "sleep 3" in command and timeout <= 1:
            raise TimeoutError()
        if "echo hello" in command:
            return SSHExecResult(exit_status=0, stdout="hello\n", stderr="")
        if "echo root-approved" in command:
            return SSHExecResult(exit_status=0, stdout="root-approved\n", stderr="")
        if "echo blocked" in command:
            return SSHExecResult(exit_status=0, stdout="blocked\n", stderr="")
        return SSHExecResult(exit_status=0, stdout="", stderr="")

    async def run_detached(
        self,
        command: str,
        *,
        stdout_path: str,
        stderr_path: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        _ = command, stdout_path, stderr_path, cwd, env
        pid = self._next_pid
        type(self)._next_pid += 1
        return pid


class _FakeRuntimeInstance:
    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        self.ssh = _FakeRuntimeSSH()


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


def _install_app_tool_runtime(fake_db: FakeDB, *, approval_waiter=None):
    previous_registry = getattr(app.state, "tool_registry", None)
    previous_executor = getattr(app.state, "tool_executor", None)
    previous_get_runtime = runtime_exec_module.get_runtime
    session_factory = _FakeSessionFactory(fake_db)
    runtime_exec_module.AsyncSessionLocal = session_factory
    git_exec_module.AsyncSessionLocal = session_factory
    module_manager_module.AsyncSessionLocal = session_factory
    runtime_exec_module.get_runtime = lambda: _FakeRuntimeProvider()
    reset_runtime_services()
    configure_runtime_services(app_state=app.state)
    registry = build_default_registry(session_factory=session_factory)
    app.state.tool_registry = registry
    app.state.tool_executor = ToolExecutor(registry, approval_waiter=approval_waiter)
    return previous_registry, previous_executor, previous_get_runtime


def _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime) -> None:
    reset_runtime_services()
    runtime_exec_module.get_runtime = previous_get_runtime
    app.state.tool_registry = previous_registry
    app.state.tool_executor = previous_executor


def _runtime_exec_input(
    *,
    shell_command: str,
    session_id: str,
    action: str = "run_user",
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {"command": action, "shell_command": shell_command, "session_id": session_id}
    payload.update(extra)
    return payload


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

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        listed = client.get("/api/v1/tools", headers=headers)
        assert listed.status_code == 200
        names = {item["name"] for item in listed.json()["items"]}
        assert {
            "http_request",
            "runtime_exec",
            "git_exec",
            "str_replace_editor",
            "browser",
            "module_manager",
        } <= names

        detail = client.get("/api/v1/tools/http_request", headers=headers)
        assert detail.status_code == 200
        schema = detail.json()["parameters_schema"]
        assert "url" in schema["properties"]

        invalid = client.post(
            "/api/v1/tools/http_request/execute",
            json={"input": {}},
            headers=headers,
        )
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
        accounts_run = client.post(
            "/api/v1/tools/git_exec/execute",
            json={"input": {"command": "accounts", "repo_url": "https://github.com/arais-labs/sentinel.git"}},
            headers=headers,
        )
        assert accounts_run.status_code == 200
        payload = accounts_run.json()["result"]
        assert payload["total"] == 1
        assert payload["accounts"][0]["name"] == "primary-gh"
        assert payload["accounts"][0]["matches_repo"] is True

        created_session = client.post(
            "/api/v1/sessions",
            json={"title": "tools-runtime-exec-estop"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        estop = client.post("/api/v1/admin/estop", headers=headers)
        assert estop.status_code == 200
        blocked = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={"input": {"command": "run_user", "shell_command": "echo blocked", "session_id": session_id}},
            headers=headers,
        )
        assert blocked.status_code == 200
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_tool_auth_required():
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

    try:
        client = TestClient(app)
        response = client.get("/api/v1/tools")
        assert response.status_code == 401

        user_token = _make_token(sub="standard-user")
        auth = {"Authorization": f"Bearer {user_token}"}
        response = client.get("/api/v1/tools", headers=auth)
        assert response.status_code == 200
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_exec_runs_command():
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

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/sessions",
            json={"title": "runtime-exec-smoke"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={"input": _runtime_exec_input(shell_command="echo hello", session_id=session_id)},
            headers=headers,
        )
        assert run.status_code == 200
        payload = run.json()["result"]
        assert payload["ok"] is True
        assert "hello" in payload["stdout"]

        runtime = client.get(f"/api/v1/sessions/{session_id}/runtime", headers=headers)
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


def test_runtime_exec_detached_job_lifecycle():
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

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/sessions",
            json={"title": "runtime-exec-detached"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={
                "input": _runtime_exec_input(
                    shell_command="sleep 30",
                    session_id=session_id,
                    detached=True,
                )
            },
            headers=headers,
        )
        assert run.status_code == 200
        payload = run.json()["result"]
        assert payload["ok"] is True
        assert payload["detached"] is True
        job_id = payload["job"]["id"]
        if payload["privilege"] == "user":
            assert str(payload["workspace"]).endswith("/workspace")
            assert str(payload["cwd"]).endswith("/workspace")
            assert str(payload["job"]["cwd"]).endswith("/workspace")
            assert "/workspace/" in str(payload["job"].get("stdout_path", ""))
            assert "/workspace/" in str(payload["job"].get("stderr_path", ""))
            assert "/tmp/sentinel/session_runtime" not in json.dumps(payload["job"])

        listed = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={"input": {"command": "jobs_list", "session_id": session_id}},
            headers=headers,
        )
        assert listed.status_code == 200
        jobs = listed.json()["result"]["jobs"]
        listed_job = next((item for item in jobs if item["id"] == job_id), None)
        assert listed_job is not None
        if payload["privilege"] == "user":
            assert str(listed_job["cwd"]).endswith("/workspace")
            assert "/workspace/" in str(listed_job.get("stdout_path", ""))
            assert "/workspace/" in str(listed_job.get("stderr_path", ""))
            assert "/tmp/sentinel/session_runtime" not in json.dumps(listed_job)

        status = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={"input": {"command": "job_status", "session_id": session_id, "job_id": job_id}},
            headers=headers,
        )
        assert status.status_code == 200
        status_job = status.json()["result"]["job"]
        assert status_job["id"] == job_id
        if payload["privilege"] == "user":
            assert str(status_job["cwd"]).endswith("/workspace")
            assert "/workspace/" in str(status_job.get("stdout_path", ""))
            assert "/workspace/" in str(status_job.get("stderr_path", ""))
            assert "/tmp/sentinel/session_runtime" not in json.dumps(status_job)

        logs = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={"input": {"command": "job_logs", "session_id": session_id, "job_id": job_id}},
            headers=headers,
        )
        assert logs.status_code == 200
        logs_result = logs.json()["result"]
        assert "stdout_tail" in logs_result
        if payload["privilege"] == "user":
            logs_job = logs_result["job"]
            assert str(logs_job["cwd"]).endswith("/workspace")
            assert "/workspace/" in str(logs_job.get("stdout_path", ""))
            assert "/workspace/" in str(logs_job.get("stderr_path", ""))
            assert "/tmp/sentinel/session_runtime" not in json.dumps(logs_job)

        stopped = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={"input": {"command": "job_stop", "session_id": session_id, "job_id": job_id}},
            headers=headers,
        )
        assert stopped.status_code == 200
        assert stopped.json()["result"]["job"]["status"] in {"cancelled", "completed", "failed"}
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_exec_rejects_background_without_detached():
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

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/sessions",
            json={"title": "runtime-exec-bg-reject"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={"input": {"command": "run_user", "shell_command": "sleep 1 &", "session_id": session_id}},
            headers=headers,
        )
        assert run.status_code == 422
        assert "detached=true" in run.json()["error"]["message"]
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_exec_timeout_returns_result():
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

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/sessions",
            json={"title": "runtime-exec-timeout"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={
                "input": _runtime_exec_input(
                    shell_command="sleep 3",
                    session_id=session_id,
                    timeout_seconds=1,
                )
            },
            headers=headers,
        )
        assert run.status_code == 200
        payload = run.json()["result"]
        assert payload["timed_out"] is True
        assert payload["ok"] is False
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_exec_root_privilege_requires_approval():
    fake_db = FakeDB()
    waiter_seen: dict[str, object] = {}

    async def _fake_waiter(_tool_name, _payload, requirement, _pending_callback=None):
        waiter_seen["action"] = requirement.action
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.APPROVED,
            approval={
                "provider": "runtime_exec",
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

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created_session = client.post(
            "/api/v1/sessions",
            json={"title": "runtime-exec-root-approval"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={
                "input": {
                    "command": "run_root",
                    "shell_command": "echo root-approved",
                    "session_id": session_id,
                }
            },
            headers=headers,
        )
        assert run.status_code == 200
        payload = run.json()["result"]
        assert payload["ok"] is True
        assert "root-approved" in payload["stdout"]
        assert payload["approval"]["provider"] == "runtime_exec"
        assert payload["approval"]["approval_id"] == "apr_runtime_root"
        assert waiter_seen["action"] == "runtime_exec.run_root"
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

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.post(
            "/api/v1/tools/file_read/execute",
            json={"input": {"path": "/etc/passwd"}},
            headers=headers,
        )
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

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.post(
            "/api/v1/tools/http_request/execute",
            json={"input": {"url": "http://127.0.0.1:8000/health", "method": "GET"}},
            headers=headers,
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


class _FakeAraiOSAsyncClient:
    def __init__(self, *_args, **_kwargs):
        self._request_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict | None = None):
        if url.endswith("/platform/auth/token"):
            assert json is not None
            assert json.get("api_key", "").startswith("sk-arais-agent-")
            return _FakeHttpxResponse(
                200,
                {
                    "access_token": "test-access-token",
                    "refresh_token": "test-refresh-token",
                    "expires_in": 3600,
                },
            )
        if url.endswith("/platform/auth/refresh"):
            return _FakeHttpxResponse(
                200,
                {
                    "access_token": "test-access-token-refresh",
                    "refresh_token": "test-refresh-token-next",
                    "expires_in": 3600,
                },
            )
        return _FakeHttpxResponse(404, {"detail": "not found"})

    async def request(self, method: str, url: str, **kwargs):
        self._request_count += 1
        headers = kwargs.get("headers", {})
        assert headers.get("Authorization", "").startswith("Bearer test-access-token")
        return _FakeHttpxResponse(
            200, {"ok": True, "method": method, "url": url, "attempt": self._request_count}
        )


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

    try:
        fake_db.add(AraiosModule(name="notes", label="Notes", description="d", icon="file-text"))
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = client.post(
            "/api/v1/tools/module_manager/execute",
            json={"input": {"command": "list_modules"}},
            headers=headers,
        )
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

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = client.post(
            "/api/v1/tools/module_manager/execute",
            json={"input": {}},
            headers=headers,
        )
        assert response.status_code == 422
        assert "command" in response.json()["error"]["message"]
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor, previous_get_runtime)
        app.dependency_overrides.clear()
        app_main.init_db = old_init
