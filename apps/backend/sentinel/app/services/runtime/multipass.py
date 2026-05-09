from __future__ import annotations

import asyncio
import logging
import os
import textwrap
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import httpx

from app.config import settings
from app.services.runtime.base import (
    RuntimeCommandClient,
    RuntimeExecResult,
    RuntimeInstance,
    RuntimeProviderInfo,
    RuntimeProviderInfoItem,
)
from app.services.runtime.playwright_runtime import DEFAULT_BROWSER_LOCALE, DEFAULT_BROWSER_TIMEZONE_ID

logger = logging.getLogger(__name__)

_NAME_PREFIX = "sentinel-runtime-"
_GUEST_WORKSPACE = "/home/sentinel/workspace"
_PROVISION_MARKER = "/var/lib/sentinel/runtime-provisioned-v1"
_PROVISION_LOCK_FILE = "/var/lib/sentinel/runtime-provision.lock"
_GUEST_START_SCRIPT = "/usr/local/bin/sentinel-runtime-start.sh"
_GUEST_SERVICE_UNIT = "/etc/systemd/system/sentinel-runtime-desktop.service"
_BROWSER_MARKER = "/var/lib/sentinel/browser-provisioned-v1"
_BROWSER_LOCK_FILE = "/var/lib/sentinel/browser-provision.lock"
_BROWSER_ROOT = "/opt/google/chrome"
_BROWSER_BIN = f"{_BROWSER_ROOT}/chrome"
_BROWSER_SANDBOX = f"{_BROWSER_ROOT}/chrome_sandbox"
_GUEST_BROWSER_SCRIPT = "/usr/local/bin/sentinel-runtime-browser.sh"
_GUEST_BROWSER_SERVICE_UNIT = "/etc/systemd/system/sentinel-runtime-browser.service"
_MULTIPASS_LAUNCH_TIMEOUT_SECONDS = 60


@dataclass(frozen=True, slots=True)
class MultipassLaunchProfile:
    image: str
    cpus: int
    memory: str
    disk: str
    workspace_root: str
    mount_mode: str


class MultipassBridgeError(RuntimeError):
    pass


def _instance_name(session_id: UUID | str) -> str:
    return f"{_NAME_PREFIX}{str(session_id)[:12]}"


def _session_workspace_root(session_id: UUID | str, workspace_root: str) -> str:
    return os.path.join(workspace_root, str(session_id), "workspace")


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _instance_state(info: dict[str, object] | None) -> str:
    if not isinstance(info, dict):
        return ""
    return str(info.get("state") or "").strip().lower()


def _build_exec_script(
    command: str,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    as_root: bool = False,
) -> str:
    parts: list[str] = []
    if env:
        for key, value in env.items():
            parts.append(f"export {key}={_shell_quote(value)};")
    if cwd:
        parts.append(f"cd {_shell_quote(cwd)} &&")
    parts.append(command)
    script = " ".join(parts)
    if as_root:
        return f"sudo bash -lc {_shell_quote(script)}"
    return f"sudo -u sentinel bash -lc {_shell_quote(script)}"


def _runtime_install_script() -> str:
    return textwrap.dedent(
        """
        set -euo pipefail
        export DEBIAN_FRONTEND=noninteractive
        APT_GET="sudo env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600"
        wait_for_package_manager() {
          local tries=0
          while pgrep -x apt >/dev/null 2>&1 || \
                pgrep -x apt-get >/dev/null 2>&1 || \
                pgrep -x dpkg >/dev/null 2>&1; do
            tries=$((tries + 1))
            if [ "$tries" -ge 600 ]; then
              echo "Timed out waiting for package manager readiness" >&2
              return 1
            fi
            sleep 1
          done
        }
        sudo mkdir -p /var/lib/sentinel
        wait_for_package_manager
        $APT_GET update
        wait_for_package_manager
        $APT_GET install -y --no-install-recommends ca-certificates curl gpg
        wait_for_package_manager
        $APT_GET update
        wait_for_package_manager
        $APT_GET install -y --no-install-recommends \\
          openbox \\
          xterm \\
          konsole \\
          plasma-desktop \\
          plasma-workspace \\
          kwin-x11 \\
          dbus-x11 \\
          at-spi2-core \\
          novnc \\
          websockify \\
          x11vnc \\
          xvfb \\
          xserver-xorg-core \\
          xserver-xorg-video-dummy \\
          mesa-utils \\
          tzdata \\
          socat \\
          build-essential \\
          git \\
          htop \\
          jq \\
          net-tools \\
          procps \\
          python3 \\
          python3-pip \\
          python3-venv \\
          ripgrep \\
          sudo \\
          tree \\
          wget
        $APT_GET clean
        sudo rm -rf /var/lib/apt/lists/*
        sudo touch """ + _PROVISION_MARKER + """
        """
    ).strip()


def _runtime_provision_script() -> str:
    write_runtime_files = _write_runtime_files_script()
    return (
        "set -euo pipefail\n"
        "mkdir -p /var/lib/sentinel\n"
        f"if test -f {_shell_quote(_PROVISION_MARKER)}; then\n"
        f"{write_runtime_files}\n"
        "  exit 0\n"
        "fi\n"
        f"if test -d {_shell_quote(_PROVISION_LOCK_FILE)}; then\n"
        f"  rmdir {_shell_quote(_PROVISION_LOCK_FILE)} >/dev/null 2>&1 || rm -rf {_shell_quote(_PROVISION_LOCK_FILE)}\n"
        "fi\n"
        f"exec 9>>{_shell_quote(_PROVISION_LOCK_FILE)}\n"
        "if ! flock -w 1800 9; then\n"
        '  echo "Timed out waiting for runtime provision lock" >&2\n'
        "  exit 1\n"
        "fi\n"
        "trap 'flock -u 9 >/dev/null 2>&1 || true' EXIT\n"
        f"if test -f {_shell_quote(_PROVISION_MARKER)}; then\n"
        f"{write_runtime_files}\n"
        "  exit 0\n"
        "fi\n"
        f"{_runtime_install_script()}\n"
        f"{write_runtime_files}"
    )


