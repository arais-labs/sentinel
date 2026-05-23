#!/usr/bin/env bash
set -euo pipefail

if [ ! -d __SESSION_ROOT__ ]; then
  exit 0
fi
__RESOLVE_HOST_TMUX__

"$sentinel_tmux" -f /dev/null -S __HOST_SOCKET__ kill-session -t __TMUX_NAME__ 2>/dev/null || true

for _ in $(seq 1 50); do
  if ! "$sentinel_tmux" -f /dev/null -S __HOST_SOCKET__ has-session -t __TMUX_NAME__ 2>/dev/null; then
    rm -f __HOST_SOCKET__
    exit 0
  fi
  sleep 0.1
done

exit 1
