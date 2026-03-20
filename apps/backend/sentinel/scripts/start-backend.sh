#!/usr/bin/env bash
set -euo pipefail

UVICORN_RELOAD="${UVICORN_RELOAD:-false}"
UVICORN_RELOAD_LOWER="$(echo "$UVICORN_RELOAD" | tr '[:upper:]' '[:lower:]')"

UVICORN_ARGS=(main:app --host 0.0.0.0 --port 8000)
if [[ "$UVICORN_RELOAD_LOWER" == "true" || "$UVICORN_RELOAD_LOWER" == "1" || "$UVICORN_RELOAD_LOWER" == "yes" ]]; then
  UVICORN_ARGS+=(--reload)
fi

uvicorn "${UVICORN_ARGS[@]}"
