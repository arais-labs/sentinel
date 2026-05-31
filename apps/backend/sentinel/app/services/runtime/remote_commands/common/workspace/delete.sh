#!/usr/bin/env bash
set -euo pipefail

eval "$(
  python3 - "$1" <<'PY'
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

request = json.loads(sys.argv[1])
session_root = Path(request["session_root"])
workspaces_root = Path(request["workspaces_root"])

session_root_resolved = session_root.resolve(strict=False)
workspaces_root_resolved = workspaces_root.resolve(strict=False)
if session_root_resolved != workspaces_root_resolved and workspaces_root_resolved not in session_root_resolved.parents:
    raise SystemExit("session path escaped workspaces root")

print("session_root=" + shlex.quote(str(session_root)))
PY
)"

if [ -L "${session_root}" ]; then
  echo "refusing to delete symlinked session root" >&2
  exit 4
fi
if [ ! -e "${session_root}" ]; then
  exit 0
fi
if rm --help 2>/dev/null | grep -q -- '--one-file-system'; then
  rm -rf --one-file-system -- "${session_root}"
else
  rm -rf -- "${session_root}"
fi
