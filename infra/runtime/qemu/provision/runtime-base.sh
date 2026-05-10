#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export TZ="${TZ:-America/Los_Angeles}"

SENTINEL_HOME=/home/sentinel
SENTINEL_ROOT=/srv/sentinel
DEFAULT_WORKSPACE_DIR="${SENTINEL_ROOT}/default-workspace"
SESSIONS_ROOT="${SENTINEL_ROOT}/sessions"
RUNTIME_MARKER=/var/lib/sentinel/runtime-provisioned-v1
BROWSER_MARKER=/var/lib/sentinel/browser-provisioned-v1
BROWSER_ROOT=/opt/google/chrome
BROWSER_BIN="${BROWSER_ROOT}/chrome"
BROWSER_SANDBOX="${BROWSER_ROOT}/chrome_sandbox"
START_SCRIPT=/usr/local/bin/sentinel-runtime-start.sh
DESKTOP_UNIT=/etc/systemd/system/sentinel-runtime-desktop.service
BROWSER_SCRIPT=/usr/local/bin/sentinel-runtime-browser.sh
BROWSER_UNIT=/etc/systemd/system/sentinel-runtime-browser.service

apt_wait() {
  local tries=0
  while pgrep -x apt >/dev/null 2>&1 || pgrep -x apt-get >/dev/null 2>&1 || pgrep -x dpkg >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -ge 900 ]; then
      echo "Timed out waiting for apt/dpkg" >&2
      return 1
    fi
    sleep 1
  done
}

apt_wait
apt-get update
apt_wait
apt-get install -y --no-install-recommends \
  at-spi2-core \
  build-essential \
  ca-certificates \
  curl \
  dbus-x11 \
  git \
  htop \
  jq \
  konsole \
  kwin-x11 \
  locales \
  mesa-utils \
  net-tools \
  nodejs \
  npm \
  novnc \
  openbox \
  plasma-desktop \
  plasma-workspace \
  procps \
  python3 \
  python3-pip \
  python3-venv \
  ripgrep \
  socat \
  sudo \
  tree \
  tzdata \
  websockify \
  wget \
  x11vnc \
  xserver-xorg-core \
  xserver-xorg-video-dummy \
  xvfb \
  xterm

locale-gen en_US.UTF-8 >/dev/null 2>&1 || true
update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 >/dev/null 2>&1 || true
ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime
echo "${TZ}" >/etc/timezone

mkdir -p /var/lib/sentinel
id -u sentinel >/dev/null 2>&1 || useradd -m -s /bin/bash sentinel
mkdir -p "${DEFAULT_WORKSPACE_DIR}" "${SESSIONS_ROOT}"
chown -R sentinel:sentinel "${SENTINEL_HOME}" "${DEFAULT_WORKSPACE_DIR}"
chmod 0755 "${SENTINEL_ROOT}"
chmod 0711 "${SESSIONS_ROOT}"

cat > /usr/local/bin/sentinel-session-prepare.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 --session-id <uuidish> [--workspace-source <path>] [--workspace-target <path>] [--reset]" >&2
  exit 1
}

SESSION_ID=""
WORKSPACE_SOURCE=""
WORKSPACE_TARGET=""
RESET=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session-id)
      SESSION_ID="${2:-}"
      shift 2
      ;;
    --workspace-source)
      WORKSPACE_SOURCE="${2:-}"
      shift 2
      ;;
    --workspace-target)
      WORKSPACE_TARGET="${2:-}"
      shift 2
      ;;
    --reset)
      RESET=1
      shift
      ;;
    *)
      usage
      ;;
  esac
done

[[ -n "${SESSION_ID}" ]] || usage

SLUG="$(printf '%s' "${SESSION_ID}" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9' | cut -c1-24)"
[[ -n "${SLUG}" ]] || { echo "invalid session id" >&2; exit 1; }

SESSION_USER="ssn-${SLUG}"
SESSION_ROOT="/srv/sentinel/sessions/${SESSION_ID}"
SESSION_HOME="${SESSION_ROOT}/home"
SESSION_WORKSPACE="${SESSION_ROOT}/workspace"
SESSION_PROFILE="${SESSION_ROOT}/browser-profile"
SESSION_RUNTIME_DIR="${SESSION_ROOT}/runtime"

