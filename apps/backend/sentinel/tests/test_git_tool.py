from __future__ import annotations

from uuid import uuid4

import pytest

from app.models import GitAccount
from app.schemas.runtime import RuntimeExecResult
from app.services.runtime.environment import RuntimeEnvironment
from app.services.tools.executor import ToolExecutor, ToolValidationError
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.registry_builder import build_default_registry
from tests.fake_db import FakeDB


class _SessionCtx:
    def __init__(self, db: FakeDB):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, db: FakeDB):
        self._db = db

    def __call__(self):
        return _SessionCtx(self._db)


class _SSHStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.next_result: RuntimeExecResult | None = None

    async def run(
        self,
        command: str,
        *,
        timeout: int = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RuntimeExecResult:
        self.calls.append({"command": command, "timeout": timeout, "cwd": cwd, "env": env})
        if self.next_result is not None:
            result = self.next_result
            self.next_result = None
            return result
        if "remote get-url" in command:
            return RuntimeExecResult(
                exit_status=0,
                stdout="https://github.com/arais-labs/sentinel.git\n",
                stderr="",
            )
        return RuntimeExecResult(exit_status=0, stdout="ok\n", stderr="")


class _TerminalManagerStub:
    def __init__(self, *, environment: RuntimeEnvironment | None = None) -> None:
        self.ssh = _SSHStub()
        self.prepared: list[str] = []
        self.workspaces_root = "/srv/sentinel"
        self._environment = environment or RuntimeEnvironment(os="linux", sandbox="bubblewrap")

    async def runtime_environment(self) -> RuntimeEnvironment:
        return self._environment

    async def prepare_workspace(self, session_id: str) -> None:
        self.prepared.append(session_id)


def _fake_db_with_account(*, write: bool = True) -> FakeDB:
    db = FakeDB()
    db.add(
        GitAccount(
            name="github-main",
            host="github.com",
            scope_pattern="github.com/arais-labs/*",
            author_name="Sentinel Bot",
            author_email="sentinel@example.com",
            token_read="read-token",
            token_write="write-token" if write else "",
        )
    )
    return db


async def _runtime_configured_stub(**_kwargs) -> bool:
    return True


def _terminal_manager_stub(manager: _TerminalManagerStub):
    async def _stub(**_kwargs) -> _TerminalManagerStub:
        return manager

    return _stub


def _runtime_context(session_id):
    return ToolRuntimeContext(session_id=session_id, instance_name="main")


@pytest.mark.asyncio
async def test_git_tool_is_registered() -> None:
    registry = build_default_registry()
    tool = registry.get("git")

    assert tool is not None
    assert tool.parameters_schema["properties"]["command"]["enum"] == ["accounts", "read", "write"]
    assert "cli_command" in tool.parameters_schema["properties"]


@pytest.mark.asyncio
async def test_git_read_runs_hidden_in_ssh_runtime(monkeypatch) -> None:
    from app.services.araios.system_modules.git_tool import handlers

    db = _fake_db_with_account()
    manager = _TerminalManagerStub()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", _SessionFactory(db))
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)
    monkeypatch.setattr(handlers, "get_runtime_terminal_manager", _terminal_manager_stub(manager))

    registry = build_default_registry()
    executor = ToolExecutor(registry)
    session_id = uuid4()

    result, _duration_ms = await executor.execute(
        "git",
        {
            "command": "read",
            "cli_command": "git clone https://github.com/arais-labs/sentinel.git",
            "timeout_seconds": 30,
        },
        runtime=_runtime_context(session_id),
    )

    assert result["ok"] is True
    assert result["network_mode"] == "read"
    assert result["account"]["name"] == "github-main"
    assert result["stdout"] == "ok\n"
    assert manager.prepared == [str(session_id)]
    assert len(manager.ssh.calls) == 1
    call = manager.ssh.calls[0]
    assert "bwrap" in str(call["command"])
    assert "git clone https://github.com/arais-labs/sentinel.git" in str(call["command"])
    assert call["timeout"] == 30
    env = call["env"]
    assert isinstance(env, dict)
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "read-token" not in str(call["command"])
    assert "tmux" not in str(call["command"])


@pytest.mark.asyncio
async def test_git_write_requires_write_command() -> None:
    registry = build_default_registry()
    executor = ToolExecutor(registry)

    with pytest.raises(ToolValidationError, match="must be 'read'"):
        await executor.execute(
            "git",
            {"command": "write", "cli_command": "git status"},
            runtime=ToolRuntimeContext(session_id=uuid4()),
            agent_mode="full_permission",
        )


