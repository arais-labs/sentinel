#!/bin/sh
set -eu
version=mesa-25.3.0-metal-2
prefix=/opt/sentinel/graphics
if [ "$(cat "$prefix/version" 2>/dev/null || true)" = "$version" ]; then exit 0; fi
cleanup_packages() { :; }
if [ "${SENTINEL_GRAPHICS_LIBC:-musl}" = glibc ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update -qq
  apt-get install -y --no-install-recommends ca-certificates curl xz-utils build-essential pkg-config python3-venv ninja-build bison flex python3-mako python3-packaging python3-yaml libdrm-dev libx11-dev libx11-xcb-dev libxcb-keysyms1-dev libxext-dev libxfixes-dev libxxf86vm-dev libxdamage-dev libxrandr-dev libxshmfence-dev libxcb1-dev libxcb-dri2-0-dev libxcb-dri3-dev libxcb-glx0-dev libxcb-present-dev libxcb-randr0-dev libxcb-shm0-dev libxcb-sync-dev libxcb-xfixes0-dev x11proto-dev zlib1g-dev libexpat1-dev libzstd-dev
else
  apk add --no-cache python3 curl xz mesa-egl mesa-dri-gallium libxshmfence libdrm zstd-libs expat libx11 libxext libxcb
  apk add --no-cache --virtual .sentinel-graphics-build build-base meson ninja bison flex py3-mako py3-packaging py3-yaml libdrm-dev libx11-dev libxext-dev libxfixes-dev libxxf86vm-dev libxdamage-dev libxrandr-dev libxshmfence-dev libxcb-dev xorgproto zlib-dev expat-dev zstd-dev
  cleanup_packages() { apk del .sentinel-graphics-build >/dev/null 2>&1 || true; }
fi
work=$(mktemp -d /var/tmp/sentinel-graphics.XXXXXX)
trap 'rm -rf "$work"; cleanup_packages' EXIT
cd "$work"
if [ "${SENTINEL_GRAPHICS_LIBC:-musl}" = glibc ]; then
  python3 -m venv --system-site-packages "$work/tools"
  "$work/tools/bin/pip" install --no-deps 'https://files.pythonhosted.org/packages/9c/07/b48592d325cb86682829f05216e4efb2dc881762b8f1bafb48b57442307a/meson-1.9.1-py3-none-any.whl#sha256=f824ab770c041a202f532f69e114c971918ed2daff7ea56583d80642564598d0'
  export PATH="$work/tools/bin:$PATH"
fi
curl --fail --location --retry 2 --connect-timeout 15 --max-time 180 https://archive.mesa3d.org/mesa-25.3.0.tar.xz -o mesa.tar.xz
printf '%s  %s\n' 0fd54fea7dbbddb154df05ac752b18621f26d97e27863db3be951417c6abe8ae mesa.tar.xz | sha256sum -c -
tar xf mesa.tar.xz
# Protocol 0 returns pixels inline. Consume them before the busy-wait reply;
# otherwise that 12-byte reply parser consumes the first three image pixels.
python3 - <<'PATCH'
from pathlib import Path
path = Path("mesa-25.3.0/src/gallium/winsys/virgl/vtest/virgl_vtest_winsys.c")
source = path.read_text()
old = "if (flush_front_buffer || vtws->protocol_version >= 2)"
assert source.count(old) == 1
source = source.replace(old, "if (vtws->protocol_version >= 2)")
old = """                                         valid_stride, box, res->format);
      virgl_vtest_resource_unmap(vws, res);"""
assert source.count(old) == 1
source = source.replace(old, """                                         valid_stride, box, res->format);
      if (flush_front_buffer)
         virgl_vtest_busy_wait(vtws, res->res_handle, VCMD_BUSY_WAIT_FLAG_WAIT);
      virgl_vtest_resource_unmap(vws, res);""")
path.write_text(source)
PATCH
meson setup build mesa-25.3.0 --prefix="$prefix" --libdir=lib -Dbuildtype=release -Dgallium-drivers=virgl,softpipe -Dvulkan-drivers= -Dllvm=disabled -Dglx=dri -Dglvnd=disabled -Degl=enabled -Dgbm=enabled -Dplatforms=x11 -Dvideo-codecs= -Dgallium-va=disabled
ninja -C build -j2 install
printf '%s' "$version" > "$prefix/version"
