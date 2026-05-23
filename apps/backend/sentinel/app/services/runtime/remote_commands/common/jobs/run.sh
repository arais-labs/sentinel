#!/usr/bin/env bash
set +e

job_dir="$(cd "$(dirname "$0")" && pwd)"
config_path="${SENTINEL_JOB_CONFIG:-${job_dir}/config.json}"

__sentinel_write_done() {
  local rc=$?
  SENTINEL_JOB_RC="$rc" SENTINEL_JOB_CONFIG="$config_path" python3 - <<'PY'
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

config = json.loads(Path(os.environ["SENTINEL_JOB_CONFIG"]).read_text(encoding="utf-8"))
rc = int(os.environ.get("SENTINEL_JOB_RC") or "1")
done_path = Path(config["done_path"])
payload = {
    "id": config["job_id"],
    "status": "completed" if rc == 0 else "failed",
    "returncode": rc,
    "ended_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
}
tmp = done_path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
tmp.replace(done_path)
PY
}

trap __sentinel_write_done EXIT

eval "$(
  python3 - "$config_path" <<'PY'
from __future__ import annotations

import json
import shlex
import sys

config = json.loads(open(sys.argv[1], encoding="utf-8").read())
cwd = config.get("cwd")
if cwd:
    print("cd " + shlex.quote(str(cwd)) + " || exit $?")
for key, value in (config.get("env") or {}).items():
    if key:
        print("export " + key + "=" + shlex.quote(str(value)))
PY
)"

command="$(
  python3 - "$config_path" <<'PY'
from __future__ import annotations

import json
import sys

print(json.loads(open(sys.argv[1], encoding="utf-8").read())["command"])
PY
)"

bash -lc "$command"
