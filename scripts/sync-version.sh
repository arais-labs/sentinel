#!/usr/bin/env bash
# Single source of truth for the app version is the root VERSION file.
# This script stamps that number into every derived file. Those files are
# OUTPUTS — never hand-edit them; bump VERSION and run this instead.
#
#   scripts/sync-version.sh            # write VERSION into all derived files
#   scripts/sync-version.sh --check    # write nothing; fail if any file drifts
#
# The --check mode is the drift guard used by the pre-commit hook and CI.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VERSION="$(tr -d '[:space:]' < VERSION)"
if [[ -z "$VERSION" ]]; then
  echo "sync-version: root VERSION file is empty." >&2
  exit 1
fi
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.]+)?$ ]]; then
  echo "sync-version: '$VERSION' is not a valid version (expected MAJOR.MINOR.PATCH)." >&2
  exit 1
fi

CHECK=0
if [[ "${1:-}" == "--check" ]]; then
  CHECK=1
fi

# Each entry: file path + a perl substitution that rewrites only the version
# token. \x27 is a literal single quote (for the TS file); $ENV{VERSION} carries
# the value in without bash-quoting headaches.
PYPROJECT='apps/backend/sentinel/pyproject.toml'
FRONTEND_PKG='apps/frontend/sentinel/package.json'
DESKTOP_PKG='apps/desktop/sentinel/package.json'
ENV_TS='apps/frontend/sentinel/src/lib/env.ts'

SUB_PYPROJECT='s/^(version\s*=\s*")[^"]*(")/$1$ENV{VERSION}$2/m'
SUB_PKG='s/("version"\s*:\s*")[^"]*(")/$1$ENV{VERSION}$2/'
SUB_ENV='s/(APP_VERSION\s*=\s*\x27)[^\x27]*(\x27)/$1$ENV{VERSION}$2/'

drift=0

apply() {
  local file="$1" sub="$2"
  if [[ ! -f "$file" ]]; then
    echo "sync-version: missing derived file '$file'." >&2
    exit 1
  fi
  if [[ "$CHECK" == 1 ]]; then
    local got expected
    got="$(cat "$file")"
    expected="$(VERSION="$VERSION" perl -0pe "$sub" "$file")"
    if [[ "$got" != "$expected" ]]; then
      echo "  drift: $file"
      drift=1
    fi
  else
    VERSION="$VERSION" perl -0pi -e "$sub" "$file"
  fi
}

apply "$PYPROJECT"   "$SUB_PYPROJECT"
apply "$FRONTEND_PKG" "$SUB_PKG"
apply "$DESKTOP_PKG"  "$SUB_PKG"
apply "$ENV_TS"       "$SUB_ENV"

if [[ "$CHECK" == 1 ]]; then
  if [[ "$drift" == 1 ]]; then
    echo
    echo "Version files are out of sync with VERSION ($VERSION)." >&2
    echo "Run scripts/sync-version.sh and re-stage the changes." >&2
    exit 1
  fi
  echo "Version files are in sync ($VERSION)."
else
  echo "Synced all derived files to version $VERSION."
fi
