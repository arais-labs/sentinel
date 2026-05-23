#!/usr/bin/env bash
set -euo pipefail

python3 - "$1" <<'PY'
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

request = json.loads(sys.argv[1])
job_dir = Path(request["job_dir"])
job_dir.mkdir(parents=True, exist_ok=True)

run_path = job_dir / "run.sh"
config_path = job_dir / "config.json"
manifest_path = job_dir / "manifest.json"

run_path.write_text(str(request["runner"]), encoding="utf-8")
os.chmod(run_path, 0o700)
config_path.write_text(json.dumps(request["config"], ensure_ascii=True), encoding="utf-8")
os.chmod(config_path, 0o600)
manifest_path.write_text(json.dumps(request["manifest"], ensure_ascii=True), encoding="utf-8")
os.chmod(manifest_path, 0o600)
PY
