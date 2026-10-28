#!/bin/sh
set -eu
project=$1
control=$2
session=$3
manifest=$4
shift 4

# Project permissions belong to the user. Never chmod their directory.
mkdir -p -- "$project"
umask 077
mkdir -p -- "$control" "$session" "$@"
for directory in "$control" "$session" "$@"; do
  if [ -L "$directory" ]; then
    echo "Refusing symlinked Sentinel state directory" >&2
    exit 1
  fi
done
printf '%s' "$manifest" > "$session/manifest.json.tmp"
mv -- "$session/manifest.json.tmp" "$session/manifest.json"
