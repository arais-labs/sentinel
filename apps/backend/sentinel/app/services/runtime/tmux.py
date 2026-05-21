from __future__ import annotations

import re
from pathlib import PurePosixPath
from shlex import quote

from app.services.runtime.linux_bubblewrap import (
    build_bubblewrap_command,
    build_require_workspace_script,
)
from app.services.runtime.remote_commands import load_remote_command
from app.services.runtime.workspace import RuntimeWorkspaceError, workspace_paths


TERMINAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
TMUX_COLS = 200
TMUX_ROWS = 50
TMUX_HISTORY_LIMIT = 50_000
SENTINEL_BASHRC = r"""if [ -r /etc/bash.bashrc ]; then
  . /etc/bash.bashrc
fi
export TERM="${TERM:-xterm-256color}"
export COLORTERM="${COLORTERM:-truecolor}"
export PAGER=cat
export GIT_PAGER=cat
export LESS=FRX
export CLICOLOR=1
export CLICOLOR_FORCE=1
export FORCE_COLOR=1
if command -v dircolors >/dev/null 2>&1; then
  eval "$(dircolors -b)"
fi
alias ls='ls --color=auto'
alias ll='ls -lah --color=auto'
alias grep='grep --color=auto'
alias egrep='egrep --color=auto'
alias fgrep='fgrep --color=auto'
__sentinel_prompt_command() {
  local __rc=$?
  printf '\033]133;D;%s\033\\' "$__rc"
  set +e
  set +u
  set +o pipefail 2>/dev/null || true
  return $__rc
}
PROMPT_COMMAND='__sentinel_prompt_command'
PS1='\[\033]133;A\033\\\]\[\033[38;5;81m\]\u@\h\[\033[0m\]:\[\033[38;5;220m\]\w\[\033[0m\]\$ \[\033]133;B\033\\\]'
"""


def validate_terminal_id(terminal_id: str) -> str:
    if not TERMINAL_ID_PATTERN.fullmatch(terminal_id):
        raise RuntimeWorkspaceError(
            "terminal id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
        )
    return terminal_id


def tmux_socket_path(terminal_id: str) -> str:
    terminal_id = validate_terminal_id(terminal_id)
    return (PurePosixPath("/state/tmux") / f"{terminal_id}.sock").as_posix()


def tmux_host_socket_path(session_id: str, *, terminal_id: str = "0", root: str | None = None) -> str:
    paths = workspace_paths(session_id, root=root)
    terminal_id = validate_terminal_id(terminal_id)
    return (PurePosixPath(paths.tmux) / f"{terminal_id}.sock").as_posix()


def tmux_session_name(terminal_id: str) -> str:
    terminal_id = validate_terminal_id(terminal_id)
    return f"sentinel_{terminal_id}"


def tmux_host_log_path(session_id: str, *, terminal_id: str = "0", root: str | None = None) -> str:
    paths = workspace_paths(session_id, root=root)
    terminal_id = validate_terminal_id(terminal_id)
    return (PurePosixPath(paths.tmux) / f"{terminal_id}.log").as_posix()


def build_open_tmux_script(
    session_id: str,
    *,
    terminal_id: str = "0",
    root: str | None = None,
) -> tuple[str, list[str]]:
    paths = workspace_paths(session_id, root=root)
    terminal_id = validate_terminal_id(terminal_id)
    socket = tmux_socket_path(terminal_id)
    host_socket = tmux_host_socket_path(session_id, terminal_id=terminal_id, root=root)
    name = tmux_session_name(terminal_id)
    rcfile = (PurePosixPath("/state/tmux") / f"{terminal_id}.bashrc").as_posix()
    pane_log = (PurePosixPath("/state/tmux") / f"{terminal_id}.log").as_posix()
    log_path = (PurePosixPath(paths.logs) / f"tmux-{terminal_id}.log").as_posix()
    inner = (
        "set -euo pipefail; "
        f"mkdir -p /state/tmux /workspace/.runtime/term/{quote(terminal_id)}; "
        f"cat > {quote(rcfile)} <<'SENTINEL_BASHRC'\n{SENTINEL_BASHRC}\nSENTINEL_BASHRC\n"
        f"touch {quote(pane_log)}; "
        f"tmux -S {quote(socket)} has-session -t {quote(name)} 2>/dev/null && exit 0; "
        f"tmux -S {quote(socket)} new-session -d -s {quote(name)} "
        f"-x {TMUX_COLS} -y {TMUX_ROWS} -c /workspace "
        "-e TERM=xterm-256color "
        "-e COLORTERM=truecolor "
        "-e PAGER=cat "
        "-e GIT_PAGER=cat "
        "-e LESS=FRX "
        "-e HOME=/state/home "
        f"{quote(f'bash --rcfile {quote(rcfile)} -i')}; "
        f"tmux -S {quote(socket)} set-option -t {quote(name)} remain-on-exit on; "
        f"tmux -S {quote(socket)} set-option -t {quote(name)} history-limit {TMUX_HISTORY_LIMIT}; "
        f"tmux -S {quote(socket)} set-option -t {quote(name)} mouse on; "
        f"tmux -S {quote(socket)} pipe-pane -t {quote(name)} -o {quote(f'cat >> {pane_log}')}"
    )
    script = load_remote_command("tmux/open.sh")
    script = script.replace("__REQUIRE_WORKSPACE__", build_require_workspace_script(paths).strip())
    script = script.replace("__RUNTIME_DIR__", quote(paths.runtime))
    script = script.replace("__LOGS_DIR__", quote(paths.logs))
    script = script.replace("__HOST_SOCKET__", quote(host_socket))
    script = script.replace("__TMUX_NAME__", quote(name))
    script = script.replace("__BWRAP_COMMAND__", build_bubblewrap_command(paths, ["bash", "-lc", inner]))
    script = script.replace("__LOG_PATH__", quote(log_path))
    return script, []


def build_close_tmux_script(
    session_id: str,
    *,
    terminal_id: str = "0",
    root: str | None = None,
) -> tuple[str, list[str]]:
    paths = workspace_paths(session_id, root=root)
    terminal_id = validate_terminal_id(terminal_id)
    host_socket = tmux_host_socket_path(session_id, terminal_id=terminal_id, root=root)
    name = tmux_session_name(terminal_id)
    script = load_remote_command("tmux/close.sh")
    script = script.replace("__SESSION_ROOT__", quote(paths.session_root))
    script = script.replace("__HOST_SOCKET__", quote(host_socket))
    script = script.replace("__TMUX_NAME__", quote(name))
    return script, []


def build_tmux_status_script(
    session_id: str,
    *,
    terminal_id: str = "0",
    root: str | None = None,
) -> tuple[str, list[str]]:
    paths = workspace_paths(session_id, root=root)
    terminal_id = validate_terminal_id(terminal_id)
    host_socket = tmux_host_socket_path(session_id, terminal_id=terminal_id, root=root)
    name = tmux_session_name(terminal_id)
    script = load_remote_command("tmux/status.sh")
    script = script.replace("__SESSION_ROOT__", quote(paths.session_root))
    script = script.replace("__HOST_SOCKET__", quote(host_socket))
    script = script.replace("__TMUX_NAME__", quote(name))
    return script, []
