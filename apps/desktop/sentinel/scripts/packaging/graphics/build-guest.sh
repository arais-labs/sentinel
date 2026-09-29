#!/bin/sh
set -eu
version=mesa-26.1.6-mapped-storage-glvnd-1
prefix=${SENTINEL_GRAPHICS_PREFIX:-/opt/sentinel/graphics}
if [ "${SENTINEL_GRAPHICS_LIBC:-musl}" = glibc ]; then
  glvnd=enabled
  mesa_prefix=/opt/sentinel/graphics
  gles1=disabled
  export DEBIAN_FRONTEND=noninteractive
  apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update -qq
  apt-get install -y --no-install-recommends ca-certificates curl xz-utils build-essential pkg-config python3-venv ninja-build bison flex python3-mako python3-packaging python3-yaml libdrm-dev libx11-dev libx11-xcb-dev libxcb-keysyms1-dev libxext-dev libxfixes-dev libxxf86vm-dev libxdamage-dev libxrandr-dev libxshmfence-dev libxcb1-dev libxcb-dri2-0-dev libxcb-dri3-dev libxcb-glx0-dev libxcb-present-dev libxcb-randr0-dev libxcb-shm0-dev libxcb-sync-dev libxcb-xfixes0-dev x11proto-dev zlib1g-dev libexpat1-dev libzstd-dev
  apt-get install -y --no-install-recommends patch libwayland-dev libwayland-egl-backend-dev wayland-protocols libudev-dev linux-libc-dev libbsd-dev libglvnd-dev
else
  # Alpine's public GL ABI is Mesa itself, not GLVND. Match the distribution's
  # client ABI instead of introducing a second dispatch implementation.
  glvnd=disabled
  version=mesa-26.1.6-native-alpine-1
  mesa_prefix=/usr
  gles1=enabled
  apk add python3 curl xz mesa-egl mesa-dri-gallium libxshmfence libdrm zstd-libs expat libx11 libxext libxcb
  apk add --virtual .sentinel-graphics-build build-base meson ninja bison flex py3-mako py3-packaging py3-yaml libdrm-dev libx11-dev libxext-dev libxfixes-dev libxxf86vm-dev libxdamage-dev libxrandr-dev libxshmfence-dev libxcb-dev xorgproto zlib-dev expat-dev zstd-dev
  apk add --virtual .sentinel-display-build patch wayland-dev wayland-protocols eudev-dev linux-headers libbsd-dev
  apk add wayland-libs-client wayland-libs-server eudev-libs
  apk add abuild openssl
fi
work=${SENTINEL_BUILD_WORK:?Run through build-component.sh}
# Artifact keys deliberately isolate outputs. Reuse unchanged compilation units
# across those keys in the same private builder, without weakening input checks.
if [ "${SENTINEL_GRAPHICS_LIBC:-musl}" = glibc ]; then
  apt-get install -y --no-install-recommends ccache
else
  apk add ccache
fi
export CCACHE_DIR="${SENTINEL_BUILD_CACHE:-/var/cache/sentinel-build}/ccache"
export CCACHE_BASEDIR="$work"
export CCACHE_COMPILERCHECK=content
export CC="ccache cc" CXX="ccache c++"
cd "$work"
if [ "${SENTINEL_GRAPHICS_LIBC:-musl}" = glibc ]; then
  python3 -m venv --system-site-packages "$work/tools"
  "$work/tools/bin/pip" install --no-deps 'https://files.pythonhosted.org/packages/9c/07/b48592d325cb86682829f05216e4efb2dc881762b8f1bafb48b57442307a/meson-1.9.1-py3-none-any.whl#sha256=f824ab770c041a202f532f69e114c971918ed2daff7ea56583d80642564598d0'
  export PATH="$work/tools/bin:$PATH"
fi
# The host verifies and caches this source once for both libc builds.
if [ ! -f mesa-prepared ]; then
tar xf "$SENTINEL_GRAPHICS_INPUTS/mesa.tar.xz"
for mesa_patch in "$SENTINEL_GRAPHICS_INPUTS"/patches/guest/*.patch; do
  patch --batch --forward --fuzz=0 -d mesa-26.1.6 -p1 -i "$mesa_patch"
done
touch mesa-prepared
fi
for driver_source in "$SENTINEL_GRAPHICS_INPUTS"/guest/driver/*.[ch]; do
  cmp -s "$driver_source" "mesa-26.1.6/src/gallium/drivers/virgl/${driver_source##*/}" ||
    install -m 644 "$driver_source" mesa-26.1.6/src/gallium/drivers/virgl/