if [[ -n "${WORKSPACE_TARGET}" ]]; then
  SESSION_WORKSPACE="${WORKSPACE_TARGET}"
fi

if id -u "${SESSION_USER}" >/dev/null 2>&1; then
  if [[ "${RESET}" -eq 1 ]]; then
    pkill -u "${SESSION_USER}" >/dev/null 2>&1 || true
  fi
else
  useradd -M -d "${SESSION_HOME}" -s /bin/bash "${SESSION_USER}"
fi

mkdir -p "${SESSION_ROOT}" "${SESSION_HOME}" "${SESSION_PROFILE}" "${SESSION_RUNTIME_DIR}" "${SESSION_WORKSPACE}"
chown "${SESSION_USER}:${SESSION_USER}" "${SESSION_ROOT}" "${SESSION_HOME}" "${SESSION_PROFILE}" "${SESSION_RUNTIME_DIR}" >/dev/null 2>&1 || true
chmod 0700 "${SESSION_ROOT}" "${SESSION_HOME}" "${SESSION_PROFILE}" "${SESSION_RUNTIME_DIR}"

if [[ -n "${WORKSPACE_SOURCE}" ]]; then
  mkdir -p "${WORKSPACE_SOURCE}" "${SESSION_WORKSPACE}"
  if mountpoint -q "${SESSION_WORKSPACE}"; then
    CURRENT_SOURCE="$(findmnt -n -o SOURCE --target "${SESSION_WORKSPACE}" 2>/dev/null || true)"
    if [[ "${CURRENT_SOURCE}" != "${WORKSPACE_SOURCE}" ]]; then
      if [[ "${RESET}" -eq 1 ]]; then
        pkill -u "${SESSION_USER}" >/dev/null 2>&1 || true
        umount -l "${SESSION_WORKSPACE}"
      else
        echo "SESSION_ID=${SESSION_ID}"
        echo "SESSION_USER=${SESSION_USER}"
        echo "SESSION_ROOT=${SESSION_ROOT}"
        echo "SESSION_HOME=${SESSION_HOME}"
        echo "SESSION_WORKSPACE=${SESSION_WORKSPACE}"
        echo "SESSION_PROFILE=${SESSION_PROFILE}"
        echo "SESSION_RUNTIME_DIR=${SESSION_RUNTIME_DIR}"
        exit 0
      fi
    fi
  fi
  if ! mountpoint -q "${SESSION_WORKSPACE}"; then
    mount --bind "${WORKSPACE_SOURCE}" "${SESSION_WORKSPACE}"
  fi
fi

chown "${SESSION_USER}:${SESSION_USER}" "${SESSION_WORKSPACE}" >/dev/null 2>&1 || true
chmod 0700 "${SESSION_WORKSPACE}" >/dev/null 2>&1 || true

cat <<OUT
SESSION_ID=${SESSION_ID}
SESSION_USER=${SESSION_USER}
SESSION_ROOT=${SESSION_ROOT}
SESSION_HOME=${SESSION_HOME}
SESSION_WORKSPACE=${SESSION_WORKSPACE}
SESSION_PROFILE=${SESSION_PROFILE}
SESSION_RUNTIME_DIR=${SESSION_RUNTIME_DIR}
OUT
EOF

cat > /usr/local/bin/sentinel-session-cleanup.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 2 && "$1" == "--session-id" ]] || {
  echo "usage: $0 --session-id <uuidish>" >&2
  exit 1
}

SESSION_ID="$2"
SLUG="$(printf '%s' "${SESSION_ID}" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9' | cut -c1-24)"
[[ -n "${SLUG}" ]] || { echo "invalid session id" >&2; exit 1; }

SESSION_USER="ssn-${SLUG}"
SESSION_ROOT="/srv/sentinel/sessions/${SESSION_ID}"
SESSION_WORKSPACE="${SESSION_ROOT}/workspace"

