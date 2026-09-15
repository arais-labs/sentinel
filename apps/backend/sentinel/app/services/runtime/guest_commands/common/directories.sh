#!/bin/sh
set -eu
cd -- "${1:-${HOME:?Machine home directory is not set}}"
[ -r . ] && [ -x . ] || exit 1
printf '%s\0' "$(pwd -P)"
for entry in ./* ./.[!.]* ./..?*; do
    [ -d "$entry" ] || continue
    printf '%s\0' "${entry#./}"
done
