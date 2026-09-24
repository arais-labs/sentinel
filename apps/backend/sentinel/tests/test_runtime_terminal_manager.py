from __future__ import annotations

import shutil
import shlex
import subprocess
import tempfile
import sys
import time
from pathlib import Path

import pytest

from app.services.runtime.terminal_manager import (
    RuntimeTerminalManager,
)
from app.services.runtime.workspace import WorkspaceLocation


def test_visible_command_keeps_simple_command_bare() -> None:
    manager = RuntimeTerminalManager(
        object(), workspace_location=WorkspaceLocation("/project", "/internal/workspace")
    )

    assert manager._build_visible_command("ls -la") == "ls -la"


def test_visible_command_scopes_cwd_and_env() -> None:
    manager = RuntimeTerminalManager(
        object(), workspace_location=WorkspaceLocation("/project", "/internal/workspace")
    )

    assert (
        manager._build_visible_command("make build", cwd="/workspace/app", env={"CC": "clang"})
        == "(cd /workspace/app && export CC=clang; make build)"
    )


def test_visible_command_preserves_multiline_script(tmp_path: Path) -> None:
    manager = RuntimeTerminalManager(
        object(), workspace_location=WorkspaceLocation("/project", "/internal/workspace")
    )

    # A bare multiline command (e.g. a heredoc + trailing command) must run as one
    # atomic subshell, not be submitted line-by-line.
    body = "quotes ' \" backslash \\ dollar $HOME $(touch unexpected) `touch unexpected`\nUnicode: café"
    cmd = "cat > f <<'EOF'\n" + body + "\nEOF\ncat f\nprintf x >> executions"
    command = manager._build_visible_command(cmd)
    assert "\n" not in command
    result = subprocess.run(
        ["/bin/bash", "-c", command], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == body + "\n"
    assert (tmp_path / "f").read_text() == body + "\n"
    assert (tmp_path / "executions").read_text() == "x"
    assert not (tmp_path / "unexpected").exists()


@pytest.fixture
def tmux_socket():
    # macOS Unix socket paths are limited to 104 bytes; pytest's temp root is longer.
    with tempfile.TemporaryDirectory(prefix="sentinel-tmux-", dir="/tmp") as directory:
        yield str(Path(directory) / "s.sock")


@pytest.mark.skipif(shutil.which("tmux") is None, reason="requires real tmux")
def test_pane_feed_delivers_large_heredoc_intact(tmp_path: Path, tmux_socket: str) -> None:
    # End-to-end: a ~26 KB heredoc must land byte-for-byte (paced feed) and the
    # command chained after it must run (subshell wrap), on whatever bash is here.
    from app.services.runtime.tmux import build_pane_feed_script

    socket = tmux_socket
    target = tmp_path / "out.txt"
    sentinel = tmp_path / "trailing.flag"
    name = "t"
    body = "\n".join(f"line{i:05d} " + "x" * 60 for i in range(400))  # ~26 KB
    raw = f"cat > {target} << 'SENTINEL_EOF'\n{body}\nSENTINEL_EOF\ntouch {sentinel}"
    manager = RuntimeTerminalManager(
        object(), workspace_location=WorkspaceLocation("/project", "/internal/workspace")
    )
    command = manager._build_visible_command(raw)
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


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("tmux") is None,
    reason="verifies the macOS local-runtime DEBUG-trap OSC 133;C path; needs tmux",
)
def test_osc133_capture_is_clean_for_wrapped_multiline(tmp_path: Path, tmux_socket: str) -> None:
    # The real rcfile emits OSC 133 A/B/C/D; capturing C->D must yield only the
    # command output (no echoed input/prompts) for a wrapped multiline command.
    from app.services.runtime.terminal_manager import (
        _OSC_C_PATTERN,
        _OSC_D_PATTERN,
        _clean_terminal_text,
    )
    from app.services.runtime.tmux import SENTINEL_BASHRC

    socket = tmux_socket
    log = tmp_path / "pane.log"
    rc = tmp_path / "sentinel.bashrc"
    rc.write_text(SENTINEL_BASHRC)
    name = "t"
    # As _build_visible_command wraps multiline commands.
    manager = RuntimeTerminalManager(
        object(), workspace_location=WorkspaceLocation("/project", "/internal/workspace")
    )
    cmd = manager._build_visible_command(
        "printf 'OUT1\\n'\ncat <<'EOF'\nhello\nEOF\nprintf 'OUT2\\n'"
    )

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
                # Attach capture before starting Bash so its first prompt cannot be missed.
                f"tmux -S {shlex.quote(socket)} wait-for capture-ready; "
                f"exec /bin/bash --rcfile {shlex.quote(str(rc))} -i",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["tmux", "-S", socket, "pipe-pane", "-o", "-t", name, f"cat >> {log}"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["tmux", "-S", socket, "wait-for", "-S", "capture-ready"],
            check=True,
            capture_output=True,
        )
        ready = time.monotonic() + 5
        while not log.exists() or b"\x1b]133;B\x1b\\" not in log.read_bytes():
            assert time.monotonic() < ready, "shell did not emit its prompt-ready marker"
            time.sleep(0.01)
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
        while time.time() < deadline:
            raw = log.read_bytes()
            start = _OSC_C_PATTERN.search(raw)
            if start and _OSC_D_PATTERN.search(raw, start.end()):
                break
            time.sleep(0.1)
    finally:
        subprocess.run(["tmux", "-S", socket, "kill-server"], capture_output=True)

    raw = log.read_bytes()
    start = _OSC_C_PATTERN.search(raw)
    assert start, "no output-start marker captured"
    assert raw[: start.start()].count(b"OUT1") == 1, "command was echoed repeatedly"
    # The initial prompt can also emit D; only match completion after command output starts.
    d = _OSC_D_PATTERN.search(raw, start.end())
    assert d, "no command-done marker captured"
    pre = raw[: d.start()]
    cs = list(_OSC_C_PATTERN.finditer(pre))
    assert cs, "no output-start marker captured"
    output = _clean_terminal_text(pre[cs[-1].end() :]).strip()
    assert output == "OUT1\nhello\nOUT2"
