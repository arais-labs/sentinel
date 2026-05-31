from __future__ import annotations

import pytest

from app.services.runtime.darwin_seatbelt import build_seatbelt_profile
from app.services.runtime.linux_bubblewrap import build_bubblewrap_argv
from app.services.runtime.ssh_client import build_shell_command
from app.services.runtime.status import (
    RuntimeStatusCheck,
    _capabilities,
    _detected_os,
    _detected_sandbox,
)
from app.services.runtime.tmux import (
    SENTINEL_BASHRC,
    build_close_tmux_script,
    build_open_tmux_script,
    build_tmux_status_script,
)
from app.services.runtime.workspace import (
    RuntimeWorkspaceError,
    build_delete_workspace_script,
    build_prepare_workspace_script,
    workspace_paths,
)


def test_workspace_paths_use_remote_root() -> None:
    paths = workspace_paths("session-123", root="/srv/sentinel")

    assert paths.session_root == "/srv/sentinel/session-123"
    assert paths.workspace == "/srv/sentinel/session-123/workspace"
    assert paths.home == "/srv/sentinel/session-123/state/home"
    assert paths.tmux == "/srv/sentinel/session-123/state/tmux"
    assert paths.browser == "/srv/sentinel/session-123/state/browser"


def test_ssh_run_shell_wrapper_does_not_read_login_profiles() -> None:
    command = build_shell_command("echo ok")

    assert command.startswith("bash --noprofile --norc -c ")
    assert "bash -lc" not in command


@pytest.mark.parametrize("session_id", ["../escape", "", "bad/slash", "bad\nnewline"])
def test_workspace_paths_reject_invalid_session_ids(session_id: str) -> None:
    with pytest.raises(RuntimeWorkspaceError):
        workspace_paths(session_id, root="/srv/sentinel")


def test_workspace_root_must_be_absolute() -> None:
    with pytest.raises(RuntimeWorkspaceError):
        workspace_paths("session-123", root="relative/root")


def test_prepare_workspace_script_is_filesystem_only() -> None:
    script, args = build_prepare_workspace_script("session-123", root="/srv/sentinel")

    assert "directories" in script
    assert "/srv/sentinel/session-123/workspace" in args[0]
    assert "manifest.json" in args[0]
    assert "tmux -S" not in script
    assert "bwrap" not in script
    assert "rm -rf" not in script


def test_delete_workspace_script_is_safe_and_process_free() -> None:
    script, args = build_delete_workspace_script("session-123", root="/srv/sentinel")

    assert "refusing to delete symlinked session root" in script
    assert "session path escaped workspaces root" in script
    assert "rm -rf --one-file-system --" in script
    assert 'rm -rf -- "${session_root}"' in script
    assert "/srv/sentinel/session-123" in args[0]
    assert "tmux" not in script
    assert "bwrap" not in script


def test_bubblewrap_does_not_bind_home_root_or_srv() -> None:
    paths = workspace_paths("session-123", root="/srv/sentinel")
    argv = build_bubblewrap_argv(paths, ["bash", "-lc", "pwd"])

    assert argv[0] == "bwrap"
    assert paths.workspace in argv
    assert paths.state in argv
    assert paths.tmp in argv
    assert "/workspace" in argv
    assert "/state" in argv
    assert "/run/systemd/resolve/stub-resolv.conf" in argv
    assert "/run/systemd/resolve/resolv.conf" in argv
    assert "/home" not in argv
    assert "/root" not in argv
    assert "/srv" not in argv


def test_open_tmux_starts_tmux_inside_bubblewrap() -> None:
    script, _ = build_open_tmux_script("session-123", terminal_id="main", root="/srv/sentinel")

    assert "test -d /srv/sentinel/session-123/workspace" in script
    assert "bwrap" in script
    assert "nohup bwrap" in script
    assert "</dev/null &" in script
    assert "/srv/sentinel/session-123/logs/tmux-main.log" in script
    assert "pipe-pane" in script
    assert "main.bashrc" in script
    assert "PROMPT_COMMAND='__sentinel_prompt_command'" in SENTINEL_BASHRC
    assert "alias ls='ls --color=auto'" in SENTINEL_BASHRC
    assert "\\[\\033[38;5;81m\\]" in SENTINEL_BASHRC
    assert "/state/tmux/main.sock" in script
    assert "/srv/sentinel/session-123/state/tmux/main.sock" in script
    assert "sentinel_tmux=tmux" in script
    assert (
        '"$sentinel_tmux" -f /dev/null -S /srv/sentinel/session-123/state/tmux/main.sock' in script
    )
    assert "new-session" in script
    assert "HOME=/state/home" in script
    assert script.index("bwrap") < script.index("new-session")