pkill -u "${SESSION_USER}" >/dev/null 2>&1 || true
if mountpoint -q "${SESSION_WORKSPACE}"; then
  umount "${SESSION_WORKSPACE}" || true
fi
userdel "${SESSION_USER}" >/dev/null 2>&1 || true
rm -rf "${SESSION_ROOT}"
EOF

chmod 0755 /usr/local/bin/sentinel-session-prepare.sh /usr/local/bin/sentinel-session-cleanup.sh

cat > "${START_SCRIPT}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SESSION_USER="${SENTINEL_DESKTOP_USER:-sentinel}"
USER_HOME="$(getent passwd "${SESSION_USER}" | cut -d: -f6)"
if [[ -z "${USER_HOME}" ]]; then
  echo "desktop user ${SESSION_USER} not found" >&2
  exit 1
fi

WORKSPACE_DIR="${SENTINEL_DESKTOP_WORKSPACE:-/srv/sentinel/default-workspace}"
mkdir -p "${WORKSPACE_DIR}"
chown "${SESSION_USER}:${SESSION_USER}" "${WORKSPACE_DIR}" >/dev/null 2>&1 || true

mkdir -p /run/dbus
dbus-daemon --system --fork 2>/dev/null || true

XDG_RT="${SENTINEL_DESKTOP_RUNTIME_DIR:-/tmp/runtime-${SESSION_USER}}"
mkdir -p "${XDG_RT}"
chown "${SESSION_USER}:${SESSION_USER}" "${XDG_RT}"
chmod 700 "${XDG_RT}"

RESOLUTION="${RUNTIME_RESOLUTION:-1920x1080x24}"
export DISPLAY=:99
WIDTH="${RESOLUTION%x*}"
HEIGHT_DEPTH="${RESOLUTION#*x}"
HEIGHT="${HEIGHT_DEPTH%x*}"
DEPTH="${RESOLUTION##*x}"
if [[ -z "${WIDTH}" || -z "${HEIGHT}" || -z "${DEPTH}" ]]; then
  WIDTH=1920
  HEIGHT=1080
  DEPTH=24
fi

pkill -f "Xorg :99" >/dev/null 2>&1 || true
pkill -f "Xvfb :99" >/dev/null 2>&1 || true
pkill -u "${SESSION_USER}" -x openbox >/dev/null 2>&1 || true
pkill -u "${SESSION_USER}" -x xterm >/dev/null 2>&1 || true
pkill -u "${SESSION_USER}" -x konsole >/dev/null 2>&1 || true
pkill -u "${SESSION_USER}" -x plasmashell >/dev/null 2>&1 || true
pkill -u "${SESSION_USER}" -x kwin_x11 >/dev/null 2>&1 || true
pkill -u "${SESSION_USER}" -x startplasma-x11 >/dev/null 2>&1 || true
pkill -f "websockify .*6080" >/dev/null 2>&1 || true
pkill -f "x11vnc -display :99" >/dev/null 2>&1 || true

mkdir -p /etc/X11
cat > /etc/X11/xorg-dummy.conf <<XORG
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
    DefaultDepth ${DEPTH}
    SubSection "Display"
        Depth ${DEPTH}
        Modes "1920x1080"
        Virtual ${WIDTH} ${HEIGHT}
    EndSubSection
EndSection
XORG

if command -v Xorg >/dev/null 2>&1; then
  Xorg "${DISPLAY}" -noreset -nolisten tcp -config /etc/X11/xorg-dummy.conf +extension GLX +extension RANDR +extension RENDER &
  X_PID=$!
  sleep 2
  if ! kill -0 "${X_PID}" 2>/dev/null; then
    Xvfb "${DISPLAY}" -screen 0 "${RESOLUTION}" -ac +extension RANDR -nolisten tcp &
    sleep 1
  fi
else
  Xvfb "${DISPLAY}" -screen 0 "${RESOLUTION}" -ac +extension RANDR -nolisten tcp &
  sleep 1
fi

SESSION_CMD="openbox-session"
if command -v startplasma-x11 >/dev/null 2>&1; then
  SESSION_CMD="dbus-launch --exit-with-session startplasma-x11"
fi

