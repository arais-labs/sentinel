#!/usr/bin/env bash
set -euo pipefail

# --- SSH key injection ---
SSH_PUBLIC_KEY="${SSH_PUBLIC_KEY:-}"
if [[ -n "$SSH_PUBLIC_KEY" ]]; then
    mkdir -p /home/sentinel/.ssh
    echo "$SSH_PUBLIC_KEY" > /home/sentinel/.ssh/authorized_keys
    chown -R sentinel:sentinel /home/sentinel/.ssh
    chmod 700 /home/sentinel/.ssh
    chmod 600 /home/sentinel/.ssh/authorized_keys
fi

# --- Workspace ownership ---
# The workspace dir may be bind-mounted from the host (created by root).
# Ensure the sentinel user can write to it.
mkdir -p /home/sentinel/workspace
chown sentinel:sentinel /home/sentinel/workspace

# --- Start SSH ---
/usr/sbin/sshd

# --- D-Bus (required by KDE) ---
mkdir -p /run/dbus
dbus-daemon --system --fork 2>/dev/null || true

# --- XDG runtime dir for KDE ---
XDG_RT="/tmp/runtime-sentinel"
mkdir -p "$XDG_RT"
chown sentinel:sentinel "$XDG_RT"
chmod 700 "$XDG_RT"

# --- Desktop environment ---
RESOLUTION="${RUNTIME_RESOLUTION:-1920x1080x24}"
export DISPLAY=:99
export TZ="${BROWSER_TIMEZONE_ID:-${TZ:-America/Los_Angeles}}"
WIDTH="${RESOLUTION%x*}"
HEIGHT_DEPTH="${RESOLUTION#*x}"
HEIGHT="${HEIGHT_DEPTH%x*}"
DEPTH="${RESOLUTION##*x}"
if [[ -z "$WIDTH" || -z "$HEIGHT" || -z "$DEPTH" ]]; then
    WIDTH=1920
    HEIGHT=1080
    DEPTH=24
fi

mkdir -p /etc/X11
cat > /etc/X11/xorg-dummy.conf <<EOF
Section "ServerLayout"
    Identifier "Layout0"
    Screen 0 "Screen0"
EndSection

Section "Monitor"
    Identifier "Monitor0"
    HorizSync 28.0-80.0
    VertRefresh 48.0-75.0
    Modeline "1920x1080" 172.80 1920 2040 2248 2576 1080 1081 1084 1118
EndSection

Section "Device"
    Identifier "Device0"
    Driver "dummy"
    VideoRam 256000
EndSection

Section "Screen"
    Identifier "Screen0"
    Device "Device0"
    Monitor "Monitor0"
    DefaultDepth $DEPTH
    SubSection "Display"
        Depth $DEPTH
        Modes "1920x1080"
        Virtual $WIDTH $HEIGHT
    EndSubSection
EndSection
EOF

if command -v Xorg >/dev/null 2>&1; then
    Xorg "$DISPLAY" -noreset -nolisten tcp -config /etc/X11/xorg-dummy.conf +extension GLX +extension RANDR +extension RENDER &
    X_PID=$!
    sleep 2
    if ! kill -0 "$X_PID" 2>/dev/null; then
        echo "Xorg dummy display failed, falling back to Xvfb" >&2
        Xvfb "$DISPLAY" -screen 0 "$RESOLUTION" -ac +extension RANDR -nolisten tcp &
        sleep 1
    fi
else
    Xvfb "$DISPLAY" -screen 0 "$RESOLUTION" -ac +extension RANDR -nolisten tcp &
    sleep 1
fi

# Disable KDE screen locker and power management
SENTINEL_KDE_CFG="/home/sentinel/.config"
mkdir -p "$SENTINEL_KDE_CFG"

cat > "$SENTINEL_KDE_CFG/kscreenlockerrc" <<'KSCREENLOCKER'
[Daemon]
Autolock=false
LockOnResume=false
Timeout=0
KSCREENLOCKER

cat > "$SENTINEL_KDE_CFG/powermanagementprofilesrc" <<'POWER'
[AC][DPMSControl]
idleTimeout=0
lockBeforeTurnOff=0

[AC][SuspendSession]
idleTimeout=0
suspendThenHibernate=false
suspendType=0
POWER

chown -R sentinel:sentinel "$SENTINEL_KDE_CFG"

# Start KDE Plasma desktop session with KWin window manager
su - sentinel -c "DISPLAY=$DISPLAY XDG_RUNTIME_DIR=$XDG_RT XDG_SESSION_TYPE=x11 startplasma-x11" &
sleep 3

# Ensure KWin is running (provides window decorations: close/min/max)
if ! pgrep -u sentinel kwin_x11 > /dev/null 2>&1; then
    su - sentinel -c "DISPLAY=$DISPLAY XDG_RUNTIME_DIR=$XDG_RT kwin_x11 --replace" &
    sleep 1
fi

# --- VNC ---
x11vnc -display "$DISPLAY" -forever -shared -rfbport 5900 -nopw -localhost -xkb &
sleep 1

NOVNC_WEB="${NOVNC_WEB:-/usr/share/novnc}"
websockify --web="$NOVNC_WEB" 0.0.0.0:6080 localhost:5900 &

# --- Chromium with CDP for remote Playwright control ---
# Clean any stale singleton lock from a previous crash
rm -f /home/sentinel/.config/chromium/SingletonLock \
      /home/sentinel/.config/chromium/SingletonSocket \
      /home/sentinel/.config/chromium/SingletonCookie 2>/dev/null || true

BROWSER_USER_AGENT="${BROWSER_USER_AGENT:-}"
BROWSER_LOCALE="${BROWSER_LOCALE:-en-US}"
BROWSER_TIMEZONE_ID="${BROWSER_TIMEZONE_ID:-America/Los_Angeles}"

su - sentinel -c "DISPLAY=$DISPLAY TZ=$BROWSER_TIMEZONE_ID MESA_LOADER_DRIVER_OVERRIDE=llvmpipe chromium \
    --disable-dev-shm-usage \
    --use-gl=angle \
    --use-angle=gl \
    --ignore-gpu-blocklist \
    --disable-gpu-driver-bug-workaround \
    --remote-debugging-address=0.0.0.0 \
    --remote-debugging-port=9222 \
    --no-first-run \
    --no-default-browser-check \
    --lang=$BROWSER_LOCALE \
    ${BROWSER_USER_AGENT:+--user-agent=\"$BROWSER_USER_AGENT\"} \
    --window-size=1920,1080 \
    about:blank" &

# Chromium 143+ ignores --remote-debugging-address and binds CDP to 127.0.0.1 only.
# Expose it on 0.0.0.0 so the backend can reach it from another container.
socat TCP-LISTEN:9223,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9222 &

echo "Runtime ready: SSH on 22, noVNC on 6080, CDP on 9223"

# Keep alive — container stays up until explicitly stopped
sleep infinity
