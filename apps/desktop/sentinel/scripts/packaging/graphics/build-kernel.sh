#!/bin/sh
set -eu
# Kata's pinned 6.18.35 configuration, plus the virtual display/input devices.
# This artifact is libc-independent and shared by all workspace distributions.
prefix=${SENTINEL_GRAPHICS_PREFIX:-/opt/sentinel/graphics}
apk add bash build-base bison flex perl openssl-dev elfutils-dev bc curl xz python3 kmod patch coreutils findutils sed ccache
work=${SENTINEL_BUILD_WORK:?Run through build-component.sh}
# Match Mesa's persistent compiler cache, independently of the component key.
# Kbuild tracks headers/configuration; ccache also hashes compiler contents.
export CCACHE_DIR="${SENTINEL_BUILD_CACHE:-/var/cache/sentinel-build}/ccache"
export CCACHE_BASEDIR="$work"
export CCACHE_COMPILERCHECK=content
export CC="ccache cc" HOSTCC="ccache cc" HOSTCXX="ccache c++"
cd "$work"
[ -f linux.tar.xz ] || curl -fL --retry 2 --connect-timeout 15 --max-time 300 https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.18.35.tar.xz -o linux.tar.xz
printf '%s  %s\n' f78602932219125e211c5f5bfd84edcfd4ec5ce88fc944f8248413f665bef236 linux.tar.xz | sha256sum -c -
[ -f loopback.tar.gz ] || curl -fL --retry 2 --connect-timeout 15 --max-time 180 https://codeload.github.com/unified-hmi/virtio-loopback-driver/tar.gz/62a1d6a71601c2fb96f59d56550f00e1851bc6c2 -o loopback.tar.gz
printf '%s  %s\n' 3ff95985cf7499c188ed4b71c363ebb10726c241095d195dbad4af9dbaab78f2 loopback.tar.gz | sha256sum -c -
if [ ! -f prepared ]; then
tar xf linux.tar.xz
mkdir -p loopback
tar xf loopback.tar.gz --strip-components=1 -C loopback
cd linux-6.18.35
patch --batch --forward --fuzz=0 -p1 < "$SENTINEL_GRAPHICS_INPUTS/kernel-virtio-vblank.patch"
patch --batch --forward --fuzz=0 -p1 < "$SENTINEL_GRAPHICS_INPUTS/kernel-namespace-order.patch"
python3 "$SENTINEL_GRAPHICS_INPUTS/generate-display-modes.py" \
  "$SENTINEL_GRAPHICS_INPUTS/modes.json" > drivers/gpu/drm/virtio/sentinel_display_modes.h
cp "$SENTINEL_GRAPHICS_INPUTS/kernel.config" .config
scripts/config --enable MODULES --enable MODULE_UNLOAD --disable MODVERSIONS \
  --enable DRM --enable DRM_KMS_HELPER --enable DRM_GEM_SHMEM_HELPER --module DRM_VIRTIO_GPU \
  --enable INPUT --enable INPUT_EVDEV --enable INPUT_MISC --enable INPUT_UINPUT --enable FB --enable DRM_FBDEV_EMULATION \
  --enable VT --enable VT_CONSOLE --enable FRAMEBUFFER_CONSOLE --enable DEVTMPFS \
  --enable DEVTMPFS_MOUNT --disable DEBUG_INFO --disable DEBUG_INFO_BTF --enable DEBUG_INFO_NONE \
  --enable USERFAULTFD --enable SHMEM --enable MEMFD_CREATE --enable CHECKPOINT_RESTORE \
  --enable BLK_DEV_LOOP --enable SQUASHFS --enable SQUASHFS_XATTR \
  --enable SQUASHFS_ZLIB --enable SQUASHFS_XZ --enable SQUASHFS_LZO --enable SQUASHFS_LZ4 --enable SQUASHFS_ZSTD \
  --enable SECURITY --enable SECURITYFS --enable SECURITY_APPARMOR \
  --disable LOCALVERSION_AUTO --set-str LOCALVERSION '-sentinel-5'
touch "$work/prepared"
fi
cd "$work/linux-6.18.35"
# Stable build metadata makes an identical input produce an identical artifact.
export KBUILD_BUILD_TIMESTAMP='2026-09-19 00:00:00 UTC' KBUILD_BUILD_USER=sentinel KBUILD_BUILD_HOST=builder KBUILD_BUILD_VERSION=1
date -d "$KBUILD_BUILD_TIMESTAMP" +%s >/dev/null
test "$(find . -maxdepth 0 -printf '%f')" = .
make CC="$CC" HOSTCC="$HOSTCC" HOSTCXX="$HOSTCXX" olddefconfig
for option in INPUT_EVDEV INPUT_UINPUT DEVTMPFS SHMEM MEMFD_CREATE USERFAULTFD CHECKPOINT_RESTORE BLK_DEV_LOOP SQUASHFS SECURITYFS SECURITY_APPARMOR; do
  grep -qx "CONFIG_${option}=y" .config || { printf 'Required kernel option missing: %s\n' "$option" >&2; exit 1; }
done
grep -qx 'CONFIG_DRM_VIRTIO_GPU=m' .config
# AppArmor's generated tables use GNU sed's lowercase replacement syntax.
# Interrupted generation can leave empty headers that make considers current.
# Regenerate only these cheap outputs, preserving compiled kernel objects.
test "$(printf A | sed 's/A/\La/')" = a
rm -f security/apparmor/capability_names.h security/apparmor/net_names.h security/apparmor/rlim_names.h
make CC="$CC" HOSTCC="$HOSTCC" HOSTCXX="$HOSTCXX" -j"${SENTINEL_BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN)}" Image modules
make CC="$CC" HOSTCC="$HOSTCC" HOSTCXX="$HOSTCXX" INSTALL_MOD_PATH="$prefix" INSTALL_MOD_STRIP=1 modules_install
release=$(make CC="$CC" HOSTCC="$HOSTCC" HOSTCXX="$HOSTCXX" -s kernelrelease)
make CC="$CC" HOSTCC="$HOSTCC" HOSTCXX="$HOSTCXX" -C "$PWD" M="$work/loopback" -j"${SENTINEL_BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN)}" modules
install -m 644 "$work/loopback/virtio-lo.ko" "$prefix/lib/modules/$release/virtio-lo.ko"
strip --strip-debug "$prefix/lib/modules/$release/virtio-lo.ko"
rm -f "$prefix/lib/modules/$release/build" "$prefix/lib/modules/$release/source"
depmod -b "$prefix" "$release"
cp arch/arm64/boot/Image "$prefix/Image"
cp .config "$prefix/config"
cp COPYING "$prefix/LICENSE"
printf '%s\n' "$release" > "$prefix/kernelrelease"
printf '%s\n' "$release" > "$prefix/version"
ccache --show-stats
