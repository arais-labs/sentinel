from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
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


def test_visible_command_wraps_bare_multiline_in_subshell() -> None:
    manager = RuntimeTerminalManager(_SSHStub(), workspaces_root="/srv/sentinel")

    # A bare multiline command (e.g. a heredoc + trailing command) must run as one
    # atomic subshell, not be submitted line-by-line.
    cmd = "cat > f <<'EOF'\nhi\nEOF\ncat f"
    assert manager._build_visible_command(cmd) == "(\ncat > f <<'EOF'\nhi\nEOF\ncat f\n)"


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
    # The command is typed into the pane via a paced chunk loop (run_script),
    # never a single literal send-keys argument that could overflow the tty.
    feed = [
        (script, args)
        for script, args in zip(ssh.scripts, ssh.script_args)
        if 'send-keys -t sentinel_main -l "${sentinel_cmd:' in script
    ]
    assert feed, "expected a paced pane-feed script"
    assert feed[0][1] == ["echo hello"]
    assert not any(
        "send-keys" in command and " -l " in command and "echo hello" in command
        for command in ssh.commands
    )


@pytest.mark.skipif(shutil.which("tmux") is None, reason="requires real tmux")
def test_pane_feed_delivers_large_heredoc_intact(tmp_path: Path) -> None:
    # End-to-end: a ~26 KB heredoc must land byte-for-byte (paced feed) and the
    # command chained after it must run (subshell wrap), on whatever bash is here.
    from app.services.runtime.tmux import build_pane_feed_script

    socket = str(tmp_path / "s.sock")
    target = tmp_path / "out.txt"
    sentinel = tmp_path / "trailing.flag"
    name = "t"
    body = "\n".join(f"line{i:05d} " + "x" * 60 for i in range(400))  # ~26 KB
    raw = f"cat > {target} << 'SENTINEL_EOF'\n{body}\nSENTINEL_EOF\ntouch {sentinel}"
    command = "(\n" + raw + "\n)"  # mirrors _build_visible_command's multiline wrap
    os_name = "darwin" if sys.platform == "darwin" else "linux"
    feed_script = build_pane_feed_script(socket, name, os_name=os_name)
    shell = shutil.which("bash") or "/bin/bash"

    def pane_command() -> str:
        probe = subprocess.run(
            ["tmux", "-S", socket, "display-message", "-p", "-t", name, "#{pane_current_command}"],
            capture_output=True,
            text=True,
        )
        return probe.stdout.strip()

    try:
        subprocess.run(
            [
                "tmux",
                "-S",
                socket,
                "new-session",
                "-d",
                "-s",
                name,
                "-x",
                "200",
                "-y",
                "50",
                "/bin/bash",
                "--norc",
                "-i",
            ],
            check=True,
            capture_output=True,
        )
        ready = time.time() + 5
        while time.time() < ready and pane_command() not in {"bash", "sh"}:
            time.sleep(0.1)
        subprocess.run(
            ["tmux", "-S", socket, "send-keys", "-t", name, "C-u"], check=True, capture_output=True
        )
        # Run the feed script the way run_script does: bash -s -- <command>, script on stdin.
        fed = subprocess.run(
            [shell, "-s", "--", command], input=feed_script.encode(), capture_output=True
        )
        assert fed.returncode == 0, fed.stderr.decode()
        subprocess.run(
            ["tmux", "-S", socket, "send-keys", "-t", name, "Enter"],
            check=True,
            capture_output=True,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            if sentinel.exists() and target.exists() and target.read_text() == body + "\n":
                break
            time.sleep(0.1)
    finally:
        subprocess.run(["tmux", "-S", socket, "kill-server"], capture_output=True)

    assert target.exists(), "heredoc never produced the file"
    assert target.read_text() == body + "\n"  # no dropped/mangled bytes
    assert sentinel.exists(), "command chained after the heredoc did not run"


@pytest.mark.skipif(shutil.which("tmux") is None, reason="requires real tmux")
def test_osc133_capture_is_clean_for_wrapped_multiline(tmp_path: Path) -> None:
    # The real rcfile emits OSC 133 A/B/C/D; capturing C->D must yield only the
    # command output (no echoed input/prompts) for a wrapped multiline command.
    from app.services.runtime.terminal_manager import (
        _OSC_C_PATTERN,
        _OSC_D_PATTERN,
        _clean_terminal_text,
    )
    from app.services.runtime.tmux import SENTINEL_BASHRC

    socket = str(tmp_path / "s.sock")
    log = tmp_path / "pane.log"
    rc = tmp_path / "sentinel.bashrc"
    rc.write_text(SENTINEL_BASHRC)
    name = "t"
    # As _build_visible_command wraps multiline commands.
    cmd = "(\nprintf 'OUT1\\n'\ncat <<'EOF'\nhello\nEOF\nprintf 'OUT2\\n'\n)"

    def pane_command() -> str:
        probe = subprocess.run(
            ["tmux", "-S", socket, "display-message", "-p", "-t", name, "#{pane_current_command}"],
            capture_output=True,
            text=True,
        )
        return probe.stdout.strip()

    try:
        subprocess.run(
            [
                "tmux",
                "-S",
                socket,
                "new-session",
                "-d",
                "-s",
                name,
                "-x",
                "200",
                "-y",
                "50",
                "/bin/bash",
                "--rcfile",
                str(rc),
                "-i",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["tmux", "-S", socket, "pipe-pane", "-o", "-t", name, f"cat >> {log}"],
            check=True,
            capture_output=True,
        )
        ready = time.time() + 5
        while time.time() < ready and pane_command() not in {"bash", "sh"}:
            time.sleep(0.1)
        subprocess.run(
            ["tmux", "-S", socket, "send-keys", "-t", name, "C-u"], check=True, capture_output=True
        )
        for i in range(0, len(cmd), 512):
            subprocess.run(
                ["tmux", "-S", socket, "send-keys", "-t", name, "-l", cmd[i : i + 512]],
                check=True,
                capture_output=True,
            )
            time.sleep(0.02)
        subprocess.run(
            ["tmux", "-S", socket, "send-keys", "-t", name, "Enter"],
            check=True,
            capture_output=True,
        )
        deadline = time.time() + 10
        while time.time() < deadline and not _OSC_D_PATTERN.search(log.read_bytes()):
            time.sleep(0.1)
    finally:
        subprocess.run(["tmux", "-S", socket, "kill-server"], capture_output=True)

    raw = log.read_bytes()
    d = _OSC_D_PATTERN.search(raw)
    assert d, "no command-done marker captured"
    pre = raw[: d.start()]
    cs = list(_OSC_C_PATTERN.finditer(pre))
    assert cs, "no output-start marker captured"
    output = _clean_terminal_text(pre[cs[-1].end() :]).strip()
    assert output == "OUT1\nhello\nOUT2"


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
    # The pane runs the job via `bash <run_path>`, delivered through the paced feed.
    assert any("bash /state/runtime/jobs/" in arg for args in ssh.script_args for arg in args)
