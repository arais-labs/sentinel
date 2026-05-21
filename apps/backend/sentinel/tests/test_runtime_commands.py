from __future__ import annotations

import pytest

from app.config import Settings
from app.services.runtime.linux_bubblewrap import build_bubblewrap_argv
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


@pytest.mark.parametrize("session_id", ["../escape", "", "bad/slash", "bad\nnewline"])
def test_workspace_paths_reject_invalid_session_ids(session_id: str) -> None:
    with pytest.raises(RuntimeWorkspaceError):
        workspace_paths(session_id, root="/srv/sentinel")


def test_workspace_root_must_be_absolute() -> None:
    with pytest.raises(RuntimeWorkspaceError):
        workspace_paths("session-123", root="relative/root")


def test_settings_use_sentinel_runtime_workspaces_dir(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("SENTINEL_RUNTIME_WORKSPACES_DIR", "/tmp/sentinel-workspaces")

    loaded = Settings(_env_file=None)

    assert loaded.runtime_workspaces_dir == "/tmp/sentinel-workspaces"


def test_prepare_workspace_script_is_filesystem_only() -> None:
    script = build_prepare_workspace_script("session-123", root="/srv/sentinel")

    assert "mkdir -p" in script
    assert "/srv/sentinel/session-123/workspace" in script
    assert "manifest.json" in script
    assert "tmux -S" not in script
    assert "bwrap" not in script
    assert "rm -rf" not in script


def test_delete_workspace_script_is_safe_and_process_free() -> None:
    script = build_delete_workspace_script("session-123", root="/srv/sentinel")

    assert "refusing to delete symlinked session root" in script
    assert "session path escaped workspaces root" in script
    assert "rm -rf --one-file-system --" in script
    assert "/srv/sentinel/session-123" in script
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
    script = build_open_tmux_script("session-123", terminal_id="main", root="/srv/sentinel")

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
    assert "tmux -S" in script
    assert "new-session" in script
    assert "HOME=/state/home" in script
    assert script.index("bwrap") < script.index("new-session")


def test_close_tmux_only_targets_terminal_socket() -> None:
    script = build_close_tmux_script("session-123", terminal_id="main", root="/srv/sentinel")

    assert "kill-session" in script
    assert "/srv/sentinel/session-123/state/tmux/main.sock" in script
    assert "rm -f /srv/sentinel/session-123/state/tmux/main.sock" in script
    assert ".pid" not in script
    assert "rm -rf" not in script


def test_tmux_status_reports_missing_without_creating_workspace() -> None:
    script = build_tmux_status_script("session-123", terminal_id="main", root="/srv/sentinel")

    assert "echo missing" in script
    assert "mkdir -p" not in script
    assert "has-session" in script
    assert "/srv/sentinel/session-123/state/tmux/main.sock" in script