done
meson setup build mesa-26.1.6 --prefix="$mesa_prefix" --libdir=lib -Dbuildtype=release \
  -Dgallium-drivers=virgl -Dvulkan-drivers= -Dllvm=disabled -Dglx=dri -Dglvnd="$glvnd" \
  -Degl=enabled -Dgbm=enabled -Dplatforms=x11,wayland -Dvideo-codecs= -Dgallium-va=disabled \
  -Dgles1="$gles1" -Dgles2=enabled -Dgallium-rusticl=false -Dbuild-tests=false

# Bundle the guest device proxy; only the owning Mac sees its command stream.
[ -f proxy.tar.gz ] || curl -fL --retry 2 --connect-timeout 15 --max-time 180 https://codeload.github.com/unified-hmi/remote-virtio-gpu/tar.gz/1336f8af5fa9c1f4a7c55dd6ba94990de50e739e -o proxy.tar.gz
printf '%s  %s\n' e5e5d065e8d74cabec555e8d730c890b5a677dcff768125e262cbdffdd25d664 proxy.tar.gz | sha256sum -c -
[ -f loopback.tar.gz ] || curl -fL --retry 2 --connect-timeout 15 --max-time 180 https://codeload.github.com/unified-hmi/virtio-loopback-driver/tar.gz/62a1d6a71601c2fb96f59d56550f00e1851bc6c2 -o loopback.tar.gz
printf '%s  %s\n' 3ff95985cf7499c188ed4b71c363ebb10726c241095d195dbad4af9dbaab78f2 loopback.tar.gz | sha256sum -c -
if [ ! -f proxy-prepared ]; then
mkdir -p proxy loopback
tar xf proxy.tar.gz --strip-components=1 -C proxy
tar xf loopback.tar.gz --strip-components=1 -C loopback
patch --batch --forward --fuzz=0 -d proxy -p1 -i "$SENTINEL_GRAPHICS_INPUTS/remote-proxy-fences.patch"
touch proxy-prepared
fi
mkdir -p "$work/include/linux" "$prefix/lib"
cp loopback/virtio_lo.h "$work/include/linux/virtio_lo.h"
cd proxy
mkdir -p "$prefix/bin"
cc -std=c11 -O2 -Wall -Wextra -Werror \
  -I"$SENTINEL_GRAPHICS_INPUTS/transport" \
  "$SENTINEL_GRAPHICS_INPUTS/guest/gpu-bridge.c" \
  "$SENTINEL_GRAPHICS_INPUTS/transport/gpu_transport.c" \
  -o "$prefix/bin/sentinel-gpu-bridge"
cc -O2 -Wall -Wextra -Werror -pthread \
  "$SENTINEL_GRAPHICS_INPUTS/guest/driver/mapped-memory-check.c" \
  "$SENTINEL_GRAPHICS_INPUTS/guest/driver/dirty_tracker.c" \
  -o "$prefix/bin/sentinel-mapped-memory-check"
proxy_cflags="$(pkg-config --cflags libbsd-overlay) -include string.h -include limits.h -D__IOV_MAX=IOV_MAX -I$work/include"
cc -O2 -fPIC -shared -D_GNU_SOURCE $proxy_cflags -Iinclude -I"$work/loopback" \
  src/librvgpu/rvgpu.c src/librvgpu/tcp/rvgpu-tcp.c src/librvgpu/res/rvgpu-res.c \
  src/rvgpu-utils/rvgpu-utils.c -pthread -lbsd -Wl,-soname,librvgpu.so.0 -o "$prefix/lib/librvgpu.so.0"
