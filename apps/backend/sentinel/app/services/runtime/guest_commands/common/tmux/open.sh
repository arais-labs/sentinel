#!/usr/bin/env bash
set -euo pipefail
__REQUIRE_WORKSPACE__
mkdir -p __RUNTIME_DIR__ __LOGS_DIR__
__RESOLVE_HOST_TMUX__
if ! "$sentinel_tmux" -f /dev/null -S __HOST_SOCKET__ has-session -t sentinel 2>/dev/null; then
  nohup __CONTROLLER_COMMAND__ >__LOG_PATH__ 2>&1 </dev/null &
  for _ in $(seq 1 50); do
    if "$sentinel_tmux" -S __HOST_SOCKET__ has-session -t sentinel 2>/dev/null; then break; fi
    sleep 0.1
  done
fi
"$sentinel_tmux" -S __HOST_SOCKET__ source-file - <<'SENTINEL_TMUX'
__TMUX_CONFIG__
SENTINEL_TMUX