def _write_runtime_files_script() -> str:
    start_script = _runtime_start_script().rstrip("\n")
    service_unit = _runtime_service_unit().rstrip("\n")
    return (
        f"cat > {_shell_quote(_GUEST_START_SCRIPT)} <<'SENTINEL_RUNTIME_START'\n"
        f"{start_script}\n"
        "SENTINEL_RUNTIME_START\n"
        f"chmod 0755 {_shell_quote(_GUEST_START_SCRIPT)}\n"
        f"chown root:root {_shell_quote(_GUEST_START_SCRIPT)}\n"
        f"cat > {_shell_quote(_GUEST_SERVICE_UNIT)} <<'SENTINEL_RUNTIME_SERVICE'\n"
        f"{service_unit}\n"
        "SENTINEL_RUNTIME_SERVICE\n"
        f"chmod 0644 {_shell_quote(_GUEST_SERVICE_UNIT)}\n"
        f"chown root:root {_shell_quote(_GUEST_SERVICE_UNIT)}\n"
        "systemctl daemon-reload"
    )


def _runtime_start_script() -> str:
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        mkdir -p {_GUEST_WORKSPACE}
        chown sentinel:sentinel {_GUEST_WORKSPACE} >/dev/null 2>&1 || true

        mkdir -p /run/dbus
        dbus-daemon --system --fork 2>/dev/null || true

        XDG_RT="/tmp/runtime-sentinel"
        mkdir -p "$XDG_RT"
        chown sentinel:sentinel "$XDG_RT"
        chmod 700 "$XDG_RT"

        RESOLUTION="${{RUNTIME_RESOLUTION:-1920x1080x24}}"
        export DISPLAY=:99
        export TZ="${{BROWSER_TIMEZONE_ID:-${{TZ:-America/Los_Angeles}}}}"
        WIDTH="${{RESOLUTION%x*}}"
        HEIGHT_DEPTH="${{RESOLUTION#*x}}"
        HEIGHT="${{HEIGHT_DEPTH%x*}}"
        DEPTH="${{RESOLUTION##*x}}"
        if [[ -z "$WIDTH" || -z "$HEIGHT" || -z "$DEPTH" ]]; then
            WIDTH=1920
            HEIGHT=1080
            DEPTH=24
        fi

        pkill -f "Xorg :99" >/dev/null 2>&1 || true
        pkill -f "Xvfb :99" >/dev/null 2>&1 || true
        pkill -u sentinel -x openbox >/dev/null 2>&1 || true
        pkill -u sentinel -x xterm >/dev/null 2>&1 || true
        pkill -u sentinel -x konsole >/dev/null 2>&1 || true
        pkill -u sentinel -x plasmashell >/dev/null 2>&1 || true
        pkill -u sentinel -x kwin_x11 >/dev/null 2>&1 || true
        pkill -u sentinel -x startplasma-x11 >/dev/null 2>&1 || true
        pkill -f "websockify .*6080" >/dev/null 2>&1 || true
        pkill -f "x11vnc -display :99" >/dev/null 2>&1 || true

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

        SESSION_CMD=""
        if command -v startplasma-x11 >/dev/null 2>&1; then
            SESSION_CMD="dbus-launch --exit-with-session startplasma-x11"
        elif command -v startplasma-wayland >/dev/null 2>&1; then
            SESSION_CMD="dbus-launch --exit-with-session startplasma-wayland"
        else
            SESSION_CMD="openbox-session"
        fi

        su - sentinel -c "mkdir -p ~/.config ~/.config/kdedefaults"
        su - sentinel -c "cat > ~/.config/kscreenlockerrc <<'EOF'
        [Daemon]
        Autolock=false
        LockOnResume=false
        Timeout=0

        [Greeter]
        Theme=
        EOF"
        su - sentinel -c "cat > ~/.config/powerdevilrc <<'EOF'
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
        EOF"
        su - sentinel -c "cat > ~/.config/kwalletrc <<'EOF'
        [Wallet]
        Enabled=false
        First Use=false
        EOF"

        su - sentinel -c "DISPLAY=$DISPLAY XDG_RUNTIME_DIR=$XDG_RT QT_X11_NO_MITSHM=1 $SESSION_CMD" &
        SESSION_PID=$!
        sleep 6

        if ! kill -0 "$SESSION_PID" 2>/dev/null; then
            su - sentinel -c "DISPLAY=$DISPLAY XDG_RUNTIME_DIR=$XDG_RT openbox-session" &
            SESSION_PID=$!
            sleep 2
        fi

        xset -display "$DISPLAY" s off -dpms >/dev/null 2>&1 || true

        if command -v konsole >/dev/null 2>&1; then
            su - sentinel -c "DISPLAY=$DISPLAY XDG_RUNTIME_DIR=$XDG_RT konsole --workdir {_GUEST_WORKSPACE} --hold -e /bin/bash -lc \\\"printf 'Sentinel runtime ready\\\\n'; exec bash\\\"" &
        else
            su - sentinel -c "DISPLAY=$DISPLAY XDG_RUNTIME_DIR=$XDG_RT xterm -geometry 120x36+60+60 -fa Monospace -fs 11 -title Sentinel" &
        fi

        x11vnc -display "$DISPLAY" -forever -shared -rfbport 5900 -nopw -localhost -xkb -noxdamage &
        X11VNC_PID=$!
        sleep 1

        NOVNC_WEB="${{NOVNC_WEB:-/usr/share/novnc}}"
        websockify --web="$NOVNC_WEB" 0.0.0.0:6080 localhost:5900 &
        WEBSOCKIFY_PID=$!

        while true; do
            kill -0 "$X11VNC_PID" 2>/dev/null || exit 1
            kill -0 "$WEBSOCKIFY_PID" 2>/dev/null || exit 1
            sleep 2
        done
        """
    ).strip()


def _browser_install_script() -> str:
    write_browser_files = _write_browser_files_script()
    return textwrap.dedent(
        f"""\
        set -euo pipefail
        mkdir -p /var/lib/sentinel
        if test -f {_shell_quote(_BROWSER_MARKER)} && test -x {_shell_quote(_BROWSER_BIN)} && test -s {_shell_quote(_BROWSER_SANDBOX)}; then
            {write_browser_files}
            exit 0
        fi
        if test -d {_shell_quote(_BROWSER_LOCK_FILE)}; then
            rmdir {_shell_quote(_BROWSER_LOCK_FILE)} >/dev/null 2>&1 || rm -rf {_shell_quote(_BROWSER_LOCK_FILE)}
        fi
        exec 9>>{_shell_quote(_BROWSER_LOCK_FILE)}
        if ! flock -w 1800 9; then
            echo "Timed out waiting for browser provision lock" >&2
            exit 1
        fi
        trap 'flock -u 9 >/dev/null 2>&1 || true' EXIT
        if test -f {_shell_quote(_BROWSER_MARKER)} && test -x {_shell_quote(_BROWSER_BIN)} && test -s {_shell_quote(_BROWSER_SANDBOX)}; then
            {write_browser_files}
            exit 0
        fi
        rm -f {_shell_quote(_BROWSER_MARKER)}
        rm -rf {_shell_quote(_BROWSER_ROOT)}
        sudo -u sentinel -H env HOME=/home/sentinel PATH=/home/sentinel/.local/bin:$PATH \
            python3 -m pip install --break-system-packages --user playwright
        sudo -u sentinel -H env HOME=/home/sentinel PATH=/home/sentinel/.local/bin:$PATH \
            python3 -m playwright install chromium
        PLAYWRIGHT_CHROME="$(sudo -u sentinel -H bash -lc 'find /home/sentinel/.cache/ms-playwright -maxdepth 4 -type f -path \"*/chrome-linux/chrome\" | head -n 1')"
        if [ -z "$PLAYWRIGHT_CHROME" ]; then
            echo "Could not locate Playwright Chromium binary" >&2
            exit 1
        fi
        PLAYWRIGHT_ROOT="$(dirname "$PLAYWRIGHT_CHROME")"
        mkdir -p {_shell_quote(str(Path(_BROWSER_ROOT).parent))}
        mv "$PLAYWRIGHT_ROOT" {_shell_quote(_BROWSER_ROOT)}
        chown -R root:root {_shell_quote(_BROWSER_ROOT)}
        test -x {_shell_quote(_BROWSER_BIN)}
        test -s {_shell_quote(_BROWSER_SANDBOX)}
        chmod 4755 {_shell_quote(_BROWSER_SANDBOX)}
        sudo -u sentinel -H bash -lc 'rm -rf /home/sentinel/.cache/ms-playwright /home/sentinel/.cache/pip'
        touch {_shell_quote(_BROWSER_MARKER)}
        {write_browser_files}
        """
    ).strip()


def _write_browser_files_script() -> str:
    browser_script = _browser_start_script().rstrip("\n")
    browser_unit = _browser_service_unit().rstrip("\n")
    return (
        f"cat > {_shell_quote(_GUEST_BROWSER_SCRIPT)} <<'SENTINEL_RUNTIME_BROWSER'\n"
        f"{browser_script}\n"
        "SENTINEL_RUNTIME_BROWSER\n"
        f"chmod 0755 {_shell_quote(_GUEST_BROWSER_SCRIPT)}\n"
        f"chown root:root {_shell_quote(_GUEST_BROWSER_SCRIPT)}\n"
        f"cat > {_shell_quote(_GUEST_BROWSER_SERVICE_UNIT)} <<'SENTINEL_RUNTIME_BROWSER_SERVICE'\n"
        f"{browser_unit}\n"
        "SENTINEL_RUNTIME_BROWSER_SERVICE\n"
        f"chmod 0644 {_shell_quote(_GUEST_BROWSER_SERVICE_UNIT)}\n"
        f"chown root:root {_shell_quote(_GUEST_BROWSER_SERVICE_UNIT)}\n"
        "systemctl daemon-reload"
    )


def _browser_start_script() -> str:
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        mkdir -p /tmp/runtime-sentinel
        chown sentinel:sentinel /tmp/runtime-sentinel >/dev/null 2>&1 || true
        chmod 700 /tmp/runtime-sentinel >/dev/null 2>&1 || true
        pkill -u sentinel -f {_shell_quote(_BROWSER_BIN)} >/dev/null 2>&1 || true
        pkill -f "socat TCP-LISTEN:9223" >/dev/null 2>&1 || true
        rm -f /home/sentinel/.config/chromium/SingletonLock \
              /home/sentinel/.config/chromium/SingletonSocket \
              /home/sentinel/.config/chromium/SingletonCookie 2>/dev/null || true
        runuser -u sentinel -- mkdir -p /home/sentinel/.config/chromium /tmp/runtime-sentinel
        chmod 700 /tmp/runtime-sentinel >/dev/null 2>&1 || true

        socat TCP-LISTEN:9223,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9222 \
            >/tmp/chromium-socat.log 2>&1 &
        SOCAT_PID=$!

        runuser -u sentinel -- env \
            DISPLAY=:99 \
            XDG_RUNTIME_DIR=/tmp/runtime-sentinel \
            CHROME_DEVEL_SANDBOX={_BROWSER_SANDBOX} \
            MESA_LOADER_DRIVER_OVERRIDE=llvmpipe \
            {_BROWSER_BIN} \
            --disable-dev-shm-usage \
            --use-gl=angle \
            --use-angle=gl \
            --ignore-gpu-blocklist \
            --disable-gpu-driver-bug-workaround \
            --remote-debugging-port=9222 \
            --user-data-dir=/home/sentinel/.config/chromium \
            --no-first-run \
            --no-default-browser-check \
            --window-size=1920,1080 \
            about:blank >/tmp/chromium-reset.log 2>&1 &
        CHROME_PID=$!

        trap 'kill "$SOCAT_PID" "$CHROME_PID" >/dev/null 2>&1 || true' EXIT

        while true; do
            kill -0 "$SOCAT_PID" 2>/dev/null || exit 1
            kill -0 "$CHROME_PID" 2>/dev/null || exit 1
            sleep 2
        done
        """
    ).strip()