@pytest.mark.asyncio
async def test_git_accounts_filters_by_repo(monkeypatch) -> None:
    from app.services.araios.system_modules.git_tool import handlers

    db = _fake_db_with_account()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", _SessionFactory(db))

    registry = build_default_registry()
    executor = ToolExecutor(registry)

    result, _duration_ms = await executor.execute(
        "git",
        {
            "command": "accounts",
            "repo_url": "https://github.com/arais-labs/sentinel.git",
        },
    )

    assert result["total"] == 1
    assert result["accounts"][0]["name"] == "github-main"
    assert result["accounts"][0]["has_read_token"] is True


@pytest.mark.asyncio
async def test_git_gh_uses_ephemeral_token_env(monkeypatch) -> None:
    from app.services.araios.system_modules.git_tool import handlers

    db = _fake_db_with_account()
    manager = _TerminalManagerStub()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", _SessionFactory(db))
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)
    monkeypatch.setattr(handlers, "get_runtime_terminal_manager", _terminal_manager_stub(manager))

    registry = build_default_registry()
    executor = ToolExecutor(registry)

    result, _duration_ms = await executor.execute(
        "git",
        {
            "command": "read",
            "cli_command": "gh repo view arais-labs/sentinel",
        },
        runtime=_runtime_context(uuid4()),
    )

    assert result["ok"] is True
    call = manager.ssh.calls[0]
    env = call["env"]
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "read-token"
    assert env["GITHUB_TOKEN"] == "read-token"
    assert "read-token" not in str(call["command"])


@pytest.mark.asyncio
async def test_git_gh_pr_view_infers_owner_from_origin(monkeypatch) -> None:
    from app.services.araios.system_modules.git_tool import handlers

    db = _fake_db_with_account()
    manager = _TerminalManagerStub()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", _SessionFactory(db))
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)
    monkeypatch.setattr(handlers, "get_runtime_terminal_manager", _terminal_manager_stub(manager))

    registry = build_default_registry()
    executor = ToolExecutor(registry)

    result, _duration_ms = await executor.execute(
        "git",
        {
            "command": "read",
            "cli_command": "gh pr view 123",
            "cwd": "/workspace/sentinel",
        },
        runtime=_runtime_context(uuid4()),
    )

    assert result["ok"] is True
    assert len(manager.ssh.calls) == 2
    assert "git remote get-url origin" in str(manager.ssh.calls[0]["command"])
    assert "gh pr view 123" in str(manager.ssh.calls[1]["command"])
    assert result["account"]["name"] == "github-main"


@pytest.mark.asyncio
async def test_git_reports_missing_runtime_executable(monkeypatch) -> None:
    from app.services.araios.system_modules.git_tool import handlers

    db = _fake_db_with_account()
    manager = _TerminalManagerStub()
    manager.ssh.next_result = RuntimeExecResult(exit_status=127, stdout="", stderr="bash: gh: command not found\n")
    monkeypatch.setattr(handlers, "AsyncSessionLocal", _SessionFactory(db))
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)
    monkeypatch.setattr(handlers, "get_runtime_terminal_manager", _terminal_manager_stub(manager))

    registry = build_default_registry()
    executor = ToolExecutor(registry)

    result, _duration_ms = await executor.execute(
        "git",
        {
            "command": "read",
            "cli_command": "gh repo view arais-labs/sentinel",
        },
        runtime=_runtime_context(uuid4()),
    )

    assert result["ok"] is False
    assert result["returncode"] == 127
    assert "Required executable 'gh' is not available" in result["stderr"]


@pytest.mark.asyncio
async def test_git_read_runs_hidden_in_macos_seatbelt_runtime(monkeypatch) -> None:
    from app.services.araios.system_modules.git_tool import handlers

    db = _fake_db_with_account()
    manager = _TerminalManagerStub(environment=RuntimeEnvironment(os="darwin", sandbox="seatbelt"))
    monkeypatch.setattr(handlers, "AsyncSessionLocal", _SessionFactory(db))
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)
    monkeypatch.setattr(handlers, "get_runtime_terminal_manager", _terminal_manager_stub(manager))

    registry = build_default_registry()
    executor = ToolExecutor(registry)
    session_id = uuid4()

    result, _duration_ms = await executor.execute(
        "git",
        {
            "command": "read",
            "cli_command": "git clone https://github.com/arais-labs/sentinel.git",
            "cwd": "/workspace/subdir",
            "timeout_seconds": 30,
        },
        runtime=_runtime_context(session_id),
    )

    assert result["ok"] is True
    call = manager.ssh.calls[0]
    command = str(call["command"])
    assert "sandbox-exec -f /srv/sentinel/" in command
    assert "/bin/sh -lc" not in command
    assert "bwrap" not in command
    assert f"/srv/sentinel/{session_id}/workspace/subdir" in command
    assert f"HOME=/srv/sentinel/{session_id}/state/home" in command
    assert f"TMPDIR=/srv/sentinel/{session_id}/tmp" in command
    assert "/Library/Developer" in command
    assert "xcrun --find git" in command
    assert "sentinel_resolve_tool" in command
    assert " git " in command
