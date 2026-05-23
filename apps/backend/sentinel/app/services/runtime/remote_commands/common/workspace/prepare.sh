#!/usr/bin/env bash
set -euo pipefail

python3 - "$1" <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path

request = json.loads(__import__("sys").argv[1])
session_root = Path(request["session_root"])
workspaces_root = Path(request["workspaces_root"])
manifest_path = Path(request["manifest_path"])
directories = [Path(item) for item in request["directories"]]
private_directories = [Path(item) for item in request["private_directories"]]
manifest = request["manifest"]

session_root_resolved = session_root.resolve(strict=False)
workspaces_root_resolved = workspaces_root.resolve(strict=False)
if session_root_resolved != workspaces_root_resolved and workspaces_root_resolved not in session_root_resolved.parents:
    raise SystemExit("session path escaped workspaces root")

for directory in directories:
    directory.mkdir(parents=True, exist_ok=True)
workspaces_root.chmod(0o755)
for directory in private_directories:
    directory.chmod(0o700)

tmp_manifest = manifest_path.with_name(f"{manifest_path.name}.{os.getpid()}.tmp")
try:
    tmp_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    tmp_manifest.chmod(0o600)
    tmp_manifest.replace(manifest_path)
finally:
    try:
        tmp_manifest.unlink()
    except FileNotFoundError:
        pass
PY
