set -eu
prefix=/opt/sentinel/graphics
test "$(uname -r)" = "$(cat /opt/sentinel/kernel/kernelrelease)"
/sbin/modprobe virtio_gpu
/sbin/modprobe virtio-lo
/sbin/modprobe uinput
test -c /dev/virtio-lo
test -c /dev/uinput
"$prefix/bin/sentinel-mapped-memory-check"
# Native init owns the device service. A display connection must never start a
# second udev daemon or fabricate a login runtime directory.
udevadm control --reload
udevadm trigger --subsystem-match=drm
udevadm trigger --subsystem-match=input
udevadm settle --timeout=10
exec env LD_LIBRARY_PATH="$prefix/lib" "$prefix/bin/rvgpu-proxy" \
  -c /run/sentinel-desktop/virgl.capset -s "${1}x${2}@0,0" -n 127.0.0.1:55667
