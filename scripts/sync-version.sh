#!/usr/bin/env bash
# VERSION owns the app release number; shared libraries keep their own versions.
# Usage: scripts/sync-version.sh [--check [--greater-than REF] | --set MAJOR.MINOR.PATCH]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python3 - "$@" <<'PY'
import argparse
import json
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser(description="Synchronize Sentinel app versions and lock metadata")
mode = parser.add_mutually_exclusive_group()
mode.add_argument("--check", action="store_true")
mode.add_argument("--set", dest="requested", metavar="MAJOR.MINOR.PATCH")
parser.add_argument("--greater-than", metavar="REF", help="require a version newer than Git REF")
args = parser.parse_args()
if args.greater_than and not args.check:
    parser.error("--greater-than requires --check")
version = args.requested if args.requested is not None else Path("VERSION").read_text().strip()
version_pattern = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
if not re.fullmatch(version_pattern, version):
    parser.error("version must be MAJOR.MINOR.PATCH; beta/stable are release channels")
if args.greater_than:
    previous = subprocess.check_output(["git", "show", f"{args.greater_than}:VERSION"], text=True).strip()
    if not re.fullmatch(version_pattern, previous):
        parser.error(f"target ref has an invalid app version: {previous}")
    if tuple(map(int, version.split("."))) <= tuple(map(int, previous.split("."))):
        raise SystemExit(f"App version {version} must be strictly greater than target version {previous}.")

updates = {Path("VERSION"): version + "\n"}

def replace(path, pattern, value):
    path = Path(path)
    text, count = re.subn(pattern, lambda m: m[1] + value + m[2], path.read_text(), flags=re.M)
    if count != 1:
        raise SystemExit(f"Expected exactly one version field in {path}, found {count}")
    updates[path] = text

for folder, name in [("apps/backend/sentinel", "sentinel-backend"), ("apps/tui", "sentinel-tui")]:
    replace(f"{folder}/pyproject.toml", r'(^version\s*=\s*")[^"]*(")', version)
    replace(f"{folder}/uv.lock", rf'(\[\[package\]\]\nname = "{name}"\nversion = ")[^"]*(")', version)

for folder in ["apps/frontend/sentinel", "apps/desktop/sentinel"]:
    for filename in ["package.json", "package-lock.json"]:
        path = Path(folder) / filename
        data = json.loads(path.read_text())
        data["version"] = version
        if filename == "package-lock.json":
            data["packages"][""]["version"] = version
        updates[path] = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
replace("apps/frontend/sentinel/src/lib/env.ts", r"(APP_VERSION\s*=\s*')[^']*(')", version)

changed = [path for path, text in updates.items() if path.read_text() != text]
if args.check:
    if changed:
        raise SystemExit("Version drift: " + ", ".join(map(str, changed)) + "\nRun scripts/sync-version.sh")
    print(f"Version files and lock metadata are in sync ({version}).")
else:
    for path in changed:
        path.write_text(updates[path])
    print(f"Synced Sentinel {version}: {len(changed)} files updated.")
PY
