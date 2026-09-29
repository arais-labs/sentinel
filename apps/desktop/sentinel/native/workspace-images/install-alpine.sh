#!/bin/sh
# Image-build transaction only; OpenRC owns workspace PID 1.
set -eu
. /etc/os-release
[ "$ID" = alpine ]
case "$VERSION_ID" in 3.24.*) ;; *) exit 1 ;; esac
apk add --no-cache \
    openrc openrc-init dbus dbus-openrc elogind elogind-openrc \
    eudev eudev-openrc udev-init-scripts-openrc linux-pam shadow greetd greetd-openrc \
    sudo python3 util-linux coreutils ca-certificates iproute2
# The container manager supplies mounted API filesystems and VM networking.
# Use the distro's real startup scripts; never fabricate OpenRC softlevel files.
rc-update add udev sysinit
rc-update add udev-trigger sysinit
rc-update add udev-settle sysinit
rc-update add cgroups boot
chmod 755 /etc/init.d/sentinel-runtime-mounts
rc-update add sentinel-runtime-mounts boot
# Containerization already configures the VM's external interface. The native
# networking service owns loopback only.
mkdir -p /etc/network
printf 'auto lo\niface lo inet loopback\n' > /etc/network/interfaces
rc-update add networking boot
rc-update add dbus default
rc-update add elogind default
mkdir -p /usr/lib/sentinel
printf '{"schema":1,"boot_contract":1,"distribution":"alpine","release":"3.24","init":"openrc"}\n' \
    > /usr/lib/sentinel/workspace-image.json
apk info -v | sort > /usr/lib/sentinel/base-packages.tsv
# Unlike systemd, dbus-uuidgen rejects an existing empty machine-id. Its native
# OpenRC start_pre hook generates the missing per-workspace ID on first boot.
rm -f /etc/machine-id
rm -f /var/lib/dbus/machine-id
mkdir -p /var/lib/dbus
ln -s /etc/machine-id /var/lib/dbus/machine-id
test -x /sbin/openrc-init
test -x /etc/init.d/elogind
grep -Eq 'session[[:space:]]+include[[:space:]]+base-session' /etc/pam.d/greetd
grep -Eq 'session[[:space:]]+optional[[:space:]]+pam_elogind.so' /usr/lib/pam.d/base-session
test -f /usr/lib/security/pam_elogind.so
