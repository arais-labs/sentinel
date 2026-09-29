#!/bin/sh
# Native Alpine desktop packages are cached separately from the Mesa build.
set -eu
sh "$SENTINEL_GRAPHICS_INPUTS/build-component.sh" labwc "$SENTINEL_LABWC_KEY" < "$SENTINEL_GRAPHICS_INPUTS/build-labwc.sh"
