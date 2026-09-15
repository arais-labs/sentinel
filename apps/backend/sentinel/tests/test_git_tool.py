from __future__ import annotations

from uuid import uuid4

import pytest

from app.models import GitAccount
from app.schemas.runtime import RuntimeExecResult
from app.services.runtime.environment import RuntimeEnvironment
from sentral.errors import ToolValidationError
from app.services.tools.executor import ToolExecutor
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
                stdout="https://github.com/example-org/sample-app.git\n",
                stderr="",
            )
        return RuntimeExecResult(exit_status=0, stdout="ok\n", stderr="")


class _TerminalManagerStub:
    def __init__(self, *, environment: RuntimeEnvironment | None = None) -> None:
        self.ssh = _SSHStub()
        self.prepared: list[str] = []
        from app.services.runtime.workspace import WorkspaceLocation

        self.workspace_location = WorkspaceLocation("/Users/test/project", "/var/lib/sentinel")
        self._environment = environment or RuntimeEnvironment(os="linux", sandbox="container")

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
            scope_pattern="github.com/example-org/*",
            author_name="Sentinel Bot",
            author_email="sentinel@example.com",
            token="account-token" if write else "",
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
    assert tool.parameters_schema["properties"]["action"]["enum"] == [
        "accounts",
        "gh_read",
        "gh_write",
        "read",
        "repos",
        "run_script",
        "write",
    ]
    assert "cli_command" in tool.parameters_schema["properties"]


@pytest.mark.asyncio
async def test_git_read_runs_hidden_in_ssh_runtime(monkeypatch) -> None:
    from app.services.modules.builtins.git_tool import handlers

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
            "action": "read",
            "cli_command": "git clone https://github.com/example-org/sample-app.git",
            "timeout_seconds": 30,
        },
        runtime=_runtime_context(session_id),
    )

    assert result["ok"] is True
    assert result["network_mode"] == "read"
    assert result["account"]["name"] == "github-main"
    assert result["stdout"] == "ok\n"
    assert manager.brokered is True
    assert len(manager.ssh.calls) == 1
    call = manager.ssh.calls[0]
    assert str(call["command"]).startswith("cd /Users/test/project && exec git ")
    assert "git clone https://github.com/example-org/sample-app.git" in str(call["command"])
    assert call["timeout"] == 30
    env = call["env"]
    assert isinstance(env, dict)
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "account-token" not in str(call["command"])
    assert "tmux" not in str(call["command"])


@pytest.mark.asyncio
async def test_git_write_requires_write_command() -> None:
    registry = build_default_registry()
    executor = ToolExecutor(registry)

    with pytest.raises(ToolValidationError, match="action=read"):
        await executor.execute(
            "git",
            {"action": "write", "cli_command": "git status"},
            runtime=ToolRuntimeContext(session_id=uuid4()),
            agent_mode="full_permission",
        )


@pytest.mark.asyncio
async def test_git_accounts_filters_by_repo(monkeypatch) -> None:
    from app.services.modules.builtins.git_tool import handlers

    db = _fake_db_with_account()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", _SessionFactory(db))

    registry = build_default_registry()
    executor = ToolExecutor(registry)

    result, _duration_ms = await executor.execute(
        "git",
        {
            "action": "accounts",
            "repo_url": "https://github.com/example-org/sample-app.git",
        },
    )

    assert result["total"] == 1
    assert result["accounts"][0]["name"] == "github-main"
    assert result["accounts"][0]["has_token"] is True


@pytest.mark.asyncio
async def test_git_gh_routes_authentication_through_broker(monkeypatch) -> None:
    from app.services.modules.builtins.git_tool import handlers

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
            "action": "gh_read",
            "cli_command": "gh repo view example-org/sample-app",
        },
        runtime=_runtime_context(uuid4()),
    )

    assert result["ok"] is True
    call = manager.ssh.calls[0]
    env = call["env"]
    assert isinstance(env, dict)
    assert "GH_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env
    assert manager.brokered is True
    assert "account-token" not in str(call["command"])


