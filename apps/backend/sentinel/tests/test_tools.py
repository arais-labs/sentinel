import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from functools import lru_cache
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("TOOL_FILE_READ_BASE_DIR", "/tmp")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import GitAccount
from app.models.system import SystemSetting
from app.services.tools import ToolExecutor, build_default_registry
from app.services.tools.builtin import _validate_public_hostname
from app.services.tools.registry import ToolApprovalOutcome, ToolApprovalOutcomeStatus
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


def _install_app_tool_runtime(fake_db: FakeDB):
    previous_registry = getattr(app.state, "tool_registry", None)
    previous_executor = getattr(app.state, "tool_executor", None)
    registry = build_default_registry(session_factory=_FakeSessionFactory(fake_db))
    app.state.tool_registry = registry
    app.state.tool_executor = ToolExecutor(registry)
    return previous_registry, previous_executor


def _restore_app_tool_runtime(previous_registry, previous_executor) -> None:
    app.state.tool_registry = previous_registry
    app.state.tool_executor = previous_executor


def _runtime_exec_needs_root_test_mode() -> bool:
    return os.name != "nt" and not _runtime_exec_user_sandbox_available()


@lru_cache(maxsize=1)
def _runtime_exec_user_sandbox_available() -> bool:
    if os.name == "nt":
        return False
    bwrap_bin = shutil.which("bwrap")
    if not bwrap_bin:
        return False

    # CI environments can have bwrap installed but disallow the required namespaces.
    probe = [
        bwrap_bin,
        "--die-with-parent",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-ipc",
        "--ro-bind",
        "/",
        "/",
        "--proc",
        "/proc",
        "--dev-bind",
        "/dev",
        "/dev",
        "--",
        "/bin/bash",
        "-lc",
        "true",
    ]
    try:
        result = subprocess.run(
            probe,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _enable_runtime_root_auto_approval_for_tests() -> None:
    tool = app.state.tool_registry.get("runtime_exec")
    assert tool is not None
    assert tool.approval_gate is not None

    async def _auto_approve(_tool_name, _payload, _requirement):
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.APPROVED,
            approval={
                "provider": "tool",
                "approval_id": "apr_runtime_auto",
                "status": "approved",
                "pending": False,
                "can_resolve": False,
            },
            message="approved",
        )

    tool.approval_gate.waiter = _auto_approve


