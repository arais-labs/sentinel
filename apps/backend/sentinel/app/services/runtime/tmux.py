from __future__ import annotations

from app.services.runtime.workspace import WorkspaceLocation

from pathlib import PurePosixPath
from shlex import quote

from app.services.runtime.guest_commands import load_guest_command
from app.services.runtime.workspace import workspace_paths

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
# Color interactive output without leaking escape sequences into pipes/files.
# Honor the user's opt-out and existing directory-color customization.
if [ -z "${NO_COLOR:-}" ]; then
  export CLICOLOR="${CLICOLOR:-1}"
  if [ -z "${LS_COLORS+x}" ] && command -v dircolors >/dev/null 2>&1; then
    eval "$(dircolors -b)"
  fi
  if [ "$(uname -s)" = Darwin ]; then alias ls='ls -G'; else alias ls='ls --color=auto'; fi
  alias grep='grep --color=auto'
  alias egrep='egrep --color=auto'
  alias fgrep='fgrep --color=auto'
  alias git='git -c color.ui=auto'
else
  unset CLICOLOR
  if [ "$(uname -s)" = Darwin ]; then alias ls='ls'; else alias ls='ls --color=never'; fi
  alias grep='grep --color=never'
  alias egrep='egrep --color=never'
  alias fgrep='fgrep --color=never'
  alias git='git -c color.ui=false'
fi
alias ll='ls -lah'
__sentinel_armed=0
__sentinel_preexec() {
  # OSC 133;C (output start), emitted once per command so capture can skip the
  # echoed input. functrace lets it fire inside the subshell wrapper.
  case "$BASH_COMMAND" in __sentinel_prompt_command*) return 0 ;; esac
  local __f
  for __f in "${FUNCNAME[@]}"; do
    [ "$__f" = "__sentinel_prompt_command" ] && return 0
  done
  [ "$__sentinel_armed" = 1 ] || return 0
  __sentinel_armed=0
  printf '\033]133;C\033\\'
}
trap '__sentinel_preexec' DEBUG
set -o functrace
__sentinel_prompt_command() {
  local __rc=$?
  printf '\033]133;D;%s\033\\' "$__rc"
  set +e
  set +u
  set +o pipefail 2>/dev/null || true
  set -o functrace 2>/dev/null || true
  __sentinel_armed=1
  return $__rc
}
PROMPT_COMMAND='__sentinel_prompt_command'
PROMPT_DIRTRIM=2
PS1='\[\033]133;A\033\\\]\[\033[34m\]\W\[\033[0m\] ❯ \[\033]133;B\033\\\]'
"""


def tmux_host_socket_path(session_id: str, *, root: WorkspaceLocation) -> str:
    paths = workspace_paths(session_id, root=root)
    return (PurePosixPath(paths.tmux) / "session.sock").as_posix()


def _short_socket(socket: str) -> tuple[str, str]:
    # UNIX socket limits apply to the address passed to connect(), not the
    # filesystem path. Connect from the socket directory for long workspace paths.
    if len(socket.encode()) > 100:
        path = PurePosixPath(socket)
        return f"cd {quote(str(path.parent))}\n", f"./{path.name}"
    return "", socket


def build_host_tmux_command(args: list[str], *, os_name: str = "linux") -> str:
    # The terminal and metadata protocol are UTF-8 even when the container has
    # no locale configured. Otherwise tmux sanitizes its output for ASCII.
    args = ["-u", *args]
    prefix = ""
    if "-S" in args:
        args = list(args)
        index = args.index("-S") + 1
        prefix, args[index] = _short_socket(args[index])
    if os_name != "darwin":
        return prefix + " ".join(["tmux", *(quote(arg) for arg in args)])
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
            prefix + 'exec "$sentinel_tmux" ' + " ".join(quote(arg) for arg in args),
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


# Chunk size/delay for feeding input into the pane without overrunning the tty.
PANE_FEED_CHUNK_BYTES = 512
PANE_FEED_CHUNK_DELAY = "0.03"


def build_pane_feed_script(socket: str, name: str, *, os_name: str = "linux") -> str:
    """Paste ``$1`` directly to the pane's PTY, bypassing copy-mode bindings."""
    prefix, socket = _short_socket(socket)
    return "\n".join(
        [
            prefix + build_resolve_host_tmux_script(os_name=os_name),
            'sentinel_cmd="$1"',
            'sentinel_buffer="sentinel-input-$$"',
            # tmux 3.7 sanitizes controls unless -S is supplied. Older releases
            # already paste raw bytes and do not recognize that flag.
            'sentinel_paste_raw=""',
            f'if "$sentinel_tmux" -S {quote(socket)} list-commands | '
            "grep -q '^paste-buffer .*\\[-[^]]*S'; then sentinel_paste_raw=-S; fi",
            "sentinel_len=${#sentinel_cmd}",
            "sentinel_i=0",
            'while [ "$sentinel_i" -lt "$sentinel_len" ]; do',
            f'  printf %s "${{sentinel_cmd:$sentinel_i:{PANE_FEED_CHUNK_BYTES}}}" | '
            f'"$sentinel_tmux" -S {quote(socket)} load-buffer -b "$sentinel_buffer" - || exit 1',
            f'  "$sentinel_tmux" -S {quote(socket)} paste-buffer -d -r $sentinel_paste_raw '
            f'-b "$sentinel_buffer" -t {quote(name)} || {{ '
            f'"$sentinel_tmux" -S {quote(socket)} delete-buffer -b "$sentinel_buffer"; exit 1; }}',
            f"  sentinel_i=$((sentinel_i + {PANE_FEED_CHUNK_BYTES}))",
            f"  sleep {PANE_FEED_CHUNK_DELAY}",
            "done",
        ]
    )