def test_open_tmux_can_use_macos_seatbelt_wrapper() -> None:
    script, _ = build_open_tmux_script(
        "session-123",
        terminal_id="main",
        root="/srv/sentinel",
        os_name="darwin",
        sandbox="seatbelt",
    )

    assert "sandbox-exec -f /srv/sentinel/session-123/state/runtime/seatbelt-main.sb" in script
    assert "seatbelt-tool-roots" in script
    assert "sentinel_resolve_tool()" in script
    assert "sentinel_login_shell" in script
    assert "command -v $sentinel_name" in script
    assert "xcrun --find git" in script
    assert "/Library/Developer/CommandLineTools/usr/bin" in script
    assert "sentinel_tmux=$(command -v tmux" in script
    assert "__RESOLVE_HOST_TMUX__" not in script
    assert "__HOST_TMUX_HAS_SESSION__" not in script
    assert "/bin/bash --noprofile --norc -lc" in script
    assert "bwrap" not in script
    assert "/srv/sentinel/session-123/workspace" in script
    assert "../state/home" in script
    assert "../state/tmux/main.sock" in script
    assert "/srv/sentinel/session-123/state/tmux/main.sock" in script


def test_seatbelt_profile_allows_session_writes_only() -> None:
    profile = build_seatbelt_profile(workspace_paths("session-123", root="/srv/sentinel"))

    assert "(deny default)" in profile
    assert "(allow file-read*)" not in profile
    assert "(allow process-exec)" in profile
    assert "(allow process-fork)" in profile
    assert "(allow pseudo-tty)" in profile
    assert '(subpath "/Library/Developer")' in profile
    assert '(subpath "/srv/sentinel/session-123")' in profile
    assert '(subpath "/srv/sentinel/session-123/workspace")' not in profile
    assert '(subpath "/opt/homebrew")' not in profile


def test_seatbelt_profile_adds_common_canonical_aliases() -> None:
    profile = build_seatbelt_profile(workspace_paths("session-123", root="/tmp/sentinel"))

    assert '(subpath "/tmp/sentinel/session-123")' in profile
    assert '(subpath "/private/tmp/sentinel/session-123")' in profile
    assert '(literal "/private/tmp/.sentinel/runtime-workspaces")' not in profile
    assert '(literal "/private/tmp/sentinel")' in profile


def test_runtime_capabilities_require_os_sandbox() -> None:
    checks = [
        RuntimeStatusCheck("ssh_connect", "SSH", "pass"),
        RuntimeStatusCheck("ssh_command", "Remote command", "pass"),
        RuntimeStatusCheck("workspace_writable", "Workspace", "pass"),
        RuntimeStatusCheck("os", "OS", "pass", detail="linux"),
        RuntimeStatusCheck("sandbox", "Sandbox", "fail", detail="unavailable"),
        RuntimeStatusCheck("binary_bash", "bash", "pass"),
        RuntimeStatusCheck("binary_tmux", "tmux", "pass"),
        RuntimeStatusCheck("binary_python3", "python3", "pass"),
        RuntimeStatusCheck("binary_git", "git", "pass"),
        RuntimeStatusCheck("binary_gh", "gh", "pass"),
    ]

    assert _detected_os(checks) == "linux"
    assert _detected_sandbox(checks) == "unavailable"
    assert _capabilities(checks)["shell"] == "unavailable"


def test_runtime_capabilities_support_darwin_core_only() -> None:
    checks = [
        RuntimeStatusCheck("ssh_connect", "SSH", "pass"),
        RuntimeStatusCheck("ssh_command", "Remote command", "pass"),
        RuntimeStatusCheck("workspace_writable", "Workspace", "pass"),
        RuntimeStatusCheck("os", "OS", "pass", detail="darwin"),
        RuntimeStatusCheck("sandbox", "Sandbox", "pass", detail="seatbelt"),
        RuntimeStatusCheck("binary_bash", "bash", "pass"),
        RuntimeStatusCheck("binary_tmux", "tmux", "pass"),
        RuntimeStatusCheck("binary_python3", "python3", "pass"),
        RuntimeStatusCheck("binary_git", "git", "pass"),
        RuntimeStatusCheck("binary_gh", "gh", "pass"),
        RuntimeStatusCheck("desktop_stack", "Desktop", "warn", detail="Linux-only", required=False),
    ]

    capabilities = _capabilities(checks)

    assert capabilities["shell"] == "ready"
    assert capabilities["files"] == "ready"
    assert capabilities["git"] == "ready"
    assert capabilities["jobs"] == "ready"
    assert capabilities["desktop"] == "unavailable"
    assert capabilities["browser"] == "unavailable"


def test_close_tmux_only_targets_terminal_socket() -> None:
    script, _ = build_close_tmux_script("session-123", terminal_id="main", root="/srv/sentinel")

    assert "kill-session" in script
    assert "/srv/sentinel/session-123/state/tmux/main.sock" in script
    assert "rm -f /srv/sentinel/session-123/state/tmux/main.sock" in script
    assert ".pid" not in script
    assert "rm -rf" not in script


def test_tmux_status_reports_missing_without_creating_workspace() -> None:
    script, _ = build_tmux_status_script("session-123", terminal_id="main", root="/srv/sentinel")

    assert "echo missing" in script
    assert "mkdir -p" not in script
    assert "has-session" in script
    assert "/srv/sentinel/session-123/state/tmux/main.sock" in script
