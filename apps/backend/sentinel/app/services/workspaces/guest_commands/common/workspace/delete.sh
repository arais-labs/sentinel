#!/bin/sh
set -eu
control=$1
session=$2
case "$session" in "$control"/*) ;; *) echo "Invalid session state path" >&2; exit 1 ;; esac
[ ! -e "$session" ] && exit 0
[ ! -L "$control" ] && [ ! -L "$session" ] || exit 1
rm -r -- "$session"
