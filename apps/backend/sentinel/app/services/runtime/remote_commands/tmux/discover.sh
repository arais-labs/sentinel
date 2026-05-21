#!/usr/bin/env bash
set -euo pipefail

tmux_dir=__TMUX_DIR__
if [ ! -d "${tmux_dir}" ]; then
  exit 0
fi

shopt -s nullglob
for socket in "${tmux_dir}"/*.sock; do
  name="$(basename "${socket}" .sock)"
  if tmux -S "${socket}" has-session -t "sentinel_${name}" 2>/dev/null; then
    printf '%s\n' "${name}"
  fi
done
