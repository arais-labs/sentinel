#!/usr/bin/env bash
set -euo pipefail

LIVE_ENABLED="${BROWSER_LIVE_VIEW_ENABLED:-true}"
LIVE_ENABLED_LOWER="$(echo "$LIVE_ENABLED" | tr '[:upper:]' '[:lower:]')"

declare -a CHILD_PIDS=()

cleanup() {
  for pid in "${CHILD_PIDS[@]:-}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
  wait || true
}

trap cleanup EXIT INT TERM

if [[ "$LIVE_ENABLED_LOWER" == "true" || "$LIVE_ENABLED_LOWER" == "1" || "$LIVE_ENABLED_LOWER" == "yes" ]]; then
  export DISPLAY="${DISPLAY:-:99}"
  export BROWSER_HEADLESS="${BROWSER_HEADLESS:-false}"

  BROWSER_LIVE_RESOLUTION="${BROWSER_LIVE_RESOLUTION:-1600x900x24}"
  BROWSER_VNC_PORT="${BROWSER_VNC_PORT:-5900}"
  BROWSER_NOVNC_PORT="${BROWSER_NOVNC_PORT:-6080}"
  BROWSER_NOVNC_BIND_HOST="${BROWSER_NOVNC_BIND_HOST:-0.0.0.0}"
  BROWSER_NOVNC_WEB_ROOT="${BROWSER_NOVNC_WEB_ROOT:-/usr/share/novnc}"
  BROWSER_USER_DATA_DIR="${BROWSER_USER_DATA_DIR:-}"

  if [[ -n "$BROWSER_USER_DATA_DIR" && -L "$BROWSER_USER_DATA_DIR/SingletonLock" ]]; then
    LOCK_TARGET="$(readlink "$BROWSER_USER_DATA_DIR/SingletonLock" || true)"
    LOCK_PID="${LOCK_TARGET##*-}"
    if [[ -n "$LOCK_PID" && "$LOCK_PID" =~ ^[0-9]+$ ]] && [[ ! -d "/proc/$LOCK_PID" ]]; then
      rm -f \
        "$BROWSER_USER_DATA_DIR/SingletonLock" \
        "$BROWSER_USER_DATA_DIR/SingletonSocket" \
        "$BROWSER_USER_DATA_DIR/SingletonCookie"
    fi
  fi

  Xvfb "$DISPLAY" -screen 0 "$BROWSER_LIVE_RESOLUTION" -ac +extension RANDR -nolisten tcp >/tmp/xvfb.log 2>&1 &
  CHILD_PIDS+=($!)
  sleep 1

  fluxbox -display "$DISPLAY" >/tmp/fluxbox.log 2>&1 &
  CHILD_PIDS+=($!)
  sleep 1

  X11VNC_ARGS=(
    -display "$DISPLAY"
    -forever
    -shared
    -rfbport "$BROWSER_VNC_PORT"
    -localhost
    -xkb
  )

  BROWSER_VNC_PASSWORD="${BROWSER_VNC_PASSWORD:-}"
  if [[ -n "$BROWSER_VNC_PASSWORD" ]]; then
    x11vnc -storepasswd "$BROWSER_VNC_PASSWORD" /tmp/x11vnc.pass >/dev/null 2>&1
    X11VNC_ARGS+=(-rfbauth /tmp/x11vnc.pass)
  else
    X11VNC_ARGS+=(-nopw)
  fi

  x11vnc "${X11VNC_ARGS[@]}" >/tmp/x11vnc.log 2>&1 &
  CHILD_PIDS+=($!)
  sleep 1

  websockify --web="$BROWSER_NOVNC_WEB_ROOT" "$BROWSER_NOVNC_BIND_HOST:$BROWSER_NOVNC_PORT" "localhost:$BROWSER_VNC_PORT" >/tmp/websockify.log 2>&1 &
  CHILD_PIDS+=($!)
fi

uvicorn main:app --host 0.0.0.0 --port 8000 &
APP_PID=$!
CHILD_PIDS+=($APP_PID)
wait "$APP_PID"