cc -O2 -D_GNU_SOURCE -D_DEFAULT_SOURCE -D_FILE_OFFSET_BITS=64 -DLIBRVGPU_SOVERSION=0 \
  $proxy_cflags -Iinclude -I"$work/loopback" -I/usr/include/libdrm \
  src/rvgpu-proxy/rvgpu-proxy.c src/rvgpu-proxy/gpu/*.c \
  src/rvgpu-utils/rvgpu-utils.c src/rvgpu-sanity/rvgpu-sanity.c \
  -pthread -ldl -ludev -lbsd -o "$prefix/bin/rvgpu-proxy"
cp LICENSE.md "$prefix/remote-virtio-gpu-LICENSE"
cd "$work"
DESTDIR="$work/mesa-install" ninja -C build -j"${SENTINEL_BUILD_JOBS:-4}" install
ccache --show-stats
if [ "$glvnd" = disabled ]; then
  python3 "$SENTINEL_GRAPHICS_INPUTS/packaging/mesa/build.py" \
    "$SENTINEL_GRAPHICS_INPUTS" "$work" "$prefix"
  install -m 644 "$SENTINEL_GRAPHICS_INPUTS/guest/install-native-mesa.py" \
    "$prefix/bin/install-native-mesa.py"
else
  cp -a "$work/mesa-install/opt/sentinel/graphics/." "$prefix/"
fi
# A GLVND distribution must load its public libEGL/libOpenGL dispatch from the
# OS, with only the Mesa vendors supplied by Sentinel. Mixing a monolithic EGL
# context with GLVND's public GL dispatch crashes Qt's context initialization.
python3 - "$prefix" "$glvnd" "$work/mesa-install/usr" <<'PY'
import json
import sys
from pathlib import Path
prefix = Path(sys.argv[1])
if sys.argv[2] == "enabled":
    for name in ("libEGL_mesa.so.0", "libGLX_mesa.so.0"):
        assert (prefix / "lib" / name).is_file(), f"Missing GLVND vendor: {name}"
    vendor = prefix / "share/glvnd/egl_vendor.d/50_mesa.json"
    assert json.loads(vendor.read_text())["ICD"]["library_path"] == "libEGL_mesa.so.0"
    for public in ("EGL", "GL", "GLX", "OpenGL", "GLESv1_CM", "GLESv2", "GLdispatch"):
        assert not list((prefix / "lib").glob(f"lib{public}.so*")), f"Bundled public GLVND dispatch: {public}"
else:
    native = Path(sys.argv[3])
    for name in ("libEGL.so.1", "libGL.so.1", "libGLESv1_CM.so.1", "libGLESv2.so.2"):
        assert (native / "lib" / name).is_file(), f"Missing Alpine Mesa ABI: {name}"
PY
# Use the unmodified maintained client for both ext-data-control and the older
# wlr protocol. Distribution clients currently lag the ext protocol used by KWin.
# Keep protocol XML independent of the oldest guest libc build image. These are
# build-time definitions only: no replacement Wayland runtime library is shipped.
protocols_url=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["wayland-protocols"]["url"])' "$SENTINEL_GRAPHICS_INPUTS/sources.lock.json")
protocols_sha=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["wayland-protocols"]["sha256"])' "$SENTINEL_GRAPHICS_INPUTS/sources.lock.json")
[ -f wayland-protocols.tar.gz ] || curl -fL --retry 2 --connect-timeout 15 --max-time 180 "$protocols_url" -o wayland-protocols.tar.gz
printf '%s  %s\n' "$protocols_sha" wayland-protocols.tar.gz | sha256sum -c -
mkdir -p wayland-protocols
tar xf wayland-protocols.tar.gz --strip-components=1 -C wayland-protocols
meson setup protocols-build wayland-protocols --prefix="$work/protocols" -Dtests=false
ninja -C protocols-build install
clipboard_url=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["wl-clipboard"]["url"])' "$SENTINEL_GRAPHICS_INPUTS/sources.lock.json")
clipboard_sha=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["wl-clipboard"]["sha256"])' "$SENTINEL_GRAPHICS_INPUTS/sources.lock.json")
[ -f wl-clipboard.tar.gz ] || curl -fL --retry 2 --connect-timeout 15 --max-time 180 "$clipboard_url" -o wl-clipboard.tar.gz
printf '%s  %s\n' "$clipboard_sha" wl-clipboard.tar.gz | sha256sum -c -
mkdir -p wl-clipboard
tar xf wl-clipboard.tar.gz --strip-components=1 -C wl-clipboard
PKG_CONFIG_PATH="$work/protocols/share/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}" \
  meson setup clipboard-build wl-clipboard --prefix=/opt/sentinel/graphics -Dbuildtype=release \
  -Dzshcompletiondir=no -Dfishcompletiondir=no
# A successful build without the required protocol must not pass packaging.
if ! grep -Eq '^#define HAVE_EXT_DATA_CONTROL([[:space:]]+1)?[[:space:]]*$' clipboard-build/src/config.h; then
  printf '%s\n' 'wl-clipboard build is missing required ext-data-control support' >&2
  exit 1
fi
DESTDIR="$work/install" ninja -C clipboard-build -j"${SENTINEL_BUILD_JOBS:-4}" install
cp -a "$work/install/opt/sentinel/graphics/." "$prefix/"
# These GPL tools remain separate executables. Ship their complete corresponding
# upstream source and license with the same artifact, not merely a download URL.
mkdir -p "$prefix/share/sources"
install -m 644 wl-clipboard.tar.gz "$prefix/share/sources/wl-clipboard-2.3.0.tar.gz"
install -m 644 wl-clipboard/COPYING "$prefix/share/sources/wl-clipboard-COPYING"
install -m 644 wayland-protocols.tar.gz "$prefix/share/sources/wayland-protocols-1.39.tar.gz"
install -m 644 wayland-protocols/COPYING "$prefix/share/sources/wayland-protocols-COPYING"
printf '%s\n' "$version" > "$prefix/version"
