#!/bin/sh
# Alpine's labwc 0.20 regressed stationary pointer focus on Xwayland map.
# Build only the compositor executable, against the distribution's wlroots ABI.
set -eu
prefix=$1
inputs=$SENTINEL_GRAPHICS_INPUTS
apk add --virtual .sentinel-labwc-build \
  build-base meson ninja patch curl python3 binutils cairo-dev glib-dev \
  libinput-dev libsfdo-dev librsvg-dev libxml2-dev pango-dev wayland-protocols \
  wlroots0.20-dev xwayland-dev gettext-dev cmocka-dev
work=${SENTINEL_BUILD_WORK:?Run through build-component.sh}
cd "$work"
labwc_url=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["labwc"]["url"])' "$inputs/sources.lock.json")
labwc_sha=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["labwc"]["sha256"])' "$inputs/sources.lock.json")
labwc_version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["labwc"]["version"])' "$inputs/sources.lock.json")
wlroots_abi=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["labwc"]["wlrootsAbi"])' "$inputs/sources.lock.json")
test "$wlroots_abi" = 0.20
pkg-config --atleast-version=0.20.1 --max-version=0.20.99 "wlroots-$wlroots_abi"
test "$(pkg-config --variable=have_xwayland "wlroots-$wlroots_abi")" = true
[ -f labwc.tar.gz ] || curl --fail --location --retry 2 --connect-timeout 15 --max-time 180 "$labwc_url" -o labwc.tar.gz
printf '%s  %s\n' "$labwc_sha" labwc.tar.gz | sha256sum -c -
if [ ! -f prepared ]; then
mkdir -p source
tar xf labwc.tar.gz --strip-components=1 -C source
for compositor_patch in "$inputs"/patches/labwc/*.patch; do
  patch --batch --forward --fuzz=0 -d source -p1 -i "$compositor_patch"
done
touch prepared
fi
# Retain distribution config/data paths. Do not install a private wlroots,
# duplicate system defaults or silently build Meson fallback subprojects.
meson setup build source --prefix=/usr --sysconfdir=/etc --libdir=lib \
  --wrap-mode=nofallback -Dbuildtype=release -Dxwayland=enabled \
  -Dsvg=enabled -Dicon=enabled -Dnls=enabled -Dman-pages=disabled \
  -Dlabnag=disabled -Dsystemd-session=disabled -Dtest=enabled
ninja -C build -j"${SENTINEL_BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN)}"
meson test -C build --print-errorlogs
install -Dm755 build/labwc "$prefix/bin/labwc"
strip "$prefix/bin/labwc"
python3 - "$prefix/bin/labwc" "$wlroots_abi" <<'PY'
import re
import subprocess
import sys
from pathlib import Path
binary = Path(sys.argv[1])
header = binary.read_bytes()[:20]
assert header[:6] == b"\x7fELF\x02\x01" and int.from_bytes(header[18:20], "little") == 183, "labwc must be ELF64 AArch64"
program = subprocess.check_output(["readelf", "-l", str(binary)], text=True)
assert "/lib/ld-musl-aarch64.so.1" in program, "labwc must use Alpine's musl ABI"
dynamic = subprocess.check_output(["readelf", "-d", str(binary)], text=True)
needed = re.findall(r"\(NEEDED\).*?\[([^]]+)\]", dynamic)
assert [name for name in needed if name.startswith("libwlroots")] == [f"libwlroots-{sys.argv[2]}.so"], "labwc must dynamically link the distribution wlroots ABI"
PY
"$prefix/bin/labwc" --version
# GPL corresponding source includes the exact upstream archive, our changes,
# and the build recipe. Only labwc itself is bundled, not its system libraries.
source_dir="$prefix/share/sources/labwc-$labwc_version"
mkdir -p "$source_dir"
install -m644 labwc.tar.gz "$source_dir/upstream.tar.gz"
install -m644 source/LICENSE "$source_dir/LICENSE"
install -m644 "$inputs"/patches/labwc/*.patch "$source_dir/"
install -m644 "$inputs/build-labwc.sh" "$source_dir/build-labwc.sh"
install -m644 "$inputs/sources.lock.json" "$source_dir/sources.lock.json"
