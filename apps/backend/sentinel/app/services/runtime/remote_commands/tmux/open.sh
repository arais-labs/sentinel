#!/usr/bin/env bash
set -euo pipefail

__REQUIRE_WORKSPACE__
mkdir -p __RUNTIME_DIR__ __LOGS_DIR__

if tmux -S __HOST_SOCKET__ has-session -t __TMUX_NAME__ 2>/dev/null; then
  exit 0
fi

nohup __BWRAP_COMMAND__ >__LOG_PATH__ 2>&1 </dev/null &

for _ in $(seq 1 50); do
  if tmux -S __HOST_SOCKET__ has-session -t __TMUX_NAME__ 2>/dev/null; then
    exit 0
  fi
  sleep 0.1
done

cat __LOG_PATH__ >&2 || true
exit 1
