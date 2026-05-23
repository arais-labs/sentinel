#!/usr/bin/env bash
set -euo pipefail

if [ ! -d __SESSION_ROOT__ ]; then
  echo missing
  exit 0
fi
__RESOLVE_HOST_TMUX__

"$sentinel_tmux" -f /dev/null -S __HOST_SOCKET__ has-session -t __TMUX_NAME__ 2>/dev/null && echo running || echo stopped
