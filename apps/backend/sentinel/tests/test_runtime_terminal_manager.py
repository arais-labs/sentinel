from __future__ import annotations

from typing import Any

import pytest

from app.services.runtime.terminal_manager import (
    BackgroundJobHandle,
    RuntimeTerminalManager,
    TerminalBlockedError,
    _decode_tmux_control_value,
    _parse_tmux_control_output,
)
from app.schemas.runtime import RuntimeExecResult


class _SSHStub:
    def __init__(self) -> None:
        self.scripts: list[str] = []
        self.script_args: list[list[str]] = []
        self.commands: list[str] = []
        self.responses: list[RuntimeExecResult] = []

    def push(self, stdout: str = "", stderr: str = "", exit_status: int = 0) -> None:
        self.responses.append(
            RuntimeExecResult(exit_status=exit_status, stdout=stdout, stderr=stderr)
        )

    async def run_script(
        self, script: str, *, args: list[str] | None = None, timeout: int = 300
    ) -> RuntimeExecResult:
        _ = timeout
        self.scripts.append(script)
        self.script_args.append(args or [])
        if "echo missing" in script:
            return RuntimeExecResult(exit_status=0, stdout="running\n", stderr="")
        return RuntimeExecResult(exit_status=0, stdout="", stderr="")

    async def run(self, command: str, *, timeout: int = 300, **_: Any) -> RuntimeExecResult:
        self.commands.append(command)
        if command.startswith("uname -s"):
            return RuntimeExecResult(exit_status=0, stdout="Linux\n", stderr="")
        if "command -v bwrap" in command:
            return RuntimeExecResult(exit_status=0, stdout="yes\n", stderr="")
        if "command -v sandbox-exec" in command:
            return RuntimeExecResult(exit_status=0, stdout="no\n", stderr="")
        if self.responses:
            return self.responses.pop(0)
        return RuntimeExecResult(exit_status=0, stdout="", stderr="")


def test_visible_command_keeps_simple_command_bare() -> None:
    manager = RuntimeTerminalManager(_SSHStub(), workspaces_root="/srv/sentinel")

    assert manager._build_visible_command("ls -la") == "ls -la"


def test_visible_command_scopes_cwd_and_env() -> None:
    manager = RuntimeTerminalManager(_SSHStub(), workspaces_root="/srv/sentinel")

    assert (
        manager._build_visible_command("make build", cwd="/workspace/app", env={"CC": "clang"})
        == "(cd /workspace/app && export CC=clang; make build)"
    )


def test_tmux_control_output_decodes_escaped_bytes() -> None:
    assert _decode_tmux_control_value(r"hello\012there\134") == b"hello\nthere\\"
    assert _parse_tmux_control_output(r"%output %0 hello\015\012" + "\n") == b"hello\r\n"
    assert _parse_tmux_control_output("%session-changed $0 sentinel_0\n") == b""
    assert _parse_tmux_control_output("plain command output belongs to the block parser\n") == b""


@pytest.mark.asyncio
async def test_open_terminal_caches_running_session() -> None:
    ssh = _SSHStub()
    manager = RuntimeTerminalManager(ssh, workspaces_root="/srv/sentinel")

    first = await manager.open_terminal("session-123", terminal_id="main")
    script_count = len(ssh.scripts)
    second = await manager.open_terminal("session-123", terminal_id="main")

    assert first.status == "running"
    assert second.status == "running"
    assert len(ssh.scripts) == script_count


@pytest.mark.asyncio
async def test_run_command_opens_tmux_sends_plain_command_and_parses_marker() -> None:
    ssh = _SSHStub()
    ssh.push(stdout="bash\n")  # foreground command check
    ssh.push(stdout="0\n")  # pipe log size before send
    ssh.push()  # send C-u
    ssh.push()  # send literal command
    ssh.push()  # send Enter
    ssh.push(stdout="echo hello\nhello\n\x1b]133;D;0\x1b\\")

    manager = RuntimeTerminalManager(ssh, workspaces_root="/srv/sentinel")

    result = await manager.run_command(
        "session-123",
        "echo hello",
        terminal_id="main",
        timeout=5,
    )

    assert result == RuntimeExecResult(exit_status=0, stdout="hello", stderr="")
    assert any(
        "/srv/sentinel/session-123/workspace" in arg for args in ssh.script_args for arg in args
    )
    assert any("nohup bwrap" in script for script in ssh.scripts)
    send_literals = [
        command for command in ssh.commands if "send-keys" in command and " -l " in command
    ]
    assert send_literals == [
        "tmux -S /srv/sentinel/session-123/state/tmux/main.sock "
        "send-keys -t sentinel_main -l 'echo hello'"
    ]


@pytest.mark.asyncio
async def test_run_command_refuses_when_foreground_process_is_not_shell() -> None:
    ssh = _SSHStub()
    ssh.push(stdout="vim\n")
    manager = RuntimeTerminalManager(ssh, workspaces_root="/srv/sentinel")

    with pytest.raises(TerminalBlockedError) as info:
        await manager.run_command("session-123", "echo hello", terminal_id="main", timeout=5)

    assert info.value.reason == "foreground_process_running"
    assert info.value.current_command == "vim"


@pytest.mark.asyncio
async def test_read_tail_uses_tmux_pane_capture() -> None:
    ssh = _SSHStub()
    ssh.push(stdout="(cd /workspace && echo ok)\nok\nuser@host:/workspace$ \n")
    manager = RuntimeTerminalManager(ssh, workspaces_root="/srv/sentinel")

    output = await manager.read_tail("session-123", terminal_id="main")

    assert ssh.commands[-1].startswith(
        "tmux -S /srv/sentinel/session-123/state/tmux/main.sock capture-pane"
    )
    assert output == "(cd /workspace && echo ok)\nok\nuser@host:/workspace$"


@pytest.mark.asyncio
async def test_read_tail_truncates_at_line_boundary() -> None:
    ssh = _SSHStub()
    ssh.push(stdout=("x" * 260) + "\nsecond line\nthird line\n")
    manager = RuntimeTerminalManager(ssh, workspaces_root="/srv/sentinel")

    output = await manager.read_tail("session-123", terminal_id="main", tail_bytes=256)

    assert output == "second line\nthird line"


@pytest.mark.asyncio
async def test_start_background_command_allocates_terminal_and_sends_job_script(
    monkeypatch,
) -> None:
    ssh = _SSHStub()
    ssh.push(stdout="bash\n")  # foreground command check
    ssh.push(stdout="0\n")  # pipe log size before send
    ssh.push()  # send C-u
    ssh.push()  # send literal command
    ssh.push()  # send Enter
    manager = RuntimeTerminalManager(ssh, workspaces_root="/srv/sentinel")

    async def _no_watch(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(manager, "_watch_background_command", _no_watch)

    handle = await manager.start_background_command(
        "session-123",
        "sleep 1 && echo done",
        cwd="/workspace",
        env={"A": "B"},
    )

    assert isinstance(handle, BackgroundJobHandle)
    assert handle.terminal_id.startswith("bg-")
    assert handle.result_path.endswith(f"/state/runtime/jobs/{handle.id}/done.json")
    assert any(
        f"/srv/sentinel/session-123/state/runtime/jobs/{handle.id}" in arg
        for args in ssh.script_args
        for arg in args
    )
    assert any("sleep 1 && echo done" in arg for args in ssh.script_args for arg in args)
    assert any("bash /state/runtime/jobs/" in command for command in ssh.commands)
