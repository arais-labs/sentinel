#!/bin/sh
# Qualification recipe only. Run in a dedicated Alpine 3.24 component builder;
# nothing calls this from production packaging. Keep work, source and ccache.
set -eu
JOBS=${SENTINEL_BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN)}
case "$JOBS" in ''|*[!0-9]*|0) echo 'Invalid positive job count' >&2; exit 2 ;; esac
[ "$JOBS" -gt 0 ] || { echo 'Invalid positive job count' >&2; exit 2; }
output=$1
case "$output" in /*) ;; *) echo 'Output must be absolute' >&2; exit 1 ;; esac
inputs=${SENTINEL_KLIPPER_INPUTS:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)}
patch=${SENTINEL_KLIPPER_PATCH:-$inputs/../../patches/klipper/0001-startup-restore-only-empty.patch}
work=${SENTINEL_BUILD_WORK:?Set a persistent distro-specific work root}
case "$work" in /*) ;; *) echo 'Work directory must be absolute' >&2; exit 1 ;; esac
. /etc/os-release
test "$ID" = alpine
case "$VERSION_ID" in 3.24.*) ;; *) exit 1 ;; esac
test "$(apk --print-arch)" = aarch64
indexes_updated=false
update_indexes() {
  if [ "$indexes_updated" = false ]; then
    apk update
    indexes_updated=true
  fi
}
if ! apk info -e alpine-sdk curl python3 openssl ccache binutils runuser xvfb-run xvfb xauth dbus >/dev/null 2>&1; then
  update_indexes
  apk add --virtual .sentinel-klipper-build alpine-sdk curl python3 openssl ccache binutils runuser xvfb-run xvfb xauth dbus
fi
SENTINEL_BUILD_TEST_USER=${SENTINEL_BUILD_TEST_USER:-sentinel-build}
if [ "$SENTINEL_BUILD_TEST_USER" = sentinel-build ] && ! id sentinel-build >/dev/null 2>&1; then
  adduser -D sentinel-build
fi
export SENTINEL_BUILD_TEST_USER
if [ ! -e /tmp/.X11-unix ] && [ ! -L /tmp/.X11-unix ]; then
  mkdir -m 1777 /tmp/.X11-unix
fi
export JOBS
exec python3 "$inputs/build-package-alpine.py" "$inputs" "$patch" "$work" "$output"
