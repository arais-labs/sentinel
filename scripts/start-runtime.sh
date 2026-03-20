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

Xvfb "$DISPLAY" -screen 0 "$RESOLUTION" -ac +extension RANDR -nolisten tcp &
sleep 1

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

su - sentinel -c "DISPLAY=$DISPLAY chromium \
    --no-sandbox \
    --disable-gpu \
    --disable-dev-shm-usage \
    --remote-debugging-address=0.0.0.0 \
    --remote-debugging-port=9222 \
    --disable-blink-features=AutomationControlled \
    --no-first-run \
    --no-default-browser-check \
    --window-size=1920,1080 \
    about:blank" &

# Chromium 143+ ignores --remote-debugging-address and binds CDP to 127.0.0.1 only.
# Expose it on 0.0.0.0 so the backend can reach it from another container.
socat TCP-LISTEN:9223,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9222 &

echo "Runtime ready: SSH on 22, noVNC on 6080, CDP on 9223"

# Keep alive — container stays up until explicitly stopped
sleep infinity