@pytest.mark.asyncio
async def test_git_gh_pr_view_infers_owner_from_origin(monkeypatch) -> None:
    from app.services.modules.builtins.git_tool import handlers

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
            "action": "gh_read",
            "cli_command": "gh pr view 123",
            "cwd": "/Users/test/project/sentinel",
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
    from app.services.modules.builtins.git_tool import handlers

    db = _fake_db_with_account()
    manager = _TerminalManagerStub()
    manager.ssh.next_result = RuntimeExecResult(
        exit_status=127, stdout="", stderr="bash: gh: command not found\n"
    )
    monkeypatch.setattr(handlers, "AsyncSessionLocal", _SessionFactory(db))
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)
    monkeypatch.setattr(handlers, "get_runtime_terminal_manager", _terminal_manager_stub(manager))

    registry = build_default_registry()
    executor = ToolExecutor(registry)

    result, _duration_ms = await executor.execute(
        "git",
        {
            "action": "gh_read",
            "cli_command": "gh repo view example-org/sample-app",
        },
        runtime=_runtime_context(uuid4()),
    )

    assert result["ok"] is False
    assert result["returncode"] == 127
    assert "Required executable 'gh' is not available" in result["stderr"]


@pytest.mark.asyncio
async def test_git_read_targets_project_subdirectory_in_container(monkeypatch) -> None:
    from app.services.modules.builtins.git_tool import handlers

    db = _fake_db_with_account()
    manager = _TerminalManagerStub(environment=RuntimeEnvironment(os="linux", sandbox="container"))
    monkeypatch.setattr(handlers, "AsyncSessionLocal", _SessionFactory(db))
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)
    monkeypatch.setattr(handlers, "get_runtime_terminal_manager", _terminal_manager_stub(manager))

    registry = build_default_registry()
    executor = ToolExecutor(registry)
    session_id = uuid4()

    result, _duration_ms = await executor.execute(
        "git",
        {
            "action": "read",
            "cli_command": "git clone https://github.com/example-org/sample-app.git",
            "cwd": "/Users/test/project/subdir",
            "timeout_seconds": 30,
        },
        runtime=_runtime_context(session_id),
    )

    assert result["ok"] is True
    call = manager.ssh.calls[0]
    command = str(call["command"])
    assert (
        command
        == "cd /Users/test/project/subdir && exec git clone https://github.com/example-org/sample-app.git"
    )


def test_project_paths_use_real_root_without_virtual_path_translation():
    from app.services.modules.builtins.git_tool.handlers import (
        _build_hidden_runtime_command,
        _project_cwd,
    )
    from app.services.runtime.workspace import WorkspaceLocation, workspace_paths

    project = "/Users/test/My Projects/repo"
    assert _project_cwd(None, project) == project
    assert _project_cwd("src", project) == project + "/src"
    assert _project_cwd(project + "/src", project) == project + "/src"
    assert _project_cwd("/tmp/repo", project) == "/tmp/repo"
    assert _project_cwd("../outside", project) == "/Users/test/My Projects/outside"
    paths = workspace_paths("test", root=WorkspaceLocation(project, "/var/lib/sentinel"))
    command = _build_hidden_runtime_command(
        paths,
        os_name="linux",
        sandbox="container",
        cwd=project,
        tokens=["git", "status"],
    )
    assert command == "cd '/Users/test/My Projects/repo' && exec git status"


@pytest.fixture(autouse=True)
def broker_stub(monkeypatch):
    """Handler tests isolate transport; test_git_surface exercises the real broker."""
    import shlex

    from app.services.modules.builtins.git_tool import credential_broker

    async def run(*, terminal, tokens, cwd, account, mode, timeout, repo=None, env=None):
        terminal.brokered = True
        result = await terminal.ssh.run(
            "cd " + shlex.quote(cwd) + " && exec " + shlex.join(tokens),
            timeout=timeout,
            env={"GIT_TERMINAL_PROMPT": "0", **(env or {})},
        )
        stderr = result.stderr
        if result.exit_status == 127:
            stderr = f"Required executable '{tokens[0]}' is not available. " + stderr
        return {
            "ok": result.exit_status == 0,
            "returncode": result.exit_status,
            "stdout": result.stdout,
            "stderr": stderr,
            "cwd": cwd,
        }

    monkeypatch.setattr(credential_broker, "run_brokered", run)