runuser -u "${SESSION_USER}" -- mkdir -p "${USER_HOME}/.config"
runuser -u "${SESSION_USER}" -- bash -lc "cat > ~/.config/kscreenlockerrc <<'LOCK'
[Daemon]
Autolock=false
LockOnResume=false
Timeout=0
[Greeter]
Theme=
LOCK"
runuser -u "${SESSION_USER}" -- bash -lc "cat > ~/.config/powerdevilrc <<'PWR'
[AC][DimDisplay]
idleTime=0
[AC][DPMSControl]
idleTime=0
[AC][HandleButtonEvents]
lidAction=0
[AC][SuspendSession]
idleTime=0
suspendThenHibernate=false
suspendType=0
PWR"
runuser -u "${SESSION_USER}" -- bash -lc "cat > ~/.config/kwalletrc <<'WALLET'
[Wallet]
Enabled=false
First Use=false
WALLET"

runuser -u "${SESSION_USER}" -- env DISPLAY="${DISPLAY}" XDG_RUNTIME_DIR="${XDG_RT}" QT_X11_NO_MITSHM=1 bash -lc "${SESSION_CMD}" &
SESSION_PID=$!
sleep 6
if ! kill -0 "${SESSION_PID}" 2>/dev/null; then
  runuser -u "${SESSION_USER}" -- env DISPLAY="${DISPLAY}" XDG_RUNTIME_DIR="${XDG_RT}" openbox-session &
  SESSION_PID=$!
  sleep 2
fi

xset -display "${DISPLAY}" s off -dpms >/dev/null 2>&1 || true

# Session-owned terminals are launched by the runtime provider on activation.

x11vnc -display "${DISPLAY}" -forever -shared -rfbport 5900 -nopw -localhost -xkb -noxdamage &
X11VNC_PID=$!
sleep 1
websockify --web=/usr/share/novnc 0.0.0.0:6080 localhost:5900 &
WEBSOCKIFY_PID=$!

while true; do
  kill -0 "${X11VNC_PID}" 2>/dev/null || exit 1
  kill -0 "${WEBSOCKIFY_PID}" 2>/dev/null || exit 1
  sleep 2
done
EOF

cat > "${DESKTOP_UNIT}" <<'EOF'
[Unit]
Description=Sentinel Runtime Desktop
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=TZ=America/Los_Angeles
Environment=RUNTIME_RESOLUTION=1920x1080x24
ExecStart=/usr/local/bin/sentinel-runtime-start.sh
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

chmod 0755 "${START_SCRIPT}"
chmod 0644 "${DESKTOP_UNIT}"
chown root:root "${START_SCRIPT}" "${DESKTOP_UNIT}"

sudo -u sentinel -H env HOME="${SENTINEL_HOME}" PATH="${SENTINEL_HOME}/.local/bin:${PATH}" \
  python3 -m pip install --break-system-packages --user playwright
sudo -u sentinel -H env HOME="${SENTINEL_HOME}" PATH="${SENTINEL_HOME}/.local/bin:${PATH}" \
  python3 -m playwright install chromium

PLAYWRIGHT_CHROME="$(sudo -u sentinel -H bash -lc 'find /home/sentinel/.cache/ms-playwright -maxdepth 4 -type f -path "*/chrome-linux/chrome" | head -n 1')"
if [ -z "${PLAYWRIGHT_CHROME}" ]; then
  echo "Could not locate Playwright Chromium binary" >&2
  exit 1
fi
PLAYWRIGHT_ROOT="$(dirname "${PLAYWRIGHT_CHROME}")"
rm -rf "${BROWSER_ROOT}"
mkdir -p "$(dirname "${BROWSER_ROOT}")"
mv "${PLAYWRIGHT_ROOT}" "${BROWSER_ROOT}"
chown -R root:root "${BROWSER_ROOT}"
test -x "${BROWSER_BIN}"
test -s "${BROWSER_SANDBOX}"
chmod 4755 "${BROWSER_SANDBOX}"
sudo -u sentinel -H bash -lc 'rm -rf /home/sentinel/.cache/ms-playwright /home/sentinel/.cache/pip'