def _browser_restart_script() -> str:
    return (
        "set -euo pipefail\n"
        "systemctl daemon-reload\n"
        "systemctl enable sentinel-runtime-browser.service >/dev/null 2>&1 || true\n"
        "systemctl restart sentinel-runtime-browser.service\n"
    )


def _browser_ready_check_script() -> str:
    return (
        "for i in $(seq 1 60); do "
        "if curl -fsS http://127.0.0.1:9223/json/version >/dev/null 2>&1; then exit 0; fi; "
        "sleep 0.5; "
        "done; "
        "systemctl status sentinel-runtime-browser.service --no-pager || true; "
        "exit 1"
    )


def _runtime_service_unit() -> str:
    browser_locale = os.getenv("BROWSER_LOCALE", "").strip() or DEFAULT_BROWSER_LOCALE
    browser_timezone = os.getenv("BROWSER_TIMEZONE_ID", "").strip() or DEFAULT_BROWSER_TIMEZONE_ID
    browser_user_agent = os.getenv("BROWSER_USER_AGENT", "").strip()
    return textwrap.dedent(
        f"""\
        [Unit]
        Description=Sentinel Runtime Desktop
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        Environment=BROWSER_LOCALE={browser_locale}
        Environment=BROWSER_TIMEZONE_ID={browser_timezone}
        Environment=TZ={browser_timezone}
        Environment=RUNTIME_RESOLUTION=1920x1080x24
        {"Environment=BROWSER_USER_AGENT=" + browser_user_agent if browser_user_agent else ""}
        ExecStart={_GUEST_START_SCRIPT}
        Restart=always
        RestartSec=2

        [Install]
        WantedBy=multi-user.target
        """
    ).strip() + "\n"


