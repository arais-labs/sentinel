import asyncio
import mimetypes
import os
import subprocess
import tempfile
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from tests.helpers import install_fake_db_overrides, make_fake_instance_context, restore_test_app
from app.main import app
from app.models import Message, Session, SessionBinding, ToolApproval
from app.services.llm.generic.types import AssistantMessage, SystemMessage, TextContent, UserMessage
from app.services.runtime.files import RuntimeDownload, RuntimePathInvalidError, RuntimePathIsDirectoryError
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.errors import SessionWorkspaceCleanupError
from app.services.sessions.service import SessionService
from tests.fake_db import FakeDB


SESSIONS_API = "/api/v1/instances/main/sessions"


class _LocalRuntimeWorkspaceFiles:
    def __init__(self, runtime_base: Path) -> None:
        self._runtime_base = runtime_base

    def _workspace(self, session_id: uuid.UUID | str) -> Path:
        return (self._runtime_base / str(session_id) / "workspace").resolve()

    def _resolve(self, session_id: uuid.UUID | str, path: str, *, must_exist: bool = True) -> Path:
        workspace = self._workspace(session_id).resolve()
        target = (workspace / path).resolve(strict=False) if path else workspace
        if target != workspace and workspace not in target.parents:
            raise RuntimePathInvalidError("Path must stay within runtime workspace")
        if ".." in Path(path).parts:
            raise RuntimePathInvalidError("Path traversal is not allowed")
        if must_exist and not target.exists():
            raise FileNotFoundError(path)
        return target

    def _entry(self, workspace: Path, path: Path) -> dict:
        rel = path.relative_to(workspace).as_posix()
        is_git_root = path.is_dir() and (path / ".git").exists()
        return {
            "name": path.name,
            "path": rel,
            "kind": "directory" if path.is_dir() else "file",
            "size_bytes": None if path.is_dir() else path.stat().st_size,
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
            "is_git_root": is_git_root,
            "git_branch": "main" if is_git_root else None,
            "git_detached_head": False,
        }

    async def list_files(self, session_id: uuid.UUID | str, *, path: str = "", limit: int = 400) -> dict:
        workspace = self._workspace(session_id)
        target = self._resolve(session_id, path)
        if not target.is_dir():
            raise RuntimePathInvalidError("Runtime path is not a directory")
        children = sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        entries = [self._entry(workspace, child) for child in children[:limit]]
        return {
            "session_id": str(session_id),
            "runtime_exists": workspace.parent.exists(),
            "workspace_exists": workspace.exists(),
            "path": path,
            "parent_path": str(Path(path).parent).replace(".", "") if path else None,
            "entries": entries,
            "truncated": len(children) > limit,
        }

    async def preview_file(self, session_id: uuid.UUID | str, *, path: str, max_bytes: int = 32_000) -> dict:
        target = self._resolve(session_id, path)
        if not target.is_file():
            raise RuntimePathIsDirectoryError(path)
        raw = target.read_bytes()
        return {
            "session_id": str(session_id),
            "runtime_exists": target.parent.exists(),
            "workspace_exists": self._workspace(session_id).exists(),
            "path": path,
            "name": target.name,
            "size_bytes": target.stat().st_size,
            "modified_at": datetime.fromtimestamp(target.stat().st_mtime, UTC).isoformat(),
            "content": raw[:max_bytes].decode("utf-8", errors="replace"),
            "truncated": len(raw) > max_bytes,
            "max_bytes": max_bytes,
        }

    async def download(self, session_id: uuid.UUID | str, *, path: str) -> RuntimeDownload:
        target = self._resolve(session_id, path)
        if target.is_file():
            return RuntimeDownload(
                content=target.read_bytes(),
                download_name=target.name,
                media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
            )
        buffer = tempfile.SpooledTemporaryFile()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for current in sorted(target.rglob("*")):
                if current.is_file():
                    archive.write(current, current.relative_to(target).as_posix())
        buffer.seek(0)
        return RuntimeDownload(
            content=buffer.read(),
            download_name=f"{target.name}.zip",
            media_type="application/zip",
        )

    async def git_diff(
        self,
        session_id: uuid.UUID | str,
        *,
        path: str,
        base_ref: str = "HEAD",
        staged: bool = False,
        context_lines: int = 3,
        max_bytes: int = 120_000,
    ) -> dict:
        workspace = self._workspace(session_id)
        target = self._resolve(session_id, path, must_exist=False)
        probe = target if target.exists() and target.is_dir() else target.parent
        root = subprocess.run(
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        file_rel = target.relative_to(Path(root)).as_posix()
        command = ["git", "-C", root, "diff", f"--unified={context_lines}"]
        if staged:
            command.append("--staged")
        command.extend([base_ref, "--", file_rel])
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        diff = completed.stdout
        if not diff and target.exists():
            tracked = subprocess.run(
                ["git", "-C", root, "ls-files", "--error-unmatch", "--", file_rel],
                check=False,
                capture_output=True,
                text=True,
            )
            if tracked.returncode != 0:
                diff = f"--- /dev/null\n+++ b/{file_rel}\n@@ -0,0 +1 @@\n+{target.read_text(encoding='utf-8').rstrip()}\n"
        return {
            "session_id": str(session_id),
            "runtime_exists": workspace.parent.exists(),
            "workspace_exists": workspace.exists(),
            "path": path,
            "git_root": Path(root).relative_to(workspace).as_posix(),
            "branch": "main",
            "detached_head": False,
            "base_ref": base_ref,
            "staged": staged,
            "context_lines": context_lines,
            "diff": diff[:max_bytes],
            "truncated": len(diff.encode("utf-8")) > max_bytes,
            "max_bytes": max_bytes,
        }


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


def test_mark_session_read_preserves_updated_at():
    session = Session(
        id=uuid.uuid4(),
        user_id="user-1",
        agent_id="agent-1",
        title="alpha",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    captured_updates = []

    class _ScalarResult:
        def __init__(self, rows):
            self._rows = rows

        def first(self):
            return self._rows[0] if self._rows else None

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return _ScalarResult(self._rows)

    class _CaptureDb:
        async def execute(self, stmt):
            if getattr(stmt, "is_update", False):
                captured_updates.append(stmt)
                return _Result([])
            return _Result([session])

        async def commit(self):
            return None

        async def refresh(self, _obj):
            return None

    async def _run() -> None:
        service = SessionService(run_registry=AgentRunRegistry())
        await service.mark_as_read(
            _CaptureDb(),
            session_id=session.id,
            user_id=session.user_id,
        )

    asyncio.run(_run())

    assert len(captured_updates) == 1
    sql = str(captured_updates[0].compile(dialect=postgresql.dialect()))
    assert "updated_at=sessions.updated_at" in sql.replace(" ", "")


def test_delete_session_runs_cleanup_before_database_delete():
    fake_db = FakeDB()
    service = SessionService(run_registry=AgentRunRegistry())
    parent = Session(
        id=uuid.uuid4(),
        user_id="user-1",
        agent_id="agent-1",
        title="parent",
    )
    child = Session(
        id=uuid.uuid4(),
        user_id="user-1",
        agent_id="agent-1",
        title="child",
        parent_session_id=parent.id,
    )
    main = Session(
        id=uuid.uuid4(),
        user_id="user-1",
        agent_id="agent-1",
        title="main",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    parent.created_at = datetime(2026, 1, 2, tzinfo=UTC)
    fake_db.add(parent)
    fake_db.add(child)
    fake_db.add(main)

    cleanup_ids: list[uuid.UUID] = []

    async def _cleanup(session_ids: list[uuid.UUID]) -> None:
        cleanup_ids.extend(session_ids)
        assert parent in fake_db.storage[Session]
        assert child in fake_db.storage[Session]

    async def _run() -> None:
        deleted_descendants = await service.delete_session(
            fake_db,
            session_id=parent.id,
            user_id=parent.user_id,
            before_delete=_cleanup,
        )
        assert deleted_descendants == 1

    asyncio.run(_run())

    assert cleanup_ids == [parent.id, child.id]
    assert parent not in fake_db.storage[Session]
    assert child not in fake_db.storage[Session]


def test_delete_session_keeps_database_rows_when_cleanup_fails():
    fake_db = FakeDB()
    service = SessionService(run_registry=AgentRunRegistry())
    session = Session(
        id=uuid.uuid4(),
        user_id="user-1",
        agent_id="agent-1",
        title="kept",
    )
    main = Session(
        id=uuid.uuid4(),
        user_id="user-1",
        agent_id="agent-1",
        title="main",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session.created_at = datetime(2026, 1, 2, tzinfo=UTC)
    fake_db.add(session)
    fake_db.add(main)

    async def _cleanup(_session_ids: list[uuid.UUID]) -> None:
        raise RuntimeError("remote cleanup failed")

    async def _run() -> None:
        try:
            await service.delete_session(
                fake_db,
                session_id=session.id,
                user_id=session.user_id,
                before_delete=_cleanup,
            )
        except SessionWorkspaceCleanupError as exc:
            assert exc.detail == "remote cleanup failed"
        else:
            raise AssertionError("expected cleanup failure")

    asyncio.run(_run())

    assert session in fake_db.storage[Session]


def test_sessions_crud_and_ownership(monkeypatch):
    from app.routers import sessions as sessions_router

    async def _noop_runtime_cleanup(_session_ids: list[uuid.UUID]) -> None:
        return None

    monkeypatch.setattr(
        sessions_router,
        "_cleanup_runtime_for_deleted_sessions",
        _noop_runtime_cleanup,
    )
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        user1_token_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert user1_token_resp.status_code == 200
        user1_token = user1_token_resp.json()["access_token"]

        user2_token = _make_token(sub="other-user")

        s1 = client.post(SESSIONS_API, json={"title": "alpha"}, headers={"Authorization": f"Bearer {user1_token}"})
        s2 = client.post(SESSIONS_API, json={"title": "beta"}, headers={"Authorization": f"Bearer {user1_token}"})
        s_child = client.post(
            SESSIONS_API,
            json={"title": "sub-agent:child"},
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        s3 = client.post(SESSIONS_API, json={"title": "gamma"}, headers={"Authorization": f"Bearer {user2_token}"})
        assert s1.status_code == 200 and s2.status_code == 200 and s3.status_code == 200 and s_child.status_code == 200

        session1_id = s1.json()["id"]
        session2_id = s2.json()["id"]
        child_session_id = s_child.json()["id"]
        session3_id = s3.json()["id"]

        # Mark one session as a child run (sub-agent session) and ensure it is hidden from top-level listing.
        for item in fake_db.storage[Session]:
            if str(item.id) == child_session_id:
                item.parent_session_id = uuid.UUID(session1_id)
            if str(item.id) == session1_id:
                item.initial_prompt = "first prompt"
                item.latest_system_prompt = "large system prompt" * 1000

        list_user1 = client.get(SESSIONS_API, headers={"Authorization": f"Bearer {user1_token}"})
        assert list_user1.status_code == 200
        list_items_user1 = list_user1.json()["items"]
        ids_user1 = {item["id"] for item in list_items_user1}
        assert session1_id in ids_user1
        assert session2_id in ids_user1
        assert child_session_id not in ids_user1
        assert session3_id not in ids_user1
        listed_session1 = next(item for item in list_items_user1 if item["id"] == session1_id)
        assert "initial_prompt" not in listed_session1
        assert "latest_system_prompt" not in listed_session1

        forbidden_get = client.get(f"{SESSIONS_API}/{session3_id}", headers={"Authorization": f"Bearer {user1_token}"})
        assert forbidden_get.status_code == 404

        set_main_resp = client.post(
            f"{SESSIONS_API}/{session2_id}/main",
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert set_main_resp.status_code == 200
        assert set_main_resp.json()["is_main"] is True

        delete_resp = client.delete(
            f"{SESSIONS_API}/{session1_id}",
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["status"] == "deleted"
        deleted_session = client.get(
            f"{SESSIONS_API}/{session1_id}",
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert deleted_session.status_code == 404

        m1 = client.post(
            f"{SESSIONS_API}/{session2_id}/messages",
            json={"role": "user", "content": "first", "metadata": {}},
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        m2 = client.post(
            f"{SESSIONS_API}/{session2_id}/messages",
            json={"role": "system", "content": "second", "metadata": {}},
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        m3 = client.post(
            f"{SESSIONS_API}/{session2_id}/messages",
            json={"role": "user", "content": "third", "metadata": {}},
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert m1.status_code == 200 and m2.status_code == 200 and m3.status_code == 200

        history = client.get(
            f"{SESSIONS_API}/{session2_id}/messages?limit=2",
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert history.status_code == 200
        payload = history.json()
        assert len(payload["items"]) == 2
        assert payload["has_more"] is True

        stop_resp = client.post(
            f"{SESSIONS_API}/{session2_id}/stop",
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] in {"stopping", "idle"}
    finally:
        restore_test_app(old_init)


def test_session_rename_endpoint():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        created = client.post(SESSIONS_API, json={"title": "alpha"}, headers=headers)
        assert created.status_code == 200
        session_id = created.json()["id"]

        renamed = client.patch(
            f"{SESSIONS_API}/{session_id}",
            json={"title": "   Better Name   "},
            headers=headers,
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "Better Name"

        cleared = client.patch(
            f"{SESSIONS_API}/{session_id}",
            json={"title": "   "},
            headers=headers,
        )
        assert cleared.status_code == 200
        assert cleared.json()["title"] is None
    finally:
        restore_test_app(old_init)


def test_retry_message_endpoint_reruns_existing_user_message():
    fake_db = FakeDB()

    old_ws_manager = getattr(app.state, "ws_manager", None)
    old_runtime_support = getattr(app.state, "agent_runtime_support", None)
    old_db_factory = getattr(app.state, "db_factory", None)

    captured: dict[str, object] = {}
    scheduled: list[object] = []

    class _DummyManager:
        async def broadcast_agent_thinking(self, session_key: str) -> None:
            captured["thinking_session_key"] = session_key

    @asynccontextmanager
    async def _db_factory():
        yield fake_db

    runtime_support = object()
    instance_context = make_fake_instance_context(
        app_db=fake_db,
        agent_runtime_support=runtime_support,
        session_factory=_db_factory,
    )
    old_init = install_fake_db_overrides(
        app_db=fake_db,
        instance_context=instance_context,
        session_factory=_db_factory,
    )

    async def _fake_run_agent_once(**kwargs):
        captured.update(kwargs)

        class _Outcome:
            failed = False
            cancelled = False
            run_error = None

        return _Outcome()

    def _fake_create_task(coro):
        scheduled.append(coro)
        return coro

    try:
        app.state.ws_manager = _DummyManager()
        app.state.agent_runtime_support = runtime_support
        app.state.db_factory = _db_factory

        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        created = client.post(SESSIONS_API, json={"title": "alpha"}, headers=headers)
        assert created.status_code == 200
        session_id = created.json()["id"]

        message = client.post(
            f"{SESSIONS_API}/{session_id}/messages",
            json={
                "role": "user",
                "content": "retry me",
                "metadata": {
                    "agent_mode": "read_only",
                    "generation": {
                        "requested_tier": "hard",
                        "max_iterations": 17,
                    },
                },
            },
            headers=headers,
        )
        assert message.status_code == 200
        message_id = message.json()["id"]

        with patch("app.routers.sessions.run_agent_once", new=_fake_run_agent_once), patch(
            "app.routers.sessions.asyncio.create_task", side_effect=_fake_create_task
        ):
            response = client.post(
                f"{SESSIONS_API}/{session_id}/messages/{message_id}/retry",
                headers=headers,
            )

            assert response.status_code == 200
            assert response.json()["status"] == "retrying"
            assert len(scheduled) == 1

            asyncio.run(scheduled[0])

            assert captured["session_key"] == session_id
            assert captured["persist_user_message"] is False
            assert str(captured["tier"]) == "hard"
            assert captured["max_iterations"] == 17
            assert str(captured["agent_mode"]) == "read_only"
            assert captured["payload"] == "retry me"
            assert captured["thinking_session_key"] == session_id
    finally:
        restore_test_app(old_init)
        app.state.ws_manager = old_ws_manager
        app.state.agent_runtime_support = old_runtime_support
        app.state.db_factory = old_db_factory


def test_cannot_set_telegram_channel_session_as_main():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        main_resp = client.get(f"{SESSIONS_API}/default", headers=headers)
        assert main_resp.status_code == 200
        main_session_id = main_resp.json()["id"]

        channel_resp = client.post(SESSIONS_API, json={"title": "TG Group · Ops"}, headers=headers)
        assert channel_resp.status_code == 200
        channel_session_id = channel_resp.json()["id"]

        import jwt as _jwt
        _decoded = _jwt.decode(token, options={"verify_signature": False})
        _actual_user_id = _decoded["sub"]
        fake_db.add(
            SessionBinding(
                user_id=_actual_user_id,
                binding_type="telegram_group",
                binding_key="group:-100123",
                session_id=uuid.UUID(channel_session_id),
                is_active=True,
                metadata_json={"chat_id": -100123},
            )
        )

        forbidden = client.post(f"{SESSIONS_API}/{channel_session_id}/main", headers=headers)
        assert forbidden.status_code == 400
        payload = forbidden.json()
        detail = (
            payload.get("detail")
            or (payload.get("error") or {}).get("message")
            or str(payload)
        )
        assert "Telegram channel sessions cannot be set as main" in detail

        still_main = client.get(f"{SESSIONS_API}/{main_session_id}", headers=headers)
        assert still_main.status_code == 200
        assert still_main.json()["is_main"] is True
    finally:
        restore_test_app(old_init)


def test_cannot_rename_telegram_channel_session():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        channel_resp = client.post(SESSIONS_API, json={"title": "TG Group · Ops"}, headers=headers)
        assert channel_resp.status_code == 200
        channel_session_id = channel_resp.json()["id"]

        import jwt as _jwt
        _decoded = _jwt.decode(token, options={"verify_signature": False})
        _actual_user_id = _decoded["sub"]
        fake_db.add(
            SessionBinding(
                user_id=_actual_user_id,
                binding_type="telegram_group",
                binding_key="group:-100123",
                session_id=uuid.UUID(channel_session_id),
                is_active=True,
                metadata_json={"chat_id": -100123},
            )
        )

        rename = client.patch(
            f"{SESSIONS_API}/{channel_session_id}",
            json={"title": "Renamed"},
            headers=headers,
        )
        assert rename.status_code == 400
        payload = rename.json()
        detail = (
            payload.get("detail")
            or (payload.get("error") or {}).get("message")
            or str(payload)
        )
        assert "cannot be renamed" in detail.lower()
    finally:
        restore_test_app(old_init)


def test_reset_default_session_keeps_previous_main_runtime_workspace():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_base = Path(tmpdir)
            client = TestClient(app)
            login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
            assert login.status_code == 200
            token = login.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            main_resp = client.get(f"{SESSIONS_API}/default", headers=headers)
            assert main_resp.status_code == 200
            old_main_id = main_resp.json()["id"]

            old_workspace = runtime_base / old_main_id / "workspace"
            old_workspace.mkdir(parents=True, exist_ok=True)
            marker = old_workspace / "keep.txt"
            marker.write_text("preserve")

            reset_resp = client.post(f"{SESSIONS_API}/default/reset", headers=headers)
            assert reset_resp.status_code == 200
            new_main_id = reset_resp.json()["id"]
            assert new_main_id != old_main_id

            assert old_workspace.exists() is True
            assert marker.exists() is True
            assert marker.read_text() == "preserve"
    finally:
        restore_test_app(old_init)


def test_stop_session_generation_cancels_pending_git_approvals():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_resp = client.post(SESSIONS_API, json={"title": "stop-cancels-approvals"}, headers=headers)
        assert session_resp.status_code == 200
        session_id = uuid.UUID(session_resp.json()["id"])

        pending = ToolApproval(
            provider="git",
            tool_name="git",
            session_id=session_id,
            action="git.write",
            description="Execute an approval-gated git or supported gh write command inside the session workspace.",
            status="pending",
            requested_by="session:test",
            payload_json={"tool_name": "git"},
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        fake_db.add(pending)

        stop_resp = client.post(f"{SESSIONS_API}/{session_id}/stop", headers=headers)
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] in {"stopping", "idle"}
        assert pending.status == "cancelled"
        assert pending.resolved_at is not None
    finally:
        restore_test_app(old_init)


def test_stop_session_generation_materializes_unresolved_tool_calls():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_resp = client.post(SESSIONS_API, json={"title": "stop-materializes-tool-result"}, headers=headers)
        assert session_resp.status_code == 200
        session_id = uuid.UUID(session_resp.json()["id"])

        fake_db.add(
            Message(
                session_id=session_id,
                role="assistant",
                content="",
                metadata_json={
                    "generation": {
                        "requested_tier": "normal",
                        "resolved_model": "gpt-4.1-mini",
                        "provider": "openai",
                        "temperature": 0.7,
                        "max_iterations": 50,
                    },
                    "tool_calls": [
                        {
                            "id": "toolu_pending_runtime",
                            "name": "runtime",
                            "arguments": {"command": "user", "shell_command": "sleep 20"},
                        }
                    ]
                },
            )
        )

        stop_resp = client.post(f"{SESSIONS_API}/{session_id}/stop", headers=headers)
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] in {"stopping", "idle"}

        messages_resp = client.get(f"{SESSIONS_API}/{session_id}/messages", headers=headers)
        assert messages_resp.status_code == 200
        items = messages_resp.json()["items"]
        materialized = next(
            (
                item
                for item in items
                if item["role"] == "tool_result"
                and item.get("tool_call_id") == "toolu_pending_runtime"
                and item.get("tool_name") == "runtime"
            ),
            None,
        )
        assert materialized is not None
        assert materialized["metadata"]["cancelled_by_stop"] is True
        assert materialized["metadata"]["pending"] is False
        generation = materialized["metadata"].get("generation") or {}
        assert generation.get("resolved_model") == "gpt-4.1-mini"
        assert generation.get("provider") == "openai"
    finally:
        restore_test_app(old_init)


def test_context_usage_prefers_rebuilt_context_when_runtime_snapshot_missing():
    fake_db = FakeDB()
    class _FakeContextBuilder:
        async def build(self, db, session_id, system_prompt=None, pending_user_message=None):
            _ = (db, session_id, system_prompt, pending_user_message)
            return [
                SystemMessage(content="You are Sentinel."),
                UserMessage(content="latest user"),
                AssistantMessage(content=[TextContent(text="latest answer")]),
            ]

    class _FakeLoop:
        def __init__(self):
            self.context_builder = _FakeContextBuilder()

    fake_loop = _FakeLoop()
    old_init = install_fake_db_overrides(
        app_db=fake_db,
        instance_context=make_fake_instance_context(
            app_db=fake_db,
            agent_runtime_support=fake_loop,
        ),
    )

    try:
        client = TestClient(app)

        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        session_resp = client.post(SESSIONS_API, json={"title": "usage-rebuild"}, headers=headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        usage_resp = client.get(f"{SESSIONS_API}/{session_id}/context-usage", headers=headers)
        assert usage_resp.status_code == 200
        payload = usage_resp.json()
        assert payload["source"] == "rebuilt_context_estimate"
        assert isinstance(payload["estimated_context_tokens"], int)
        assert payload["estimated_context_tokens"] > 0
    finally:
        restore_test_app(old_init)


def test_runtime_file_explorer_endpoints():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        created = client.post(SESSIONS_API, json={"title": "runtime-explorer"}, headers=headers)
        assert created.status_code == 200
        session_id = created.json()["id"]

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_base = Path(temp_dir)
            workspace = runtime_base / session_id / "workspace"
            (workspace / "src").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (workspace / "README.md").write_text("# demo\n", encoding="utf-8")
            (workspace / "repo").mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "-C", str(workspace / "repo"), "init"], check=False)

            with patch("app.routers.sessions.get_runtime_workspace_files", return_value=_LocalRuntimeWorkspaceFiles(runtime_base)):
                files_root = client.get(f"{SESSIONS_API}/{session_id}/runtime/files", headers=headers)
                assert files_root.status_code == 200
                root_payload = files_root.json()
                names = {item["name"] for item in root_payload["entries"]}
                assert {"src", "README.md", "repo"} <= names
                repo_entry = next(item for item in root_payload["entries"] if item["name"] == "repo")
                assert repo_entry["kind"] == "directory"
                assert repo_entry["is_git_root"] is True

                files_src = client.get(
                    f"{SESSIONS_API}/{session_id}/runtime/files?path=src",
                    headers=headers,
                )
                assert files_src.status_code == 200
                src_payload = files_src.json()
                assert src_payload["path"] == "src"
                assert src_payload["parent_path"] == ""
                assert any(item["name"] == "main.py" and item["kind"] == "file" for item in src_payload["entries"])

                preview = client.get(
                    f"{SESSIONS_API}/{session_id}/runtime/file?path=src/main.py",
                    headers=headers,
                )
                assert preview.status_code == 200
                preview_payload = preview.json()
                assert preview_payload["name"] == "main.py"
                assert "print('ok')" in preview_payload["content"]

                forbidden = client.get(
                    f"{SESSIONS_API}/{session_id}/runtime/files?path=../secrets",
                    headers=headers,
                )
                assert forbidden.status_code == 400
    finally:
        restore_test_app(old_init)


def test_runtime_git_diff_supports_deleted_and_untracked_files():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        created = client.post(SESSIONS_API, json={"title": "runtime-git-diff"}, headers=headers)
        assert created.status_code == 200
        session_id = created.json()["id"]

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_base = Path(temp_dir)
            repo_dir = runtime_base / session_id / "workspace" / "repo"
            repo_dir.mkdir(parents=True, exist_ok=True)

            subprocess.run(["git", "-C", str(repo_dir), "init"], check=True)
            subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test User"], check=True)
            subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "test-user@example.com"], check=True)

            deleted_file = repo_dir / "old.txt"
            deleted_file.write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo_dir), "add", "old.txt"], check=True)
            subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", "seed"], check=True)
            deleted_file.unlink()

            added_file = repo_dir / "new.txt"
            added_file.write_text("after\n", encoding="utf-8")

            with patch("app.routers.sessions.get_runtime_workspace_files", return_value=_LocalRuntimeWorkspaceFiles(runtime_base)):
                deleted_resp = client.get(
                    f"{SESSIONS_API}/{session_id}/runtime/git/diff?path=repo/old.txt&base_ref=HEAD&staged=false&context_lines=3&max_bytes=120000",
                    headers=headers,
                )
                assert deleted_resp.status_code == 200
                deleted_payload = deleted_resp.json()
                assert deleted_payload["path"] == "repo/old.txt"
                assert "deleted file mode" in deleted_payload["diff"]

                added_resp = client.get(
                    f"{SESSIONS_API}/{session_id}/runtime/git/diff?path=repo/new.txt&base_ref=HEAD&staged=false&context_lines=3&max_bytes=120000",
                    headers=headers,
                )
                assert added_resp.status_code == 200
                added_payload = added_resp.json()
                assert added_payload["path"] == "repo/new.txt"
                assert "+++ b/new.txt" in added_payload["diff"]
                assert "+after" in added_payload["diff"]
    finally:
        restore_test_app(old_init)


def test_runtime_download_supports_files_and_directories():
    fake_db = FakeDB()

    old_init = install_fake_db_overrides(app_db=fake_db)

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        created = client.post(SESSIONS_API, json={"title": "runtime-download"}, headers=headers)
        assert created.status_code == 200
        session_id = created.json()["id"]

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_base = Path(temp_dir)
            workspace = runtime_base / session_id / "workspace"
            docs_dir = workspace / "docs"
            docs_dir.mkdir(parents=True, exist_ok=True)
            readme = workspace / "README.md"
            readme.write_text("# demo\n", encoding="utf-8")
            guide = docs_dir / "guide.txt"
            guide.write_text("hello zip\n", encoding="utf-8")

            with patch("app.routers.sessions.get_runtime_workspace_files", return_value=_LocalRuntimeWorkspaceFiles(runtime_base)):
                file_resp = client.get(
                    f"{SESSIONS_API}/{session_id}/runtime/download?path=README.md",
                    headers=headers,
                )
                assert file_resp.status_code == 200
                assert file_resp.headers["content-type"].startswith("text/markdown")
                assert "filename=\"README.md\"" in file_resp.headers["content-disposition"]
                assert file_resp.text == "# demo\n"

                folder_resp = client.get(
                    f"{SESSIONS_API}/{session_id}/runtime/download?path=docs",
                    headers=headers,
                )
                assert folder_resp.status_code == 200
                assert folder_resp.headers["content-type"] == "application/zip"
                assert "filename=\"docs.zip\"" in folder_resp.headers["content-disposition"]

                archive_path = runtime_base / "download-check.zip"
                archive_path.write_bytes(folder_resp.content)
                with zipfile.ZipFile(archive_path) as archive:
                    assert archive.namelist() == ["guide.txt"]
                    assert archive.read("guide.txt").decode("utf-8") == "hello zip\n"
    finally:
        restore_test_app(old_init)
