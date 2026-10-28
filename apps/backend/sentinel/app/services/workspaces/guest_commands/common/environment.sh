#!/bin/sh
set -eu

os=$(uname -s)
sandbox=unavailable

printf '%s\0%s\0%s' "$os" "$sandbox" "${HOME:?Machine home directory is not set}"