def _browser_service_unit() -> str:
    return textwrap.dedent(
        f"""\
        [Unit]
        Description=Sentinel Runtime Browser
        After=network-online.target sentinel-runtime-desktop.service
        Wants=network-online.target sentinel-runtime-desktop.service

        [Service]
        Type=simple
        ExecStart={_GUEST_BROWSER_SCRIPT}
        Restart=always
        RestartSec=2

        [Install]
        WantedBy=multi-user.target
        """
    ).strip() + "\n"


class MultipassCommandClient(RuntimeCommandClient):
    def __init__(
        self,
        *,
        provider: "MultipassRuntimeProvider",
        instance_name: str,
        host_workspace: str,
        mount_mode: str,
    ) -> None:
        self._provider = provider
        self._instance_name = instance_name
        self._host_workspace = host_workspace
        self._mount_mode = mount_mode

    async def wait_ready(self, *, timeout: int = 60) -> None:
        await self._provider._wait_for_instance_ready(self._instance_name, timeout=timeout)

    async def run(
        self,
        command: str,
        *,
        timeout: int = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        as_root: bool = False,
    ) -> RuntimeExecResult:
        if self._mount_mode == "transfer":
            await self._provider._sync_host_to_guest(self._instance_name, self._host_workspace)
        script = _build_exec_script(command, cwd=cwd, env=env, as_root=as_root)
        try:
            payload = await self._provider._bridge_command(
                [
                    "multipass",
                    "exec",
                    self._instance_name,
                    "--",
                    "bash",
                    "-lc",
                    script,
                ],
                timeout_seconds=timeout,
            )
        finally:
            if self._mount_mode == "transfer":
                await self._provider._sync_guest_to_host(self._instance_name, self._host_workspace)
        return RuntimeExecResult(
            exit_status=payload.get("returncode"),
            stdout=str(payload.get("stdout") or ""),
            stderr=str(payload.get("stderr") or ""),
        )

    async def run_detached(
        self,
        command: str,
        *,
        stdout_path: str,
        stderr_path: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        as_root: bool = False,
    ) -> int:
        if self._mount_mode == "transfer":
            await self._provider._sync_host_to_guest(self._instance_name, self._host_workspace)
        script = _build_exec_script(
            f"setsid nohup bash -lc {_shell_quote(command)} </dev/null > {_shell_quote(stdout_path)} 2> {_shell_quote(stderr_path)} & echo $!",
            cwd=cwd,
            env=env,
            as_root=as_root,
        )
        payload = await self._provider._bridge_command(
            [
                "multipass",
                "exec",
                self._instance_name,
                "--",
                "bash",
                "-lc",
                script,
            ],
            timeout_seconds=30,
        )
        if payload.get("returncode") != 0:
            raise RuntimeError(str(payload.get("stderr") or "Failed to start detached Multipass command"))
        pid_text = str(payload.get("stdout") or "").strip().splitlines()
        if not pid_text:
            raise RuntimeError("Multipass detached command did not return a PID")
        return int(pid_text[-1].strip())

    async def close(self) -> None:
        return None


def _detect_host_cpu_count() -> int:
    count = os.cpu_count() or 2
    return max(1, int(count))


