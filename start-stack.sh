#!/usr/bin/env bash
set -euo pipefail

echo "start-stack.sh is deprecated. Use ./sentinel-cli.sh instead."
exec bash ./sentinel-cli.sh "$@"
