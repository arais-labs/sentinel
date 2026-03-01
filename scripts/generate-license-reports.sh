#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPORT_DIR="$ROOT_DIR/reports/licenses"
WORK_DIR="$ROOT_DIR/.tmp-license-venvs"

mkdir -p "$REPORT_DIR"
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

generate_python_report() {
  local name="$1"
  local project_dir="$2"
  local venv_dir="$WORK_DIR/$name"

  python3 -m venv "$venv_dir"
  "$venv_dir/bin/pip" install --quiet --upgrade pip
  "$venv_dir/bin/pip" install --quiet pip-licenses
  "$venv_dir/bin/pip" install --quiet "$project_dir"
  "$venv_dir/bin/pip-licenses" \
    --format=json \
    --with-urls \
    --output-file="$REPORT_DIR/${name}.json"
}

generate_node_report() {
  local name="$1"
  local project_dir="$2"

  (
    cd "$project_dir"
    npm ci --ignore-scripts
    npx --yes license-checker --json --production > "$REPORT_DIR/${name}.json"
  )
}

generate_python_report "sentinel-backend-python-licenses" "$ROOT_DIR/apps/backend/sentinel"
generate_python_report "araios-backend-python-licenses" "$ROOT_DIR/apps/backend/araios"

generate_node_report "sentinel-frontend-node-licenses" "$ROOT_DIR/apps/frontend/sentinel"
generate_node_report "araios-frontend-node-licenses" "$ROOT_DIR/apps/frontend/araios"

rm -rf "$WORK_DIR"

echo "License reports written to $REPORT_DIR"