def build_workspace_command(paths, command: list[str], *, os_name: str) -> str:
    return " ".join(quote(arg) for arg in command)


def build_workspace_shell(paths, *, os_name: str) -> str:
    # Stream public shell integration inside the sandbox; no controller files are mounted.
    init = f"exec /bin/bash --rcfile <(printf %s {quote(SENTINEL_BASHRC)}) -i"
    return build_workspace_command(
        paths, ["/bin/bash", "--noprofile", "--norc", "-c", init], os_name=os_name
    )


def build_open_tmux_script(
    session_id: str,
    *,
    root: WorkspaceLocation,
    os_name: str = "linux",
    sandbox: str = "container",
) -> tuple[str, list[str]]:
    paths = workspace_paths(session_id, root=root)
    host_socket = tmux_host_socket_path(session_id, root=root)
    socket_prefix, host_socket = _short_socket(host_socket)
    name = "sentinel"
    log_path = (PurePosixPath(paths.logs) / "tmux.log").as_posix()
    workspace = paths.workspace
    tmux_dir = paths.tmux
    require_workspace = f"test -d {quote(paths.workspace)} || exit 1"
    prelude = ""
    shell_command = build_workspace_shell(paths, os_name=os_name)
    socket = "./session.sock"
    # Passed through the runtime script's stdin, never installed as a tmux file.
    config = "\n".join(
        [
            "set -g default-shell /bin/bash",
            f"set -g default-command {quote(shell_command)}",
            f"set -g history-limit {TMUX_HISTORY_LIMIT}",
            "set -g remain-on-exit failed",
            "set -g remain-on-exit-format 'Shell exited unexpectedly (status #{pane_dead_status})'",
            "set -g mouse on",
            "set -s set-clipboard external",
            "set -g status on",
            # ANSI colors follow the viewing client's live Sentinel theme.
            "set -g status-style 'bg=black,fg=white'",
            "set -g status-left '  SENTINEL   '",
            "set -g status-left-length 16",
            "set -g status-justify left",
            "set -g window-status-format '  #W  '",
            "set -g window-status-current-format '  #W  '",
            "set -g window-status-separator ' '",
            "set -g automatic-rename off",
            "set -g status-left-style 'fg=blue,bold'",
            "set -g status-right '  Ctrl-b ?  Help  '",
            "set -g window-status-current-style 'bg=black,fg=blue,bold'",
            "set -g pane-border-style 'fg=brightblack'",
            "set -g pane-active-border-style 'fg=blue'",
            "set -g pane-border-status top",
            "set -g pane-border-format ' #{?#{||:#{==:#{pane_title},#{host}},#{==:#{pane_title},}},,#{pane_title} · }#{pane_current_command} '",
        ]
    )
    inner = (
        "set -euo pipefail; "
        f"mkdir -p {quote(tmux_dir)}; "
        f"cd {quote(tmux_dir)}; "
        f"tmux -f /dev/null -S {quote(socket)} has-session -t {quote(name)} 2>/dev/null && exit 0; "
        f"tmux -f /dev/null -S {quote(socket)} new-session -d -s {quote(name)} -n main "
        f"-x {TMUX_COLS} -y {TMUX_ROWS} -c {quote(workspace)} {quote(shell_command)}"
    )
    script = load_guest_command("common/tmux/open.sh")
    script = script.replace(
        "__REQUIRE_WORKSPACE__", "\n".join(item for item in [require_workspace, prelude] if item)
    )
    script = script.replace("__RUNTIME_DIR__", quote(paths.runtime))
    script = script.replace("__LOGS_DIR__", quote(paths.logs))
    script = script.replace(
        "__RESOLVE_HOST_TMUX__", socket_prefix + build_resolve_host_tmux_script(os_name=os_name)
    )
    script = script.replace("__HOST_SOCKET__", quote(host_socket))
    script = script.replace("__TMUX_NAME__", quote(name))
    script = script.replace("__TMUX_CONFIG__", config)
    script = script.replace(
        "__CONTROLLER_COMMAND__", "/bin/bash --noprofile --norc -c " + quote(inner)
    )
    script = script.replace("__LOG_PATH__", quote(log_path))
    return script, []
