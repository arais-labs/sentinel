#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "This directory is not a git repository yet."
  echo "Initialize or clone the repo first, then re-run this script."
  exit 1
fi

chmod +x .githooks/pre-commit .githooks/commit-msg
git config core.hooksPath .githooks

echo "Installed git hooks from .githooks/"
echo "Active hooks path: $(git config --get core.hooksPath)"