def _derive_default_cpus() -> int:
    total = _detect_host_cpu_count()
    if total <= 2:
        return 1
    if total <= 4:
        return 2
    if total <= 8:
        return min(4, total - 1)
    return min(6, max(2, total // 2))


def build_multipass_launch_profile() -> MultipassLaunchProfile:
    workspace_root = (
        (settings.runtime_multipass_workspace_root or "").strip()
        or settings.runtime_workspaces_host_dir
    )
    mount_mode = (settings.runtime_multipass_mount_mode or "mount").strip().lower()
    if mount_mode not in {"mount", "transfer"}:
        mount_mode = "mount"

    image = (settings.runtime_multipass_image or "").strip() or "lts"
    raw_cpus = str(settings.runtime_multipass_cpus or "").strip()
    try:
        cpus = int(raw_cpus) if raw_cpus else _derive_default_cpus()
    except ValueError:
        cpus = _derive_default_cpus()
    memory = (settings.runtime_multipass_memory or "").strip() or "2G"
    disk = (settings.runtime_multipass_disk or "").strip() or "8G"

    return MultipassLaunchProfile(
        image=image,
        cpus=max(1, cpus),
        memory=memory,
        disk=disk,
        workspace_root=workspace_root,
        mount_mode=mount_mode,
    )


def _is_custom_multipass_image(image: str) -> bool:
    raw = (image or "").strip()
    if not raw:
        return False
    if raw == "lts":
        return False
    if raw.startswith(("file://", "http://", "https://")):
        return True
    return raw.startswith("/")


def _launch_image_arg(image: str) -> str:
    raw = (image or "").strip()
    if raw.startswith(("file://", "http://", "https://")):
        return raw
    if raw.startswith("/"):
        return f"file://{raw}"
    return raw


class MultipassRuntimeProvider:
    """Multipass-backed runtime provider.

    This provider is intentionally introduced in stages.
    The initial implementation establishes backend selection and
    host-derived launch profile computation. Session lifecycle
    orchestration is implemented in a later pass.
    """

    def __init__(self) -> None:
        self._instances: dict[str, RuntimeInstance] = {}
        self._ensure_locks: dict[str, asyncio.Lock] = {}
        self._profile = build_multipass_launch_profile()
        self._bridge_url = settings.runtime_multipass_bridge_url.rstrip("/")
        self._bridge_token = (settings.runtime_multipass_bridge_token or "").strip()

    async def bridge_health(self) -> dict[str, object]:
        headers = {"X-Sentinel-Bridge-Token": self._bridge_token}
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                response = await client.get(f"{self._bridge_url}/healthz", headers=headers)
            except Exception as exc:  # noqa: BLE001
                raise MultipassBridgeError("Multipass bridge is not reachable") from exc
        if response.status_code != 200:
            raise MultipassBridgeError(f"Multipass bridge health check failed ({response.status_code})")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise MultipassBridgeError("Multipass bridge returned an invalid health response")
        return payload

    async def _bridge_command(
        self,
        argv: list[str],
        *,
        timeout_seconds: int = 120,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, object]:
        headers = {"X-Sentinel-Bridge-Token": self._bridge_token}
        payload = {
            "argv": argv,
            "timeout_seconds": timeout_seconds,
        }
        if cwd:
            payload["cwd"] = cwd
        if env:
            payload["env"] = env
        async with httpx.AsyncClient(timeout=timeout_seconds + 5) as client:
            response = await client.post(
                f"{self._bridge_url}/v1/command",
                headers=headers,
                json=payload,
            )
        if response.status_code != 200:
            raise MultipassBridgeError(f"Multipass bridge command failed ({response.status_code})")
        result = response.json()
        if not isinstance(result, dict):
            raise MultipassBridgeError("Multipass bridge returned invalid command payload")
        return result

    async def _bridge_ensure_dir(self, path: str) -> None:
        headers = {"X-Sentinel-Bridge-Token": self._bridge_token}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{self._bridge_url}/v1/ensure-dir",
                headers=headers,
                json={"path": path},
            )
        if response.status_code != 200:
            raise MultipassBridgeError(f"Multipass bridge directory ensure failed ({response.status_code})")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise MultipassBridgeError("Multipass bridge could not create the workspace directory")

    async def _bridge_reset_dir(self, path: str) -> None:
        headers = {"X-Sentinel-Bridge-Token": self._bridge_token}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self._bridge_url}/v1/reset-dir",
                headers=headers,
                json={"path": path},
            )
        if response.status_code != 200:
            raise MultipassBridgeError(f"Multipass bridge directory reset failed ({response.status_code})")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise MultipassBridgeError("Multipass bridge could not reset the workspace directory")

    async def _exec_as_root(self, instance_name: str, script: str, *, timeout_seconds: int = 300) -> dict[str, object]:
        return await self._bridge_command(
            ["multipass", "exec", instance_name, "--", "bash", "-lc", _build_exec_script(script, as_root=True)],
            timeout_seconds=timeout_seconds,
        )

    async def _write_guest_file(
        self,
        instance_name: str,
        path: str,
        content: str,
        *,
        mode: str = "0644",
        owner: str | None = None,
    ) -> None:
        import base64

        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        script = (
            "python3 - <<'PY'\n"
            "import base64, pathlib\n"
            f"path = pathlib.Path({path!r})\n"
            "path.parent.mkdir(parents=True, exist_ok=True)\n"
            f"path.write_bytes(base64.b64decode({encoded!r}))\n"
            "PY\n"
            f"chmod {mode} {_shell_quote(path)}"
        )
        if owner:
            script += f"\nchown {owner} {_shell_quote(path)}"
        result = await self._exec_as_root(instance_name, script, timeout_seconds=120)
        if result.get("returncode") != 0:
            raise MultipassBridgeError(str(result.get("stderr") or f"Failed to write {path} in Multipass guest"))

    async def _sleep_short(self) -> None:
        import asyncio
        await asyncio.sleep(1)

    async def _inspect_instance(self, instance_name: str) -> dict[str, object] | None:
        payload = await self._bridge_command(
            ["multipass", "info", instance_name, "--format", "json"],
            timeout_seconds=20,
        )
        if payload.get("returncode") != 0:
            return None
        stdout = str(payload.get("stdout") or "").strip()
        if not stdout:
            return None
        import json

        parsed = json.loads(stdout)
        info = parsed.get("info") if isinstance(parsed, dict) else None
        if not isinstance(info, dict):
            return None
        details = info.get(instance_name)
        return details if isinstance(details, dict) else None

    async def _list_instances(self) -> list[dict[str, object]]:
        payload = await self._bridge_command(
            ["multipass", "list", "--format", "json"],
            timeout_seconds=20,
        )
        if payload.get("returncode") != 0:
            return []
        stdout = str(payload.get("stdout") or "").strip()
        if not stdout:
            return []
        import json

        parsed = json.loads(stdout)
        items = parsed.get("list") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    async def _launch_instance(self, instance_name: str) -> None:
        await self._bridge_command(
            [
                "multipass",
                "launch",
                _launch_image_arg(self._profile.image),
                "--name",
                instance_name,
                "--timeout",
                str(_MULTIPASS_LAUNCH_TIMEOUT_SECONDS),
                "--cpus",
                str(self._profile.cpus),
                "--memory",
                self._profile.memory,
                "--disk",
                self._profile.disk,
            ],
            timeout_seconds=900,
        )

    async def _start_instance(self, instance_name: str) -> None:
        await self._bridge_command(
            [
                "multipass",
                "start",
                "--timeout",
                str(_MULTIPASS_LAUNCH_TIMEOUT_SECONDS),
                instance_name,
            ],
            timeout_seconds=180,
        )

    async def _delete_instance(self, instance_name: str) -> None:
        await self._bridge_command(["multipass", "delete", instance_name], timeout_seconds=120)
        await self._bridge_command(["multipass", "purge"], timeout_seconds=120)

    async def _wait_for_instance_ready(self, instance_name: str, *, timeout: int = 120) -> None:
        for _ in range(max(1, timeout)):
            info = await self._inspect_instance(instance_name)
            if info is not None and str(info.get("state") or "").lower() == "running":
                try:
                    result = await self._bridge_command(
                        ["multipass", "exec", instance_name, "--", "bash", "-lc", "true"],
                        timeout_seconds=10,
                    )
                except Exception:
                    result = None
                if isinstance(result, dict) and result.get("returncode") == 0:
                    return
            await self._sleep_short()
        raise TimeoutError(f"Multipass instance {instance_name} not ready after {timeout}s")

    async def _ensure_guest_layout(self, instance_name: str) -> None:
        script = (
            "id -u sentinel >/dev/null 2>&1 || sudo useradd -m -s /bin/bash sentinel; "
            "sudo passwd -d sentinel >/dev/null 2>&1 || true; "
            "sudo sh -lc \"grep -q '^sentinel ALL=(ALL) NOPASSWD:ALL$' /etc/sudoers || "
            "echo 'sentinel ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers\"; "
            f"sudo mkdir -p {_GUEST_WORKSPACE}; "
            f"sudo chown -R sentinel:sentinel /home/sentinel"
        )
        await self._bridge_command(
            ["multipass", "exec", instance_name, "--", "bash", "-lc", script],
            timeout_seconds=180,
        )

    async def _ensure_runtime_environment(self, instance_name: str) -> None:
        marker_check = await self._exec_as_root(
            instance_name,
            f"test -f {_shell_quote(_PROVISION_MARKER)}",
            timeout_seconds=30,
        )
        if marker_check.get("returncode") != 0:
            if _is_custom_multipass_image(self._profile.image):
                raise MultipassBridgeError(
                    "Configured Multipass image is missing the Sentinel runtime marker; rebuild the baked image"
                )
            install = await self._exec_as_root(instance_name, _runtime_provision_script(), timeout_seconds=1800)
            if install.get("returncode") != 0:
                raise MultipassBridgeError(
                    str(
                        install.get("stderr")
                        or install.get("stdout")
                        or "Failed to provision Multipass runtime packages"
                    )
                )

        await self._exec_as_root(
            instance_name,
            "systemctl daemon-reload",
            timeout_seconds=60,
        )
        ready = await self._exec_as_root(
            instance_name,
            "curl -fsS http://127.0.0.1:6080/vnc.html >/dev/null 2>&1",
            timeout_seconds=15,
        )
        if ready.get("returncode") == 0:
            return

        enable = await self._exec_as_root(
            instance_name,
            "systemctl enable sentinel-runtime-desktop.service && systemctl restart sentinel-runtime-desktop.service",
            timeout_seconds=180,
        )
        if enable.get("returncode") != 0:
            raise MultipassBridgeError(str(enable.get("stderr") or "Failed to start Sentinel runtime desktop service"))
        await self._wait_for_runtime_services(instance_name)

    async def _wait_for_runtime_services(self, instance_name: str) -> None:
        script = (
            "for i in $(seq 1 60); do "
            "if curl -fsS http://127.0.0.1:6080/vnc.html >/dev/null 2>&1; then "
            "exit 0; fi; "
            "sleep 1; "
            "done; "
            "systemctl status sentinel-runtime-desktop.service --no-pager || true; "
            "exit 1"
        )
        result = await self._exec_as_root(instance_name, script, timeout_seconds=75)
        if result.get("returncode") != 0:
            raise MultipassBridgeError(
                str(result.get("stderr") or "Sentinel runtime desktop service did not become ready")
            )

    async def _ensure_browser_environment(self, instance_name: str) -> None:
        marker_check = await self._exec_as_root(
            instance_name,
            f"test -f {_shell_quote(_BROWSER_MARKER)} && test -x {_shell_quote(_BROWSER_BIN)} && test -s {_shell_quote(_BROWSER_SANDBOX)}",
            timeout_seconds=30,
        )
        if marker_check.get("returncode") == 0:
            return
        if _is_custom_multipass_image(self._profile.image):
            raise MultipassBridgeError(
                "Configured Multipass image is missing the Sentinel browser payload; rebuild the baked image"
            )
        result = await self._exec_as_root(
            instance_name,
            _browser_install_script(),
            timeout_seconds=1800,
        )
        if result.get("returncode") != 0:
            raise MultipassBridgeError(
                str(result.get("stderr") or result.get("stdout") or "Failed to provision Multipass browser")
            )

    async def _ensure_mount(self, instance_name: str, host_workspace: str) -> None:
        if self._profile.mount_mode != "mount":
            return
        result = await self._bridge_command(
            [
                "multipass",
                "mount",
                host_workspace,
                f"{instance_name}:{_GUEST_WORKSPACE}",
            ],
            timeout_seconds=180,
        )
        if result.get("returncode") not in (0, None):
            stderr = str(result.get("stderr") or "")
            if "already mounted" not in stderr.lower():
                raise MultipassBridgeError(stderr or "Failed to mount workspace into Multipass instance")

    async def _sync_host_to_guest(self, instance_name: str, host_workspace: str) -> None:
        host_workspace = str(Path(host_workspace))
        host_parent = str(Path(host_workspace).parent)
        await self._bridge_ensure_dir(host_parent)
        await self._bridge_command(
            [
                "multipass",
                "exec",
                instance_name,
                "--",
                "bash",
                "-lc",
                f"sudo rm -rf {_shell_quote(_GUEST_WORKSPACE)} && sudo mkdir -p {_shell_quote(os.path.dirname(_GUEST_WORKSPACE))}",
            ],
            timeout_seconds=60,
        )
        result = await self._bridge_command(
            [
                "multipass",
                "transfer",
                "--recursive",
                "--parents",
                host_workspace,
                f"{instance_name}:{os.path.dirname(_GUEST_WORKSPACE)}",
            ],
            timeout_seconds=300,
        )
        if result.get("returncode") != 0:
            raise MultipassBridgeError(str(result.get("stderr") or "Failed to transfer workspace into Multipass"))
        await self._bridge_command(
            [
                "multipass",
                "exec",
                instance_name,
                "--",
                "bash",
                "-lc",
                f"sudo chown -R sentinel:sentinel {_shell_quote(_GUEST_WORKSPACE)}",
            ],
            timeout_seconds=60,
        )

    async def _sync_guest_to_host(self, instance_name: str, host_workspace: str) -> None:
        host_workspace = str(Path(host_workspace))
        host_parent = str(Path(host_workspace).parent)
        await self._bridge_ensure_dir(host_parent)
        await self._bridge_reset_dir(host_workspace)
        result = await self._bridge_command(
            [
                "multipass",
                "transfer",
                "--recursive",
                f"{instance_name}:{_GUEST_WORKSPACE}",
                host_parent,
            ],
            timeout_seconds=300,
        )
        if result.get("returncode") != 0:
            raise MultipassBridgeError(str(result.get("stderr") or "Failed to transfer workspace from Multipass"))

    async def _ensure_workspace(self, instance_name: str, host_workspace: str) -> None:
        if self._profile.mount_mode == "mount":
            await self._ensure_mount(instance_name, host_workspace)
            return
        await self._sync_host_to_guest(instance_name, host_workspace)

    async def _instance_host(self, instance_name: str) -> str:
        info = await self._inspect_instance(instance_name)
        if info is None:
            raise MultipassBridgeError(f"Multipass instance {instance_name} not found")
        ipv4 = info.get("ipv4")
        if isinstance(ipv4, list) and ipv4:
            return str(ipv4[0])
        raise MultipassBridgeError(f"Multipass instance {instance_name} has no IPv4 address")

    async def ensure(self, session_id: UUID | str) -> RuntimeInstance:
        key = str(session_id)
        lock = self._ensure_locks.setdefault(key, asyncio.Lock())
        async with lock:
            await self.bridge_health()

            instance_name = _instance_name(key)
            host_workspace = _session_workspace_root(key, self._profile.workspace_root)
            existing = self._instances.get(key)
            if existing is not None:
                info = await self._inspect_instance(instance_name)
                state = _instance_state(info)
                if state == "unknown":
                    await self._delete_instance(instance_name)
                    info = None
                elif info is not None and state == "running":
                    await self._wait_for_instance_ready(instance_name)
                    await self._ensure_guest_layout(instance_name)
                    await self._ensure_runtime_environment(instance_name)
                    await self._ensure_workspace(instance_name, host_workspace)
                    existing.host = await self._instance_host(instance_name)
                    existing.metadata["host_workspace"] = host_workspace
                    existing.metadata["mount_mode"] = self._profile.mount_mode
                    return existing

            await self._bridge_ensure_dir(host_workspace)

            info = await self._inspect_instance(instance_name)
            state = _instance_state(info)
            if state == "unknown":
                await self._delete_instance(instance_name)
                info = None
                state = ""
            if info is None:
                await self._launch_instance(instance_name)
                await self._wait_for_instance_ready(instance_name)
            elif state != "running":
                await self._start_instance(instance_name)
                await self._wait_for_instance_ready(instance_name)
            else:
                await self._wait_for_instance_ready(instance_name)

            await self._ensure_guest_layout(instance_name)
            await self._ensure_runtime_environment(instance_name)
            await self._ensure_workspace(instance_name, host_workspace)
            host = await self._instance_host(instance_name)

            runtime = RuntimeInstance(
                session_id=key,
                client=MultipassCommandClient(
                    provider=self,
                    instance_name=instance_name,
                    host_workspace=host_workspace,
                    mount_mode=self._profile.mount_mode,
                ),
                workspace_path=_GUEST_WORKSPACE,
                host=host,
                metadata={
                    "provider": "multipass",
                    "instance_name": instance_name,
                    "mount_mode": self._profile.mount_mode,
                    "host_workspace": host_workspace,
                },
            )
            self._instances[key] = runtime
            return runtime

    async def activate_session(self, session_id: UUID | str) -> RuntimeInstance:
        return await self.ensure(session_id)

    async def describe(self, session_id: UUID | str) -> RuntimeProviderInfo:
        key = str(session_id)
        runtime = self._instances.get(key)
        instance_name = (
            str(runtime.metadata.get("instance_name"))
            if runtime is not None and runtime.metadata.get("instance_name")
            else _instance_name(key)
        )
        info = await self._inspect_instance(instance_name)
        state = _instance_state(info) or "missing"
        ipv4 = info.get("ipv4") if isinstance(info, dict) else None
        host = str(ipv4[0]) if isinstance(ipv4, list) and ipv4 else "—"
        items = [
            RuntimeProviderInfoItem(key="instance", label="Instance", value=instance_name),
            RuntimeProviderInfoItem(key="state", label="State", value=state.upper()),
            RuntimeProviderInfoItem(key="host", label="Host", value=host),
            RuntimeProviderInfoItem(key="mount_mode", label="Mount Mode", value=self._profile.mount_mode),
            RuntimeProviderInfoItem(key="image", label="Image", value=self._profile.image),
        ]
        summary = {
            "running": "Per-session Multipass VM is running.",
            "stopped": "Multipass VM exists but is stopped.",
            "unknown": "Multipass VM state is unknown.",
            "missing": "Multipass VM has not been created yet.",
        }.get(state, f"Multipass instance state: {state}.")
        return RuntimeProviderInfo(
            id="multipass",
            label="Multipass",
            status=state,
            summary=summary,
            items=items,
        )

    async def hard_restart(self, session_id: UUID | str) -> RuntimeInstance:
        await self.destroy(session_id)
        return await self.activate_session(session_id)

    async def destroy(self, session_id: UUID | str) -> None:
        key = str(session_id)
        runtime = self._instances.pop(key, None)
        if runtime is not None and runtime.metadata.get("mount_mode") == "transfer":
            try:
                await self._sync_guest_to_host(
                    str(runtime.metadata.get("instance_name") or _instance_name(key)),
                    str(runtime.metadata.get("host_workspace") or _session_workspace_root(key, self._profile.workspace_root)),
                )
            except Exception:
                logger.warning("Could not sync transfer-mode workspace before destroying Multipass instance %s", key, exc_info=True)
        instance_name = (
            str(runtime.metadata.get("instance_name"))
            if runtime is not None and runtime.metadata.get("instance_name")
            else _instance_name(key)
        )
        await self._delete_instance(instance_name)

    async def stop(self, session_id: UUID | str) -> bool:
        key = str(session_id)
        runtime = self._instances.pop(key, None)
        if runtime is not None and runtime.metadata.get("mount_mode") == "transfer":
            try:
                await self._sync_guest_to_host(
                    str(runtime.metadata.get("instance_name") or _instance_name(key)),
                    str(runtime.metadata.get("host_workspace") or _session_workspace_root(key, self._profile.workspace_root)),
                )
            except Exception:
                logger.warning("Could not sync transfer-mode workspace before stopping Multipass instance %s", key, exc_info=True)
        instance_name = (
            str(runtime.metadata.get("instance_name"))
            if runtime is not None and runtime.metadata.get("instance_name")
            else _instance_name(key)
        )
        result = await self._bridge_command(["multipass", "stop", instance_name], timeout_seconds=120)
        return result.get("returncode") == 0

    async def stop_all(self) -> int:
        keys = list(self._instances.keys())
        for key in keys:
            await self.stop(key)
        return len(keys)

    def get(self, session_id: UUID | str) -> RuntimeInstance | None:
        return self._instances.get(str(session_id))

    async def recover_existing(self) -> int:
        await self.bridge_health()
        recovered = 0
        for info in await self._list_instances():
            instance_name = str(info.get("name") or "")
            if not instance_name.startswith(_NAME_PREFIX):
                continue
            state = str(info.get("state") or "")
            if state.lower() != "running":
                continue
            session_prefix = instance_name[len(_NAME_PREFIX):]
            full_key = await self._resolve_full_session_id(session_prefix) or session_prefix
            if full_key in self._instances:
                continue
            host_workspace = _session_workspace_root(full_key, self._profile.workspace_root)
            await self._bridge_ensure_dir(host_workspace)
            host = await self._instance_host(instance_name)
            runtime = RuntimeInstance(
                session_id=full_key,
                client=MultipassCommandClient(
                    provider=self,
                    instance_name=instance_name,
                    host_workspace=host_workspace,
                    mount_mode=self._profile.mount_mode,
                ),
                workspace_path=_GUEST_WORKSPACE,
                host=host,
                metadata={
                    "provider": "multipass",
                    "instance_name": instance_name,
                    "mount_mode": self._profile.mount_mode,
                    "host_workspace": host_workspace,
                },
            )
            self._instances[full_key] = runtime
            recovered += 1
        return recovered

    def get_host(self, session_id: UUID | str) -> str | None:
        instance = self.get(session_id)
        return instance.host if instance is not None else None

    def get_public_host(self, session_id: UUID | str) -> str | None:
        host = (settings.runtime_forward_public_host or "").strip()
        if host:
            return host
        return self.get_host(session_id)

    def resolve_port(self, session_id: UUID | str, internal_port: int) -> int | None:
        _ = session_id
        return int(internal_port)

    async def restart_browser(self, session_id: UUID | str, runtime: RuntimeInstance) -> None:
        _ = session_id
        instance_name = str(runtime.metadata.get("instance_name") or _instance_name(runtime.session_id))
        await self._ensure_browser_environment(instance_name)
        restart = await self._exec_as_root(
            instance_name,
            _browser_restart_script(),
            timeout_seconds=120,
        )
        if restart.get("returncode") != 0:
            raise MultipassBridgeError(
                str(restart.get("stderr") or restart.get("stdout") or "Failed to restart Multipass browser")
            )
        ready = await self._exec_as_root(
            instance_name,
            _browser_ready_check_script(),
            timeout_seconds=45,
        )
        if ready.get("returncode") != 0:
            raise MultipassBridgeError(
                str(ready.get("stderr") or ready.get("stdout") or "Browser CDP did not become ready")
            )

    @property
    def profile(self) -> MultipassLaunchProfile:
        return self._profile

    async def _resolve_full_session_id(self, prefix: str) -> str | None:
        try:
            from app.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as session:
                row = await session.execute(
                    text("SELECT id FROM sessions WHERE CAST(id AS TEXT) LIKE :prefix LIMIT 1"),
                    {"prefix": f"{prefix}%"},
                )
                result = row.fetchone()
                return str(result[0]) if result else None
        except Exception:
            logger.debug("Could not resolve session prefix %s from DB", prefix, exc_info=True)
            return None
