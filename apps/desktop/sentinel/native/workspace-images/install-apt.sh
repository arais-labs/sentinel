#!/bin/sh
# Image-build transaction only. No package manager runs from the boot command.
set -eu
distribution=$1
release=$2
. /etc/os-release
[ "$ID" = "$distribution" ] && [ "$VERSION_ID" = "$release" ]
export DEBIAN_FRONTEND=noninteractive
policy=/usr/sbin/policy-rc.d
# Some official OCI roots already prevent service starts. This derived image
# replaces that build-time policy and removes it before native init can boot.
printf '#!/bin/sh\nexit 101\n' > "$policy"
chmod 755 "$policy"
trap 'rm -f /usr/sbin/policy-rc.d' EXIT
apt-get -o Acquire::Retries=3 update
case "$distribution" in ubuntu|debian) ;; *) exit 1 ;; esac
# Native packages own the service units and PAM/session integration. Desktop
# choices install applications later; they never replace this OS service layer.
apt-get -o DPkg::Lock::Timeout=120 install -y --no-install-recommends \
    systemd-sysv systemd dbus dbus-user-session libpam-systemd udev greetd \
    sudo passwd python3 util-linux coreutils ca-certificates \
    iproute2 apparmor
systemctl set-default multi-user.target
# /run belongs to this VM, not macOS. Native login mounts must propagate into
# child namespaces created earlier by confined applications such as Snap.
systemctl enable sentinel-runtime-mounts.service
# Distro helpers skip container boots, but this workspace owns its VM kernel.
# Kernel policy must be restored on every boot, not only package installation.
systemctl enable sentinel-apparmor.service
# Configured and started by the desktop's native session owner, not on an
# unconfigured base-image boot.
systemctl disable greetd.service
# Each new workspace must receive a new machine identity, not the builder's.
truncate -s 0 /etc/machine-id
rm -f /var/lib/dbus/machine-id
ln -s /etc/machine-id /var/lib/dbus/machine-id
mkdir -p /usr/lib/sentinel
printf '{"schema":1,"boot_contract":1,"distribution":"%s","release":"%s","init":"systemd"}\n' \
    "$distribution" "$release" > /usr/lib/sentinel/workspace-image.json
dpkg-query -W -f='${binary:Package}\t${Version}\n' | sort > /usr/lib/sentinel/base-packages.tsv
apt-get clean
rm -rf /var/lib/apt/lists/*
test -x /sbin/init
test -f /usr/lib/systemd/system/systemd-logind.service
test -f /usr/lib/systemd/system/dbus.service
