#!/bin/sh
set -eu
tests=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
desktop=$(CDPATH= cd -- "$tests/../../.." && pwd)
if [ "${1:-}" = --egl-tests ]; then
    gl=$2
    egl=$3
    output=$(dirname -- "$gl")
    # Meson reserves 77 for skipped tests; requested API gates must fail instead.
    trap 'status=$?; if [ "$status" -eq 77 ]; then exit 1; fi' 0
    # Isolate uploads so legitimate GPU-written mappings in the full suite do
    # not hide a regression that reads every upload allocation back each frame.
    if SENTINEL_TEST_UPLOAD_ONLY=1 SENTINEL_STORAGE_REPORT=1 \
      "$gl" "$egl" 2>"$output/upload-only.log"; then
      cat "$output/upload-only.log" >&2
    else
      status=$?
      cat "$output/upload-only.log" >&2
      exit "$status"
    fi
    python3 "$tests/check-storage-report.py" "$output/upload-only.log"
    (unset SENTINEL_TEST_UPLOAD_ONLY SENTINEL_TEST_STORAGE_LIMITS; "$gl" "$egl")
    (unset SENTINEL_TEST_UPLOAD_ONLY SENTINEL_TEST_STORAGE_LIMITS; SENTINEL_TEST_NATIVE_FENCE=1 \
      "$gl" "$egl")
    if [ "${SENTINEL_TEST_STORAGE_LIMITS:-0}" = 1 ]; then
      # Budget capacity requires a fresh screen: Gallium can retain the previous
      # shader's resource bindings until a subsequent draw validates new state.
      (unset SENTINEL_TEST_UPLOAD_ONLY; SENTINEL_TEST_STORAGE_LIMITS=1 \
        "$gl" "$egl")
    fi
    exit 0
fi

output=${SENTINEL_GUEST_TEST_BUILD_DIR:-"$desktop/build/graphics-guest-tests"}
# Host packaging already owns a pinned Meson/Ninja toolchain. Reuse it for
# local checks; Linux guest jobs may instead supply MESON or distro tools.
tools="$desktop/build/graphics-sources/venv/bin"
if [ -z "${MESON:-}" ] && [ -x "$tools/meson" ]; then
  MESON="$tools/meson"
  PATH="$tools:$PATH"
  export PATH
fi
meson=${MESON:-meson}
drm=false
if [ "${SENTINEL_TEST_DRM_PLANES:-0}" = 1 ]; then drm=true; fi
if [ -f "$output/meson-private/coredata.dat" ]; then
  set -- --reconfigure
else
  set --
fi
"$meson" setup "$@" "$output" "$tests" --backend=ninja \
  "-Dmesa_source_dir=${MESA_SOURCE_DIR:-}" \
  "-Ddrm_planes=$drm" "-Degl_library=${SENTINEL_TEST_EGL:-}" \
  "-Dc_args=${CFLAGS:-}" "-Dc_link_args=${CFLAGS:-} ${LDFLAGS:-}"
"$meson" test -C "$output" --print-errorlogs
