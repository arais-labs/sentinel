from __future__ import annotations

import re
from pathlib import PurePosixPath
from shlex import quote

from app.services.runtime.darwin_seatbelt import (
    build_append_seatbelt_tool_roots_script,
    build_seatbelt_command,
    build_seatbelt_profile,
)
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
        raise RuntimeWorkspaceError("terminal id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
    return terminal_id


def tmux_socket_path(terminal_id: str) -> str:
    terminal_id = validate_terminal_id(terminal_id)
    return (PurePosixPath("/state/tmux") / f"{terminal_id}.sock").as_posix()


def tmux_host_socket_path(
    session_id: str, *, terminal_id: str = "0", root: str | None = None
) -> str:
    paths = workspace_paths(session_id, root=root)
    terminal_id = validate_terminal_id(terminal_id)
    return (PurePosixPath(paths.tmux) / f"{terminal_id}.sock").as_posix()


def tmux_session_name(terminal_id: str) -> str:
    terminal_id = validate_terminal_id(terminal_id)
    return f"sentinel_{terminal_id}"


def build_host_tmux_command(args: list[str], *, os_name: str = "linux") -> str:
    if os_name != "darwin":
        return " ".join(["tmux", *(quote(arg) for arg in args)])
    inner = "\n".join(
        [
            "set -e",
            "sentinel_login_shell=$(dscl . -read \"/Users/$(whoami)\" UserShell 2>/dev/null | sed 's/^UserShell: //' || true)",
            'if [ -z "$sentinel_login_shell" ] || [ ! -x "$sentinel_login_shell" ]; then',
            "  sentinel_login_shell=${SHELL:-}",
            "fi",
            "sentinel_tmux=$(command -v tmux 2>/dev/null || true)",
            'if [ -z "$sentinel_tmux" ] && [ -n "$sentinel_login_shell" ] && [ -x "$sentinel_login_shell" ]; then',
            "  sentinel_tmux=$(\"$sentinel_login_shell\" -lc 'command -v tmux' 2>/dev/null | awk 'NF { value=$0 } END { print value }' || true)",
            "fi",
            'if [ -z "$sentinel_tmux" ]; then',
            "  echo \"Required executable 'tmux' is not available in the runtime PATH.\" >&2",
            "  exit 127",
            "fi",
            'exec "$sentinel_tmux" ' + " ".join(quote(arg) for arg in args),
        ]
    )
    return f"/bin/sh -lc {quote(inner)}"


def build_resolve_host_tmux_script(*, os_name: str = "linux") -> str:
    if os_name != "darwin":
        return "sentinel_tmux=tmux"
    return "\n".join(
        [
            "sentinel_login_shell=$(dscl . -read \"/Users/$(whoami)\" UserShell 2>/dev/null | sed 's/^UserShell: //' || true)",
            'if [ -z "$sentinel_login_shell" ] || [ ! -x "$sentinel_login_shell" ]; then',
            "  sentinel_login_shell=${SHELL:-}",
            "fi",
            "sentinel_tmux=$(command -v tmux 2>/dev/null || true)",
            'if [ -z "$sentinel_tmux" ] && [ -n "$sentinel_login_shell" ] && [ -x "$sentinel_login_shell" ]; then',
            "  sentinel_tmux=$(\"$sentinel_login_shell\" -lc 'command -v tmux' 2>/dev/null | awk 'NF { value=$0 } END { print value }' || true)",
            "fi",
            'if [ -z "$sentinel_tmux" ]; then',
            "  echo \"Required executable 'tmux' is not available in the runtime PATH.\" >&2",
            "  exit 127",
            "fi",
        ]
    )


def tmux_host_log_path(session_id: str, *, terminal_id: str = "0", root: str | None = None) -> str:
    paths = workspace_paths(session_id, root=root)
    terminal_id = validate_terminal_id(terminal_id)
    return (PurePosixPath(paths.tmux) / f"{terminal_id}.log").as_posix()


def build_open_tmux_script(
    session_id: str,
    *,
    terminal_id: str = "0",
    root: str | None = None,
    os_name: str = "linux",
    sandbox: str = "bubblewrap",
) -> tuple[str, list[str]]:
    paths = workspace_paths(session_id, root=root)
    terminal_id = validate_terminal_id(terminal_id)
    host_socket = tmux_host_socket_path(session_id, terminal_id=terminal_id, root=root)
    name = tmux_session_name(terminal_id)
    log_path = (PurePosixPath(paths.logs) / f"tmux-{terminal_id}.log").as_posix()
    if os_name == "darwin" and sandbox == "seatbelt":
        rcfile = (PurePosixPath(paths.tmux) / f"{terminal_id}.bashrc").as_posix()
        pane_log = (PurePosixPath(paths.tmux) / f"{terminal_id}.log").as_posix()
        profile_path = (PurePosixPath(paths.runtime) / f"seatbelt-{terminal_id}.sb").as_posix()
        require_workspace = build_require_workspace_script(paths).strip()
        prelude = (
            f"mkdir -p {quote(paths.runtime)} {quote(paths.logs)}\n"
            f"cat > {quote(profile_path)} <<'SENTINEL_SEATBELT'\n"
            f"{build_seatbelt_profile(paths)}\n"
            "SENTINEL_SEATBELT\n"
            f"{build_append_seatbelt_tool_roots_script(paths, profile_path)}"
        )
        socket = "../state/tmux/" + f"{terminal_id}.sock"
        workspace = "."
        state_home = "../state/home"
        tmux_dir = "../state/tmux"
        runtime_dir = "../state/runtime"
        tmp_dir = "../tmp"
        sandbox_command = lambda command: build_seatbelt_command(
            paths,
            profile_path,
            [
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-lc",
                f"cd {quote(workspace)} && {command}",
            ],
        )
    else:
        socket = tmux_socket_path(terminal_id)
        rcfile = (PurePosixPath("/state/tmux") / f"{terminal_id}.bashrc").as_posix()
        pane_log = (PurePosixPath("/state/tmux") / f"{terminal_id}.log").as_posix()
        require_workspace = build_require_workspace_script(paths).strip()
        prelude = ""
        workspace = "/workspace"
        state_home = "/state/home"
        tmux_dir = "/state/tmux"
        runtime_dir = "/state/runtime"
        tmp_dir = "/tmp/sentinel"
        sandbox_command = lambda command: build_bubblewrap_command(paths, ["bash", "-lc", command])
    inner = (
        "set -euo pipefail; "
        f"mkdir -p {quote(tmux_dir)} {quote(workspace)}/.runtime/term/{quote(terminal_id)}; "
        f"cat > {quote(rcfile)} <<'SENTINEL_BASHRC'\n{SENTINEL_BASHRC}\nSENTINEL_BASHRC\n"
        f"touch {quote(pane_log)}; "
        f"tmux -f /dev/null -S {quote(socket)} has-session -t {quote(name)} 2>/dev/null && exit 0; "
        f"tmux -f /dev/null -S {quote(socket)} new-session -d -s {quote(name)} "
        f"-x {TMUX_COLS} -y {TMUX_ROWS} -c {quote(workspace)} "
        "-e TERM=xterm-256color "
        "-e COLORTERM=truecolor "
        "-e BASH_SILENCE_DEPRECATION_WARNING=1 "
        "-e PAGER=cat "
        "-e GIT_PAGER=cat "
        "-e LESS=FRX "
        f"-e HOME={quote(state_home)} "
        f"-e TMPDIR={quote(tmp_dir)} "
        f"-e XDG_RUNTIME_DIR={quote(runtime_dir)} "
        f"{quote(f'BASH_SILENCE_DEPRECATION_WARNING=1 bash --rcfile {quote(rcfile)} -i')}; "
        f"tmux -f /dev/null -S {quote(socket)} set-option -t {quote(name)} remain-on-exit on; "
        f"tmux -f /dev/null -S {quote(socket)} set-option -t {quote(name)} history-limit {TMUX_HISTORY_LIMIT}; "
        f"tmux -f /dev/null -S {quote(socket)} set-option -t {quote(name)} mouse on; "
        f"tmux -f /dev/null -S {quote(socket)} pipe-pane -t {quote(name)} -o {quote(f'cat >> {pane_log}')}"
    )
    script = load_remote_command("common/tmux/open.sh")
    script = script.replace(
        "__REQUIRE_WORKSPACE__", "\n".join(item for item in [require_workspace, prelude] if item)
    )
    script = script.replace("__RUNTIME_DIR__", quote(paths.runtime))
    script = script.replace("__LOGS_DIR__", quote(paths.logs))
    script = script.replace(
        "__RESOLVE_HOST_TMUX__", build_resolve_host_tmux_script(os_name=os_name)
    )
    script = script.replace("__HOST_SOCKET__", quote(host_socket))
    script = script.replace("__TMUX_NAME__", quote(name))
    script = script.replace("__BWRAP_COMMAND__", sandbox_command(inner))
    script = script.replace("__LOG_PATH__", quote(log_path))
    return script, []


def build_close_tmux_script(
    session_id: str,
    *,
    terminal_id: str = "0",
    root: str | None = None,
    os_name: str = "linux",
) -> tuple[str, list[str]]:
    paths = workspace_paths(session_id, root=root)
    terminal_id = validate_terminal_id(terminal_id)
    host_socket = tmux_host_socket_path(session_id, terminal_id=terminal_id, root=root)
    name = tmux_session_name(terminal_id)
    script = load_remote_command("common/tmux/close.sh")
    script = script.replace(
        "__RESOLVE_HOST_TMUX__", build_resolve_host_tmux_script(os_name=os_name)
    )
    script = script.replace("__SESSION_ROOT__", quote(paths.session_root))
    script = script.replace("__HOST_SOCKET__", quote(host_socket))
    script = script.replace("__TMUX_NAME__", quote(name))
    return script, []


def build_tmux_status_script(
    session_id: str,
    *,
    terminal_id: str = "0",
    root: str | None = None,
    os_name: str = "linux",
) -> tuple[str, list[str]]:
    paths = workspace_paths(session_id, root=root)
    terminal_id = validate_terminal_id(terminal_id)
    host_socket = tmux_host_socket_path(session_id, terminal_id=terminal_id, root=root)
    name = tmux_session_name(terminal_id)
    script = load_remote_command("common/tmux/status.sh")
    script = script.replace(
        "__RESOLVE_HOST_TMUX__", build_resolve_host_tmux_script(os_name=os_name)
    )
    script = script.replace("__SESSION_ROOT__", quote(paths.session_root))
    script = script.replace("__HOST_SOCKET__", quote(host_socket))
    script = script.replace("__TMUX_NAME__", quote(name))
    return script, []
