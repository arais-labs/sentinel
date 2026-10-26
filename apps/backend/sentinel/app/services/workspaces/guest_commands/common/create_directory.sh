#!/bin/sh
set -eu
cd -- "$1"
if [ -e "./$2" ] || [ -L "./$2" ]; then exit 17; fi
mkdir -- "./$2"
cd -- "./$2"
pwd -P
