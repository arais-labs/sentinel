#!/usr/bin/env bash
set -euo pipefail
python3 - "$1" <<'PY'
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REQ = json.loads(sys.argv[1])
HOME = Path(REQ["home"])
RUNTIME = Path(REQ["runtime"])
META = RUNTIME / "desktop.json"
WORKSPACE_XDG_RUNTIME_DIR = RUNTIME / "xdg"
TMP_XDG_RUNTIME_ROOT = Path("/tmp/sentinel-runtime")


def is_managed_xdg_runtime_dir(path: Path) -> bool:
    path = path.resolve(strict=False)
    workspace_path = WORKSPACE_XDG_RUNTIME_DIR.resolve(strict=False)
    tmp_root = TMP_XDG_RUNTIME_ROOT.resolve(strict=False)
    return path == workspace_path or path.is_relative_to(tmp_root)


def remove_xdg_runtime_dir(path: Path | str | None) -> None:
    if not path:
        return
    candidate = Path(path)
    if not is_managed_xdg_runtime_dir(candidate):
        return
    if candidate.exists():
        if candidate.is_symlink() or candidate.is_file():
            candidate.unlink()
        else:
            import shutil

            shutil.rmtree(candidate)

if META.exists():
    try:
        data = json.loads(META.read_text())
        display = int(data.get("display"))
        xdg_runtime_dir = data.get("xdg_runtime_dir")
    except Exception:
        display = None
        xdg_runtime_dir = None
    if display is not None:
        env = os.environ.copy()
        env["HOME"] = str(HOME)
        subprocess.run(["vncserver", "-kill", f":{display}"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    remove_xdg_runtime_dir(xdg_runtime_dir)
    META.unlink(missing_ok=True)

remove_xdg_runtime_dir(WORKSPACE_XDG_RUNTIME_DIR)
PY
