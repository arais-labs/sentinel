#!/bin/sh
# Runs in the private builder VM. Successful outputs and failed build trees both
# survive shutdown; only this component's input hash selects a new directory.
set -eu
component=$1
key=$2
root=${SENTINEL_BUILD_CACHE:-/var/cache/sentinel-build}
directory="$root/$component/$key"
export SENTINEL_BUILD_WORK="$directory/work"
export SENTINEL_GRAPHICS_PREFIX="$directory/output"
mkdir -p "$SENTINEL_BUILD_WORK" "$SENTINEL_GRAPHICS_PREFIX" "$SENTINEL_GRAPHICS_BUNDLE"
if [ -f "$directory/complete" ]; then
  printf 'Using cached %s build\n' "$component"
  cat >/dev/null
else
  printf 'Building %s (retaining dependencies and intermediates)\n' "$component"
  if [ -f /etc/alpine-release ]; then
    mkdir -p /var/cache/apk
  fi
  sh -s -- "$SENTINEL_GRAPHICS_PREFIX"
  touch "$directory/complete"
fi
cp -a "$SENTINEL_GRAPHICS_PREFIX/." "$SENTINEL_GRAPHICS_BUNDLE/"
