#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname -s)" != Darwin ]]; then
  echo 'The current Electron development runtime supports macOS. Backend checks can run with uv on other systems.' >&2
  exit 1
fi
if ! command -v brew >/dev/null 2>&1; then
  echo 'Install Homebrew from https://brew.sh, then run make setup again.' >&2
  exit 1
fi
command -v uv >/dev/null 2>&1 || brew install uv
if ! command -v node >/dev/null 2>&1 || ! node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 12) ? 0 : 1)'; then
  brew install node@22
  export PATH="$(brew --prefix node@22)/bin:$PATH"
fi
uv python install 3.12
sentinel_python="$(uv python find --managed-python 3.12)"
uv sync --project apps/backend/sentinel --python "$sentinel_python" --managed-python --locked --all-groups
apps/backend/sentinel/.venv/bin/python -c 'import sqlite3, sqlite_vec; db = sqlite3.connect(":memory:"); db.enable_load_extension(True); sqlite_vec.load(db); db.enable_load_extension(False); db.close()'
npm --prefix apps/frontend/sentinel ci
npm --prefix apps/desktop/sentinel ci
node apps/desktop/sentinel/node_modules/electron/install.js
bash scripts/install-git-hooks.sh
printf '\nReady. Run make dev to open Sentinel.\n'
