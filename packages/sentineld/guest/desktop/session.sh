#!/bin/sh
# The desktop session, run by the distribution's desktop service (distribution.json), which restarts
# it when it ends. /etc/sentinel/desktop, written by sentineld, names the desktop, its display system
# and the distribution's script that starts it (desktops/<desktop>/session). X11 desktops (root): an
# X server on the virtio GPU, then that script as the sentinel user. Wayland desktops (the sentinel
# user, in a session that holds the seat): that script, whose compositor drives the GPU itself.
# `session.sh publish` (root) writes /run/sentinel-desktop/session.json: the session's user and
# environment, for what the agent runs in it.
set -eu
. /etc/sentinel/desktop
# The GPU is Vulkan only (Venus): OpenGL, the display server's included, runs on it through Zink.
# publish() hands the same setting to what is started into the session from outside.
gl="export MESA_LOADER_DRIVER_OVERRIDE=zink"
eval "$gl"
uid=$(id -u sentinel)
runtime=/run/user/$uid
# The session bus at a fixed address (the systemd user manager's own, where there is one).
bus="[ -S $runtime/bus ] || dbus-daemon --session --address=unix:path=$runtime/bus --fork --nopidfile
export DBUS_SESSION_BUS_ADDRESS=unix:path=$runtime/bus"

publish() {
	wayland=
	[ "$SENTINEL_DISPLAY" = x11 ] || wayland="\"WAYLAND_DISPLAY\": \"wayland-0\", "
	mkdir -p /run/sentinel-desktop
	printf '{"session_type": "%s", "environment": {%s"DISPLAY": ":0", "MESA_LOADER_DRIVER_OVERRIDE": "zink", "XDG_RUNTIME_DIR": "%s", "DBUS_SESSION_BUS_ADDRESS": "unix:path=%s/bus"}, "user": {"uid": %s, "gid": %s, "groups": [%s], "home": "%s"}}\n' \
		"$SENTINEL_DISPLAY" "$wayland" "$runtime" "$runtime" "$uid" "$(id -g sentinel)" "$(id -G sentinel | tr ' ' ',')" \
		"$(getent passwd sentinel | cut -d: -f6)" >/run/sentinel-desktop/session.json.next
	mv /run/sentinel-desktop/session.json.next /run/sentinel-desktop/session.json
}
if [ "${1:-}" = publish ]; then publish; exit 0; fi

if [ "$SENTINEL_DISPLAY" = wayland ]; then
	[ "$(id -u)" != 0 ] || { echo "a Wayland session runs as the desktop user" >&2; exit 1; }
	export XDG_SESSION_TYPE=wayland
	eval "$bus"
	exec sh "$SENTINEL_SESSION"
fi

publish
install -d -m 700 -o sentinel "$runtime"
# This service owns display :0: a lock or socket left by a server that died goes first.
rm -f /tmp/.X0-lock /tmp/.X11-unix/X0
Xorg :0 vt7 -nolisten tcp -noreset -novtswitch >/run/sentinel-desktop.log 2>&1 &
xorg=$!
# The shell holds a signal until a foreground child exits: wait in the background instead, so a
# service stop (SIGTERM) ends the session at once and takes the X server with it.
# The next session's X server needs display :0 free: wait for this one to let go of it.
trap 'kill $xorg $desktop_pid 2>/dev/null; wait $xorg; exit 0' TERM INT
trap 'kill $xorg 2>/dev/null; wait $xorg' EXIT
for _ in $(seq 100); do [ -S /tmp/.X11-unix/X0 ] && break; kill -0 $xorg 2>/dev/null || exit 1; sleep 0.1; done
su -l sentinel -c "export DISPLAY=:0 XDG_RUNTIME_DIR=$runtime; $gl; $bus; exec sh '$SENTINEL_SESSION'" >>/run/sentinel-desktop.log 2>&1 &
desktop_pid=$!
wait $desktop_pid