def _runtime_exec_input(*, command: str, session_id: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {"command": command, "session_id": session_id}
    payload.update(extra)
    if _runtime_exec_needs_root_test_mode():
        _enable_runtime_root_auto_approval_for_tests()
        payload["privilege"] = "root"
    return payload


def test_tools_registry_and_execution():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

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
        assert {"file_read", "http_request", "runtime_exec", "git_exec", "git_accounts_available"} <= names
        assert {"runtime_jobs_list", "runtime_job_status", "runtime_job_logs", "runtime_job_stop"} <= names
        assert "browser_reset" in names
        assert "araios_api" in names

        detail = client.get("/api/v1/tools/file_read", headers=headers)
        assert detail.status_code == 200
        schema = detail.json()["parameters_schema"]
        assert "path" in schema["properties"]

        with tempfile.NamedTemporaryFile("w", delete=False, dir="/tmp") as handle:
            handle.write("tool execution ok")
            file_path = handle.name

        run = client.post(
            "/api/v1/tools/file_read/execute",
            json={"input": {"path": file_path}},
            headers=headers,
        )
        assert run.status_code == 200
        assert "tool execution ok" in run.json()["result"]["content"]

        invalid = client.post(
            "/api/v1/tools/file_read/execute",
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
            "/api/v1/tools/git_accounts_available/execute",
            json={"input": {"repo_url": "https://github.com/arais-labs/sentinel.git"}},
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
            json={"input": {"command": "echo blocked", "session_id": session_id}},
            headers=headers,
        )
        assert blocked.status_code == 403
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_tool_auth_required():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

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
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_exec_runs_command():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

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
            json={"input": _runtime_exec_input(command="echo hello", session_id=session_id)},
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
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_exec_detached_job_lifecycle():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

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
                    command="sleep 30",
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
            assert payload["workspace"] == "/mnt"
            assert payload["cwd"] == "/mnt"
            assert payload["job"]["cwd"] == "/mnt"
            assert str(payload["job"].get("stdout_path", "")).startswith("/mnt/")
            assert str(payload["job"].get("stderr_path", "")).startswith("/mnt/")
            assert "/tmp/sentinel/session_runtime" not in json.dumps(payload["job"])

        listed = client.post(
            "/api/v1/tools/runtime_jobs_list/execute",
            json={"input": {"session_id": session_id}},
            headers=headers,
        )
        assert listed.status_code == 200
        jobs = listed.json()["result"]["jobs"]
        listed_job = next((item for item in jobs if item["id"] == job_id), None)
        assert listed_job is not None
        if payload["privilege"] == "user":
            assert listed_job["cwd"] == "/mnt"
            assert str(listed_job.get("stdout_path", "")).startswith("/mnt/")
            assert str(listed_job.get("stderr_path", "")).startswith("/mnt/")
            assert "/tmp/sentinel/session_runtime" not in json.dumps(listed_job)

        status = client.post(
            "/api/v1/tools/runtime_job_status/execute",
            json={"input": {"session_id": session_id, "job_id": job_id}},
            headers=headers,
        )
        assert status.status_code == 200
        status_job = status.json()["result"]["job"]
        assert status_job["id"] == job_id
        if payload["privilege"] == "user":
            assert status_job["cwd"] == "/mnt"
            assert str(status_job.get("stdout_path", "")).startswith("/mnt/")
            assert str(status_job.get("stderr_path", "")).startswith("/mnt/")
            assert "/tmp/sentinel/session_runtime" not in json.dumps(status_job)

        logs = client.post(
            "/api/v1/tools/runtime_job_logs/execute",
            json={"input": {"session_id": session_id, "job_id": job_id}},
            headers=headers,
        )
        assert logs.status_code == 200
        logs_result = logs.json()["result"]
        assert "stdout_tail" in logs_result
        if payload["privilege"] == "user":
            logs_job = logs_result["job"]
            assert logs_job["cwd"] == "/mnt"
            assert str(logs_job.get("stdout_path", "")).startswith("/mnt/")
            assert str(logs_job.get("stderr_path", "")).startswith("/mnt/")
            assert "/tmp/sentinel/session_runtime" not in json.dumps(logs_job)

        stopped = client.post(
            "/api/v1/tools/runtime_job_stop/execute",
            json={"input": {"session_id": session_id, "job_id": job_id}},
            headers=headers,
        )
        assert stopped.status_code == 200
        assert stopped.json()["result"]["job"]["status"] in {"cancelled", "completed", "failed"}
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_exec_rejects_background_without_detached():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

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
            json={"input": {"command": "sleep 1 &", "session_id": session_id}},
            headers=headers,
        )
        assert run.status_code == 422
        assert "detached=true" in run.json()["error"]["message"]
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_exec_timeout_returns_result():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

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
                    command="sleep 3",
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
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_exec_root_privilege_requires_approval():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    waiter_seen: dict[str, object] = {}

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

        tool = app.state.tool_registry.get("runtime_exec")
        assert tool is not None
        assert tool.approval_gate is not None

        async def _fake_waiter(_tool_name, _payload, requirement):
            waiter_seen["action"] = requirement.action
            return ToolApprovalOutcome(
                status=ToolApprovalOutcomeStatus.APPROVED,
                approval={
                    "provider": "tool",
                    "approval_id": "apr_runtime_root",
                    "status": "approved",
                    "pending": False,
                    "can_resolve": False,
                },
                message="approved",
            )

        tool.approval_gate.waiter = _fake_waiter

        run = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={
                "input": {
                    "command": "echo root-approved",
                    "session_id": session_id,
                    "privilege": "root",
                }
            },
            headers=headers,
        )
        assert run.status_code == 200
        payload = run.json()["result"]
        assert payload["ok"] is True
        assert "root-approved" in payload["stdout"]
        assert payload["approval"]["provider"] == "tool"
        assert payload["approval"]["approval_id"] == "apr_runtime_root"
        assert waiter_seen["action"] == "runtime_exec.root"
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_exec_user_mode_confines_writes_to_workspace():
    if not _runtime_exec_user_sandbox_available():
        return

    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

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
            json={"title": "runtime-exec-confined-user"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        allowed = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={
                "input": {
                    "command": "echo workspace-ok > allowed.txt && cat allowed.txt",
                    "session_id": session_id,
                    "privilege": "user",
                }
            },
            headers=headers,
        )
        assert allowed.status_code == 200
        allowed_payload = allowed.json()["result"]
        assert allowed_payload["ok"] is True
        assert "workspace-ok" in allowed_payload["stdout"]
        assert allowed_payload["workspace"] == "/mnt"
        assert allowed_payload["cwd"] == "/mnt"

        tmp_mapped = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={
                "input": {
                    "command": (
                        "echo tmp-ok > /tmp/runtime_exec_probe.txt "
                        "&& cat /tmp/runtime_exec_probe.txt "
                        "&& test -f .runtime/sandbox/tmp/runtime_exec_probe.txt "
                        "&& echo mapped-ok"
                    ),
                    "session_id": session_id,
                    "privilege": "user",
                }
            },
            headers=headers,
        )
        assert tmp_mapped.status_code == 200
        tmp_payload = tmp_mapped.json()["result"]
        assert tmp_payload["ok"] is True
        assert "tmp-ok" in tmp_payload["stdout"]
        assert "mapped-ok" in tmp_payload["stdout"]
        assert tmp_payload["workspace"] == "/mnt"
        assert tmp_payload["cwd"] == "/mnt"

        blocked = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={
                "input": {
                    "command": "echo forbidden > /etc/runtime_exec_forbidden_test",
                    "session_id": session_id,
                    "privilege": "user",
                }
            },
            headers=headers,
        )
        assert blocked.status_code == 200
        blocked_payload = blocked.json()["result"]
        assert blocked_payload["ok"] is False
        assert blocked_payload["timed_out"] is False
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_exec_user_mode_python_venv_is_available_inside_sandbox():
    if not _runtime_exec_user_sandbox_available():
        return

    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

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
            json={"title": "runtime-exec-user-venv"},
            headers=headers,
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        run = client.post(
            "/api/v1/tools/runtime_exec/execute",
            json={
                "input": {
                    "command": "python -c \"import os,sys;print(sys.prefix);print(os.environ.get('VIRTUAL_ENV',''))\"",
                    "session_id": session_id,
                    "privilege": "user",
                    "use_python_venv": True,
                }
            },
            headers=headers,
        )
        assert run.status_code == 200
        payload = run.json()["result"]
        assert payload["ok"] is True
        assert "/tmp/.venv" in payload["stdout"]
        assert payload["venv"] == "/tmp/.venv"
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_file_read_path_traversal_blocked():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

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
        assert response.status_code == 422
        assert "outside allowed directory" in response.json()["error"]["message"]
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_http_request_ssrf_blocked():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

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
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_http_request_ssrf_allow_hosts_bypasses_private_check():
    previous = os.environ.get("SSRF_ALLOW_HOSTS")
    os.environ["SSRF_ALLOW_HOSTS"] = "araios-backend"
    try:
        asyncio.run(_validate_public_hostname("araios-backend"))
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


def test_araios_api_tool_executes_with_configured_integration():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)
    fake_db.add(
        SystemSetting(key="araios_backend_url", value="http://araios-backend:9000")
    )
    fake_db.add(
        SystemSetting(key="araios_integration_agent_api_key", value="sk-arais-agent-test-token")
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
        with patch("app.services.tools.builtin.httpx.AsyncClient", _FakeAraiOSAsyncClient):
            response = client.post(
                "/api/v1/tools/araios_api/execute",
                json={"input": {"path": "/api/agent", "method": "GET"}},
                headers=headers,
            )
        assert response.status_code == 200
        body = response.json()["result"]["body"]
        assert body["ok"] is True
        assert body["url"] == "http://araios-backend:9000/api/agent"
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_araios_api_tool_requires_integration_configuration():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

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
            "/api/v1/tools/araios_api/execute",
            json={"input": {"path": "/api/agent", "method": "GET"}},
            headers=headers,
        )
        assert response.status_code == 422
        assert "AraiOS integration is not configured" in response.json()["error"]["message"]
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init
