#!/bin/sh
# Alpine's Wayland session, run as root by sentinel-desktop-wayland.openrc. OpenRC opens no login
# sessions, so the compositor gets the seat another way: GNOME's Mutter takes it only from a logind
# session, so GNOME logs in through PAM on tty7 (elogind registers it; the class comes from the
# environment at login); the other compositors get it from seatd.
set -eu
. /etc/sentinel/desktop
session=/run/sentinel/desktop/session.sh
sh $session publish
if [ "$SENTINEL_DESKTOP" = gnome ]; then
	exec openvt -c 7 -f -s -w -- env XDG_SESSION_CLASS=user runuser -l sentinel -c "exec sh $session" \
		>>/run/sentinel-desktop.log 2>&1
fi
runtime=/run/user/$(id -u sentinel)
install -d -m 700 -o sentinel "$runtime"
export XDG_RUNTIME_DIR=$runtime HOME="$(getent passwd sentinel | cut -d: -f6)" USER=sentinel LOGNAME=sentinel
# su -m keeps this environment.
su -m sentinel -s /bin/sh -c "cd; exec sh $session" >>/run/sentinel-desktop.log 2>&1 &
pid=$!
# Waiting in the background lets a service stop (SIGTERM) through at once; how the session ended goes
# to the log, the only trace of a compositor that dies.
trap 'kill $pid 2>/dev/null; wait $pid; exit 0' TERM INT
status=0
wait $pid || status=$?
echo "sentinel-desktop: session ended with status $status at $(date)" >>/run/sentinel-desktop.log
exit $status
