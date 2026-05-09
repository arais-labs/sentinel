import asyncio
import os
from uuid import uuid4

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.services.runtime.base import RuntimeInstance
from app.services.runtime import session_runtime
from app.services.browser.pool import BrowserPool
from tests.fake_db import FakeDB

_TEST_SESSION_ID = "00000000-0000-0000-0000-000000000001"


def test_runtime_live_view_requires_auth():
    client = TestClient(app)
    response = client.get("/api/v1/runtime/live-view", params={"session_id": _TEST_SESSION_ID})
    assert response.status_code == 401


def test_runtime_live_view_payload(monkeypatch):
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    monkeypatch.setattr(
        "app.routers.runtime.is_runtime_available_for_session",
        lambda session_id: True,
    )

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.get(
            "/api/v1/runtime/live-view",
            headers=headers,
            params={"session_id": _TEST_SESSION_ID},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["enabled"] is True
        assert payload["available"] is True
        assert "/vnc/" in payload["url"]
        assert _TEST_SESSION_ID in payload["url"]
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_live_view_uses_origin_header(monkeypatch):
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    monkeypatch.setattr(
        "app.routers.runtime.is_runtime_available_for_session",
        lambda session_id: True,
    )

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}",
            "Origin": "http://localhost:4747",
        }

        response = client.get(
            "/api/v1/runtime/live-view",
            headers=headers,
            params={"session_id": _TEST_SESSION_ID},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["url"].startswith(f"http://localhost:4747/vnc/{_TEST_SESSION_ID}/vnc.html")
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_live_view_uses_referer_when_origin_missing(monkeypatch):
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    monkeypatch.setattr(
        "app.routers.runtime.is_runtime_available_for_session",
        lambda session_id: True,
    )

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}",
            "Referer": "http://localhost:4747/sentinel/sessions",
        }

        response = client.get(
            "/api/v1/runtime/live-view",
            headers=headers,
            params={"session_id": _TEST_SESSION_ID},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["url"].startswith(f"http://localhost:4747/vnc/{_TEST_SESSION_ID}/vnc.html")
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_live_view_checks_provider_host(monkeypatch):
    class _Provider:
        def get_host(self, session_id):
            assert session_id == _TEST_SESSION_ID
            return "10.20.30.40"

        def resolve_port(self, session_id, internal_port):
            assert session_id == _TEST_SESSION_ID
            assert internal_port == 6080
            return 16081

    captured: dict[str, object] = {}

    def _fake_connect(addr, timeout):
        captured["addr"] = addr
        captured["timeout"] = timeout

        class _Sock:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Sock()

    monkeypatch.setattr("app.services.runtime.get_runtime", lambda: _Provider())
    monkeypatch.setattr("socket.create_connection", _fake_connect)

    from app.services.runtime.runtime_live_view import is_runtime_available_for_session

    assert is_runtime_available_for_session(_TEST_SESSION_ID) is True
    assert captured["addr"] == ("10.20.30.40", 16081)


def test_runtime_instance_uses_client_field():
    class _Client:
        async def wait_ready(self, *, timeout=60):
            return None

        async def run(self, command: str, *, timeout: int = 300, cwd=None, env=None, as_root: bool = False):
            return None

        async def run_detached(self, command: str, *, stdout_path: str, stderr_path: str, cwd=None, env=None, as_root: bool = False):
            return 123

        async def close(self):
            return None

    instance = RuntimeInstance(
        session_id="session-1",
        client=_Client(),
        workspace_path="/workspace",
        host="127.0.0.1",
    )

    assert instance.workspace_path == "/workspace"
    assert instance.host == "127.0.0.1"
    assert hasattr(instance.client, "run")


def test_runtime_reset_browser_endpoint(monkeypatch):
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    class _StubPool:
        async def reset(self, session_id):
            assert session_id == _TEST_SESSION_ID
            return {
                "reset": True,
                "url": "about:blank",
                "profile_dir": "/data/browser-profile",
                "stale_lock_cleared": True,
            }

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr(
        "app.routers.runtime._resolve_browser_pool", lambda request: _StubPool()
    )

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.post(
            "/api/v1/runtime/reset",
            headers=headers,
            params={"session_id": _TEST_SESSION_ID},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["reset"] is True
        assert payload["url"] == "about:blank"
        assert payload["stale_lock_cleared"] is True
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_activate_session_endpoint(monkeypatch):
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    class _StubPool:
        def __init__(self) -> None:
            self.removed: list[str] = []

        async def remove(self, session_id):
            self.removed.append(session_id)

    class _StubProvider:
        def __init__(self) -> None:
            self.activations: list[str] = []

        async def activate_session(self, session_id):
            self.activations.append(session_id)
            return RuntimeInstance(
                session_id=str(session_id),
                client=object(),  # type: ignore[arg-type]
                workspace_path="/workspace",
                host="127.0.0.1",
            )

    provider = _StubProvider()
    pool = _StubPool()

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr("app.routers.runtime._resolve_browser_pool", lambda request: pool)
    monkeypatch.setattr("app.services.runtime.get_runtime", lambda: provider)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.post(
            "/api/v1/runtime/activate-session",
            headers=headers,
            params={"session_id": _TEST_SESSION_ID},
        )
        assert response.status_code == 200
        assert response.json()["activated"] is False
        assert response.json()["queued"] is True
        assert pool.removed == [_TEST_SESSION_ID]
        assert provider.activations == [_TEST_SESSION_ID]
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_live_view_does_not_activate_session(monkeypatch):
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    class _StubProvider:
        def __init__(self) -> None:
            self.activations: list[str] = []

        def get_host(self, session_id):
            return "127.0.0.1"

        def resolve_port(self, session_id, internal_port):
            return 16081 if internal_port == 6080 else internal_port

    provider = _StubProvider()

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr("app.services.runtime.get_runtime", lambda: provider)
    monkeypatch.setattr("app.routers.runtime.is_runtime_available_for_session", lambda session_id: True)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.get(
            "/api/v1/runtime/live-view",
            headers=headers,
            params={"session_id": _TEST_SESSION_ID},
        )
        assert response.status_code == 200
        assert provider.activations == []
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_browser_pool_uses_provider_resolved_cdp_port(monkeypatch):
    class _Provider:
        async def ensure(self, session_id):
            return RuntimeInstance(
                session_id=str(session_id),
                client=object(),  # type: ignore[arg-type]
                workspace_path="/workspace",
                host="10.20.30.40",
            )

        def get_host(self, session_id):
            return "10.20.30.40"

        def resolve_port(self, session_id, internal_port):
            assert internal_port == 9223
            return 19224

    monkeypatch.setattr("app.services.runtime.get_runtime", lambda: _Provider())

    pool = BrowserPool()
    endpoint, _, _ = asyncio.run(pool._build_runtime_context(_TEST_SESSION_ID))

    assert endpoint == "http://10.20.30.40:19224"


def test_browser_pool_resolves_hostname_for_cdp(monkeypatch):
    class _Provider:
        async def ensure(self, session_id):
            return RuntimeInstance(
                session_id=str(session_id),
                client=object(),  # type: ignore[arg-type]
                workspace_path="/workspace",
                host="host.docker.internal",
            )

        def get_host(self, session_id):
            return "host.docker.internal"

        def resolve_port(self, session_id, internal_port):
            assert internal_port == 9223
            return 19224

    monkeypatch.setattr("app.services.runtime.get_runtime", lambda: _Provider())
    monkeypatch.setattr("app.services.browser.pool.socket.gethostbyname", lambda host: "192.168.65.254")

    pool = BrowserPool()
    endpoint, _, _ = asyncio.run(pool._build_runtime_context(_TEST_SESSION_ID))

    assert endpoint == "http://192.168.65.254:19224"


def test_runtime_reset_falls_back_when_browser_pool_reset_fails(monkeypatch):
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    class _FailingPool:
        async def reset(self, session_id):
            raise RuntimeError(f"broken pool for {session_id}")

    class _StubSSH:
        def __init__(self) -> None:
            self.run_calls: list[str] = []
            self.detached_calls: list[tuple[str, str, str]] = []

        async def run(self, command: str, **kwargs):
            _ = kwargs
            self.run_calls.append(command)
            return None

        async def run_detached(self, command: str, stdout_path: str, stderr_path: str, **kwargs):
            _ = kwargs
            self.detached_calls.append((command, stdout_path, stderr_path))
            return {"pid": 123}

    class _StubRuntime:
        def __init__(self) -> None:
            self.client = _StubSSH()

    class _StubProvider:
        def __init__(self) -> None:
            self._instances = {_TEST_SESSION_ID: _StubRuntime()}
            self.restart_calls: list[str] = []

        def get(self, session_id):
            return self._instances.get(str(session_id))

        async def restart_browser(self, session_id, runtime):
            self.restart_calls.append(str(session_id))

    provider = _StubProvider()

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr(
        "app.routers.runtime._resolve_browser_pool", lambda request: _FailingPool()
    )
    monkeypatch.setattr("app.services.runtime.get_runtime", lambda: provider)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.post(
            "/api/v1/runtime/reset",
            headers=headers,
            params={"session_id": _TEST_SESSION_ID},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["reset"] is True
        assert payload["url"] == "about:blank"
        assert provider.restart_calls == [_TEST_SESSION_ID]
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_runtime_provider_resolve_port_contract():
    class _Provider:
        def get_public_host(self, session_id):
            assert session_id == _TEST_SESSION_ID
            return "127.0.0.1"

        def resolve_port(self, session_id, internal_port):
            assert session_id == _TEST_SESSION_ID
            assert internal_port == 12000
            return 49123

    provider = _Provider()
    assert provider.get_public_host(_TEST_SESSION_ID) == "127.0.0.1"
    assert provider.resolve_port(_TEST_SESSION_ID, 12000) == 49123


def test_detached_runtime_job_reads_recorded_exitcode(tmp_path, monkeypatch):
    session_id = uuid4()
    runtime_root = tmp_path / "runtime"
    workspace = runtime_root / str(session_id) / "workspace"
    logs = workspace / ".runtime" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(session_runtime, "_RUNTIME_BASE_DIR", runtime_root)

    stdout_host = logs / "job.stdout.log"
    stderr_host = logs / "job.stderr.log"
    exitcode_host = logs / "job.exitcode"
    stdout_host.write_text("done\n", encoding="utf-8")
    stderr_host.write_text("", encoding="utf-8")
    exitcode_host.write_text("7", encoding="utf-8")

    job = asyncio.run(
        session_runtime.register_detached_runtime_job(
            session_id,
            command="pytest tests",
            cwd="/home/sentinel/workspace/project",
            pid=999999,
            stdout_path="/home/sentinel/workspace/.runtime/logs/job.stdout.log",
            stderr_path="/home/sentinel/workspace/.runtime/logs/job.stderr.log",
            host_stdout_path=stdout_host,
            host_stderr_path=stderr_host,
            exitcode_path="/home/sentinel/workspace/.runtime/logs/job.exitcode",
            host_exitcode_path=exitcode_host,
        )
    )

    listed = asyncio.run(session_runtime.list_detached_runtime_jobs(session_id, include_completed=True))
    [listed_job] = [item for item in listed if item["id"] == job["id"]]

    assert listed_job["status"] == "failed"
    assert listed_job["returncode"] == 7
    assert listed_job["cwd"] == "/home/sentinel/workspace/project"
    assert listed_job["stdout_path"] == "/home/sentinel/workspace/.runtime/logs/job.stdout.log"