cat > "${BROWSER_SCRIPT}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SESSION_USER="${SENTINEL_BROWSER_USER:-sentinel}"
USER_HOME="$(getent passwd "${SESSION_USER}" | cut -d: -f6)"
if [[ -z "${USER_HOME}" ]]; then
  echo "browser user ${SESSION_USER} not found" >&2
  exit 1
fi

XDG_RT="${SENTINEL_BROWSER_RUNTIME_DIR:-/tmp/runtime-${SESSION_USER}}"
PROFILE_DIR="${SENTINEL_BROWSER_PROFILE:-${USER_HOME}/.config/chromium}"
DISPLAY_VALUE="${SENTINEL_BROWSER_DISPLAY:-:99}"

mkdir -p "${XDG_RT}"
chown "${SESSION_USER}:${SESSION_USER}" "${XDG_RT}" >/dev/null 2>&1 || true
chmod 700 "${XDG_RT}" >/dev/null 2>&1 || true
pkill -u "${SESSION_USER}" -f /opt/google/chrome/chrome >/dev/null 2>&1 || true
pkill -f "socat TCP-LISTEN:9223" >/dev/null 2>&1 || true
rm -f "${PROFILE_DIR}/SingletonLock" \
      "${PROFILE_DIR}/SingletonSocket" \
      "${PROFILE_DIR}/SingletonCookie" 2>/dev/null || true
runuser -u "${SESSION_USER}" -- mkdir -p "${PROFILE_DIR}" "${XDG_RT}"

socat TCP-LISTEN:9223,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9222 >/tmp/chromium-socat.log 2>&1 &
SOCAT_PID=$!

runuser -u "${SESSION_USER}" -- env \
  DISPLAY="${DISPLAY_VALUE}" \
  XDG_RUNTIME_DIR="${XDG_RT}" \
  CHROME_DEVEL_SANDBOX=/opt/google/chrome/chrome_sandbox \
  MESA_LOADER_DRIVER_OVERRIDE=llvmpipe \
  /opt/google/chrome/chrome \
  --disable-dev-shm-usage \
  --use-gl=angle \
  --use-angle=gl \
  --ignore-gpu-blocklist \
  --disable-gpu-driver-bug-workaround \
  --remote-debugging-port=9222 \
  --user-data-dir="${PROFILE_DIR}" \
  --no-first-run \
  --no-default-browser-check \
  --window-size=1920,1080 \
  about:blank >/tmp/chromium-reset.log 2>&1 &
CHROME_PID=$!

trap 'kill "${SOCAT_PID}" "${CHROME_PID}" >/dev/null 2>&1 || true' EXIT
while true; do
  kill -0 "${SOCAT_PID}" 2>/dev/null || exit 1
  kill -0 "${CHROME_PID}" 2>/dev/null || exit 1
  sleep 2
done
EOF

cat > "${BROWSER_UNIT}" <<'EOF'
[Unit]
Description=Sentinel Runtime Browser
After=network-online.target sentinel-runtime-desktop.service
Wants=network-online.target sentinel-runtime-desktop.service

[Service]
Type=simple
ExecStart=/usr/local/bin/sentinel-runtime-browser.sh
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

chmod 0755 "${BROWSER_SCRIPT}"
chmod 0644 "${BROWSER_UNIT}"
chown root:root "${BROWSER_SCRIPT}" "${BROWSER_UNIT}"

systemctl daemon-reload
systemctl enable sentinel-runtime-desktop.service sentinel-runtime-browser.service
systemctl restart sentinel-runtime-desktop.service
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:6080/vnc.html >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
systemctl restart sentinel-runtime-browser.service
for _ in $(seq 1 120); do
  if curl -fsS http://127.0.0.1:9223/json/version >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

touch "${RUNTIME_MARKER}"
touch "${BROWSER_MARKER}"

apt-get clean
rm -rf /var/lib/apt/lists/*

# The baked image is not supposed to depend on external metadata services on
# subsequent boots. Freeze the cloud-init state after the initial bake.
mkdir -p /etc/cloud/cloud.cfg.d
touch /etc/cloud/cloud-init.disabled
EOF
