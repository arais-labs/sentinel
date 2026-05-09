from __future__ import annotations

import asyncio
import pytest

import app.services.runtime as runtime_module
from app.config import settings
from app.services.runtime.multipass import (
    MultipassCommandClient,
    MultipassRuntimeProvider,
    _launch_image_arg,
    _browser_install_script,
    _browser_ready_check_script,
    _browser_restart_script,
    _browser_service_unit,
    _browser_start_script,
    build_multipass_launch_profile,
    _MULTIPASS_LAUNCH_TIMEOUT_SECONDS,
    _runtime_provision_script,
    _runtime_service_unit,
    _runtime_start_script,
)


def test_build_multipass_launch_profile_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_multipass_image", None)
    monkeypatch.setattr(settings, "runtime_multipass_cpus", None)
    monkeypatch.setattr(settings, "runtime_multipass_memory", None)
    monkeypatch.setattr(settings, "runtime_multipass_disk", None)
    monkeypatch.setattr(settings, "runtime_multipass_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "runtime_multipass_mount_mode", "mount")
    monkeypatch.setattr("app.services.runtime.multipass.os.cpu_count", lambda: 8)

    profile = build_multipass_launch_profile()

    assert profile.image == "lts"
    assert profile.cpus == 4
    assert profile.memory == "2G"
    assert profile.disk == "8G"
    assert profile.workspace_root == str(tmp_path)
    assert profile.mount_mode == "mount"


def test_build_multipass_launch_profile_honors_overrides(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_multipass_image", "24.04")
    monkeypatch.setattr(settings, "runtime_multipass_cpus", 3)
    monkeypatch.setattr(settings, "runtime_multipass_memory", "6G")
    monkeypatch.setattr(settings, "runtime_multipass_disk", "18G")
    monkeypatch.setattr(settings, "runtime_multipass_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "runtime_multipass_mount_mode", "transfer")

    profile = build_multipass_launch_profile()

    assert profile.image == "24.04"
    assert profile.cpus == 3
    assert profile.memory == "6G"
    assert profile.disk == "18G"
    assert profile.workspace_root == str(tmp_path)
    assert profile.mount_mode == "transfer"


def test_launch_image_arg_wraps_local_image_path() -> None:
    assert _launch_image_arg("/tmp/runtime-base.img") == "file:///tmp/runtime-base.img"
    assert _launch_image_arg("file:///tmp/runtime-base.img") == "file:///tmp/runtime-base.img"
    assert _launch_image_arg("lts") == "lts"


def test_runtime_start_script_contains_required_services() -> None:
    script = _runtime_start_script()
    assert "websockify --web" in script
    assert "x11vnc -display" in script
    assert "-noxdamage" in script
    assert "startplasma-x11" in script
    assert "konsole --workdir /home/sentinel/workspace --hold -e /bin/bash -lc" in script
    assert "~/.config/kscreenlockerrc" in script
    assert "Autolock=false" in script
    assert "LockOnResume=false" in script
    assert "~/.config/powerdevilrc" in script
    assert "idleTime=0" in script
    assert "~/.config/kwalletrc" in script
    assert "Enabled=false" in script
    assert 'xset -display "$DISPLAY" s off -dpms' in script
    assert "chown sentinel:sentinel /home/sentinel/workspace >/dev/null 2>&1 || true" in script
    assert "X11VNC_PID=$!" in script
    assert "WEBSOCKIFY_PID=$!" in script
    assert 'kill -0 "$X11VNC_PID"' in script
    assert 'kill -0 "$WEBSOCKIFY_PID"' in script
    assert "wait -n" not in script
    assert "--remote-debugging-port=9222" not in script
    assert "chromium" not in script


def test_runtime_install_script_waits_for_package_manager() -> None:
    from app.services.runtime.multipass import _runtime_install_script

    script = _runtime_install_script()
    assert "wait_for_package_manager()" in script
    assert "pgrep -x apt-get" in script
    assert "sudo env DEBIAN_FRONTEND=noninteractive apt-get" in script
    assert "DPkg::Lock::Timeout=600" in script
    assert "unattended-upgrade" not in script
    assert "snap" not in script
    assert "plasma-desktop" in script
    assert "plasma-workspace" in script
    assert "konsole" in script
    assert "$APT_GET clean" in script
    assert "rm -rf /var/lib/apt/lists/*" in script


def test_browser_install_script_uses_playwright_bundle() -> None:
    script = _browser_install_script()
    assert "python3 -m pip install --break-system-packages --user playwright" in script
    assert "python3 -m playwright install chromium" in script
    assert "find /home/sentinel/.cache/ms-playwright" in script
    assert "test -s '/opt/google/chrome/chrome_sandbox'" in script
    assert "mv \"$PLAYWRIGHT_ROOT\" '/opt/google/chrome'" in script
    assert "chmod 4755 '/opt/google/chrome/chrome_sandbox'" in script
    assert "rm -rf /home/sentinel/.cache/ms-playwright /home/sentinel/.cache/pip" in script
    assert "cat > '/usr/local/bin/sentinel-runtime-browser.sh'" in script
    assert "cat > '/etc/systemd/system/sentinel-runtime-browser.service'" in script
    assert "snap" not in script


def test_browser_start_script_uses_opt_google_chrome() -> None:
    script = _browser_start_script()
    assert "/opt/google/chrome/chrome" in script
    assert "CHROME_DEVEL_SANDBOX=/opt/google/chrome/chrome_sandbox" in script
    assert "socat TCP-LISTEN:9223" in script
    assert "--remote-debugging-port=9222" in script
    assert "DISPLAY=:99" in script
    assert "mkdir -p /home/sentinel/.config/chromium" in script
    assert "chmod 700 /tmp/runtime-sentinel >/dev/null 2>&1 || true" in script
    assert "runuser -u sentinel -- env" in script
    assert "CHROME_PID=$!" in script
    assert 'kill -0 "$CHROME_PID"' in script
    assert 'kill -0 "$SOCAT_PID"' in script


def test_browser_restart_script_restarts_service() -> None:
    script = _browser_restart_script()
    assert "systemctl daemon-reload" in script
    assert "systemctl enable sentinel-runtime-browser.service" in script
    assert "systemctl restart sentinel-runtime-browser.service" in script


def test_browser_ready_check_script_targets_cdp_proxy() -> None:
    script = _browser_ready_check_script()
    assert "http://127.0.0.1:9223/json/version" in script
    assert "systemctl status sentinel-runtime-browser.service" in script


def test_browser_service_unit_targets_browser_script() -> None:
    unit = _browser_service_unit()
    assert "ExecStart=/usr/local/bin/sentinel-runtime-browser.sh" in unit
    assert "After=network-online.target sentinel-runtime-desktop.service" in unit
    assert "Restart=always" in unit


def test_runtime_provision_script_uses_single_guest_lock() -> None:
    script = _runtime_provision_script()
    assert script.count("cat > '/usr/local/bin/sentinel-runtime-start.sh'") >= 2
    assert script.count("cat > '/etc/systemd/system/sentinel-runtime-desktop.service'") >= 2
    assert "if test -d '/var/lib/sentinel/runtime-provision.lock'" in script
    assert "exec 9>>'/var/lib/sentinel/runtime-provision.lock'" in script
    assert "flock -w 1800 9" in script
    assert "trap 'flock -u 9" in script
    assert "cat > '/usr/local/bin/sentinel-runtime-start.sh'" in script
    assert "cat > '/etc/systemd/system/sentinel-runtime-desktop.service'" in script
    assert "\nSENTINEL_RUNTIME_START\n" in script
    assert "\nSENTINEL_RUNTIME_SERVICE\n" in script
    assert "systemctl daemon-reload" in script


def test_runtime_service_unit_targets_start_script() -> None:
    unit = _runtime_service_unit()
    assert "sentinel-runtime-desktop.service" not in unit  # unit body only
    assert "ExecStart=/usr/local/bin/sentinel-runtime-start.sh" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "Environment=BROWSER_LOCALE=en-US" in unit
    assert "Environment=BROWSER_TIMEZONE_ID=America/Los_Angeles" in unit


def test_get_runtime_selects_multipass(monkeypatch) -> None:
    monkeypatch.setattr(settings, "runtime_exec_backend", "multipass")
    monkeypatch.setattr(settings, "runtime_multipass_workspace_root", "/tmp")
    monkeypatch.setattr(settings, "runtime_multipass_mount_mode", "mount")
    monkeypatch.setattr(settings, "runtime_multipass_cpus", None)
    monkeypatch.setattr(settings, "runtime_multipass_memory", None)
    monkeypatch.setattr(settings, "runtime_multipass_disk", None)
    monkeypatch.setattr(settings, "runtime_multipass_image", None)
    monkeypatch.setattr(runtime_module, "_multipass_provider", None)

    provider = runtime_module.get_runtime()

    assert isinstance(provider, MultipassRuntimeProvider)
    assert provider.profile.memory == "2G"


def test_multipass_provider_bridge_health(monkeypatch) -> None:
    monkeypatch.setattr(settings, "runtime_multipass_workspace_root", "/tmp")
    monkeypatch.setattr(settings, "runtime_multipass_mount_mode", "mount")
    monkeypatch.setattr(settings, "runtime_multipass_bridge_url", "http://bridge.test:47480")
    monkeypatch.setattr(settings, "runtime_multipass_bridge_token", "secret-token")

    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        def json(self):
            return {"ok": True, "multipass_version": "multipass 1.0"}

    class _Client:
        def __init__(self, *args, **kwargs):
            _ = args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return _Response()

    monkeypatch.setattr("app.services.runtime.multipass.httpx.AsyncClient", _Client)

    provider = MultipassRuntimeProvider()
    payload = asyncio.run(provider.bridge_health())

    assert payload["ok"] is True
    assert captured["url"] == "http://bridge.test:47480/healthz"
    assert captured["headers"] == {"X-Sentinel-Bridge-Token": "secret-token"}


def test_multipass_launch_and_start_use_bounded_cli_timeout(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_multipass_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "runtime_multipass_mount_mode", "mount")

    provider = MultipassRuntimeProvider()
    calls: list[list[str]] = []

    async def _bridge_command(argv: list[str], **kwargs):
        calls.append(argv)
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(provider, "_bridge_command", _bridge_command)

    asyncio.run(provider._launch_instance("sentinel-runtime-test"))
    asyncio.run(provider._start_instance("sentinel-runtime-test"))

    assert calls[0][:6] == [
        "multipass",
        "launch",
        provider.profile.image,
        "--name",
        "sentinel-runtime-test",
        "--timeout",
    ]
    assert calls[0][6] == str(_MULTIPASS_LAUNCH_TIMEOUT_SECONDS)
    assert calls[1] == [
        "multipass",
        "start",
        "--timeout",
        str(_MULTIPASS_LAUNCH_TIMEOUT_SECONDS),
        "sentinel-runtime-test",
    ]


def test_multipass_launch_uses_file_url_for_local_image(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_multipass_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "runtime_multipass_mount_mode", "mount")
    monkeypatch.setattr(settings, "runtime_multipass_image", "/tmp/sentinel-runtime-base-arm64.img")

    provider = MultipassRuntimeProvider()
    calls: list[list[str]] = []

    async def _bridge_command(argv: list[str], **kwargs):
        calls.append(argv)
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(provider, "_bridge_command", _bridge_command)

    asyncio.run(provider._launch_instance("sentinel-runtime-test"))

    assert calls[0][2] == "file:///tmp/sentinel-runtime-base-arm64.img"


def test_multipass_provider_ensure_launches_instance(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_multipass_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "runtime_multipass_mount_mode", "mount")
    monkeypatch.setattr(settings, "runtime_multipass_bridge_url", "http://bridge.test:47480")
    monkeypatch.setattr(settings, "runtime_multipass_bridge_token", "secret-token")
    monkeypatch.setattr(settings, "runtime_multipass_cpus", 2)
    monkeypatch.setattr(settings, "runtime_multipass_memory", "2G")
    monkeypatch.setattr(settings, "runtime_multipass_disk", "8G")
    monkeypatch.setattr(settings, "runtime_multipass_image", "lts")

    provider = MultipassRuntimeProvider()
    calls: list[tuple[str, object]] = []

    async def _bridge_health():
        calls.append(("bridge_health", None))
        return {"ok": True}

    async def _bridge_ensure_dir(path: str):
        calls.append(("ensure_dir", path))

    async def _inspect_instance(name: str):
        calls.append(("inspect", name))
        return None

    async def _launch_instance(name: str):
        calls.append(("launch", name))

    async def _ensure_guest_layout(name: str):
        calls.append(("guest_layout", name))

    async def _ensure_runtime_environment(name: str):
        calls.append(("runtime_environment", name))

    async def _wait_for_instance_ready(name: str, *, timeout: int = 120):
        calls.append(("wait_ready", (name, timeout)))

    async def _ensure_workspace(name: str, host_workspace: str):
        calls.append(("mount", (name, host_workspace)))

    async def _instance_host(name: str):
        calls.append(("host", name))
        return "10.20.30.40"

    monkeypatch.setattr(provider, "bridge_health", _bridge_health)
    monkeypatch.setattr(provider, "_bridge_ensure_dir", _bridge_ensure_dir)
    monkeypatch.setattr(provider, "_inspect_instance", _inspect_instance)
    monkeypatch.setattr(provider, "_launch_instance", _launch_instance)
    monkeypatch.setattr(provider, "_wait_for_instance_ready", _wait_for_instance_ready)
    monkeypatch.setattr(provider, "_ensure_guest_layout", _ensure_guest_layout)
    monkeypatch.setattr(provider, "_ensure_runtime_environment", _ensure_runtime_environment)
    monkeypatch.setattr(provider, "_ensure_workspace", _ensure_workspace)
    monkeypatch.setattr(provider, "_instance_host", _instance_host)

    runtime = asyncio.run(provider.ensure("session-123"))

    assert runtime.workspace_path == "/home/sentinel/workspace"
    assert runtime.host == "10.20.30.40"
    assert runtime.metadata["provider"] == "multipass"
    assert runtime.metadata["mount_mode"] == "mount"
    assert runtime.metadata["instance_name"].startswith("sentinel-runtime-")
    assert calls[0] == ("bridge_health", None)
    assert ("launch", "sentinel-runtime-session-123") in calls


def test_multipass_custom_image_missing_runtime_marker_fails_fast(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_multipass_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "runtime_multipass_mount_mode", "mount")
    monkeypatch.setattr(settings, "runtime_multipass_image", "/tmp/sentinel-runtime-base-arm64.img")

    provider = MultipassRuntimeProvider()

    async def _exec_as_root(_instance_name: str, script: str, *, timeout_seconds: int = 300):
        if "runtime-provisioned-v1" in script:
            return {"returncode": 1, "stdout": "", "stderr": ""}
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(provider, "_exec_as_root", _exec_as_root)

    with pytest.raises(Exception, match="missing the Sentinel runtime marker"):
        asyncio.run(provider._ensure_runtime_environment("sentinel-runtime-test"))


def test_multipass_provider_ensure_reuses_running_instance(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_multipass_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "runtime_multipass_mount_mode", "transfer")
    monkeypatch.setattr(settings, "runtime_multipass_bridge_url", "http://bridge.test:47480")
    monkeypatch.setattr(settings, "runtime_multipass_bridge_token", "secret-token")

    provider = MultipassRuntimeProvider()
    called: dict[str, int] = {"launch": 0}

    async def _bridge_health():
        return {"ok": True}

    async def _bridge_ensure_dir(_path: str):
        return None

    async def _inspect_instance(_name: str):
        return {"state": "Running", "ipv4": ["10.0.0.9"]}

    async def _launch_instance(_name: str):
        called["launch"] += 1

    async def _ensure_guest_layout(_name: str):
        return None

    async def _ensure_runtime_environment(_name: str):
        return None

    async def _wait_for_instance_ready(_name: str, *, timeout: int = 120):
        return None

    async def _ensure_workspace(_name: str, _host_workspace: str):
        return None

    async def _instance_host(_name: str):
        return "10.0.0.9"

    monkeypatch.setattr(provider, "bridge_health", _bridge_health)
    monkeypatch.setattr(provider, "_bridge_ensure_dir", _bridge_ensure_dir)
    monkeypatch.setattr(provider, "_inspect_instance", _inspect_instance)
    monkeypatch.setattr(provider, "_launch_instance", _launch_instance)
    monkeypatch.setattr(provider, "_wait_for_instance_ready", _wait_for_instance_ready)
    monkeypatch.setattr(provider, "_ensure_guest_layout", _ensure_guest_layout)
    monkeypatch.setattr(provider, "_ensure_runtime_environment", _ensure_runtime_environment)
    monkeypatch.setattr(provider, "_ensure_workspace", _ensure_workspace)
    monkeypatch.setattr(provider, "_instance_host", _instance_host)

    runtime = asyncio.run(provider.ensure("session-abc"))

    assert runtime.host == "10.0.0.9"
    assert runtime.metadata["mount_mode"] == "transfer"
    assert called["launch"] == 0


def test_multipass_provider_ensure_recreates_unknown_instance(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_multipass_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "runtime_multipass_mount_mode", "mount")
    monkeypatch.setattr(settings, "runtime_multipass_bridge_url", "http://bridge.test:47480")
    monkeypatch.setattr(settings, "runtime_multipass_bridge_token", "secret-token")

    provider = MultipassRuntimeProvider()
    calls: list[tuple[str, object]] = []
    state = {"inspect_calls": 0}

    async def _bridge_health():
        return {"ok": True}

    async def _bridge_ensure_dir(_path: str):
        return None

    async def _inspect_instance(name: str):
        calls.append(("inspect", name))
        state["inspect_calls"] += 1
        if state["inspect_calls"] == 1:
            return {"state": "Unknown"}
        return None

    async def _delete_instance(name: str):
        calls.append(("delete", name))

    async def _launch_instance(name: str):
        calls.append(("launch", name))

    async def _wait_for_instance_ready(name: str, *, timeout: int = 120):
        calls.append(("wait_ready", (name, timeout)))

    async def _ensure_guest_layout(name: str):
        calls.append(("guest_layout", name))

    async def _ensure_runtime_environment(name: str):
        calls.append(("runtime_environment", name))

    async def _ensure_workspace(name: str, host_workspace: str):
        calls.append(("workspace", (name, host_workspace)))

    async def _instance_host(name: str):
        calls.append(("host", name))
        return "10.0.0.11"

    monkeypatch.setattr(provider, "bridge_health", _bridge_health)
    monkeypatch.setattr(provider, "_bridge_ensure_dir", _bridge_ensure_dir)
    monkeypatch.setattr(provider, "_inspect_instance", _inspect_instance)
    monkeypatch.setattr(provider, "_delete_instance", _delete_instance)
    monkeypatch.setattr(provider, "_launch_instance", _launch_instance)
    monkeypatch.setattr(provider, "_wait_for_instance_ready", _wait_for_instance_ready)
    monkeypatch.setattr(provider, "_ensure_guest_layout", _ensure_guest_layout)
    monkeypatch.setattr(provider, "_ensure_runtime_environment", _ensure_runtime_environment)
    monkeypatch.setattr(provider, "_ensure_workspace", _ensure_workspace)
    monkeypatch.setattr(provider, "_instance_host", _instance_host)

    runtime = asyncio.run(provider.ensure("session-unknown"))

    assert runtime.host == "10.0.0.11"
    assert ("delete", "sentinel-runtime-session-unkn") in calls
    assert ("launch", "sentinel-runtime-session-unkn") in calls


def test_multipass_command_client_syncs_transfer_mode(monkeypatch) -> None:
    provider = MultipassRuntimeProvider()
    calls: list[tuple[str, object]] = []

    async def _sync_host_to_guest(instance_name: str, host_workspace: str):
        calls.append(("push", (instance_name, host_workspace)))

    async def _sync_guest_to_host(instance_name: str, host_workspace: str):
        calls.append(("pull", (instance_name, host_workspace)))

    async def _bridge_command(argv: list[str], **kwargs):
        calls.append(("command", (argv, kwargs)))
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(provider, "_sync_host_to_guest", _sync_host_to_guest)
    monkeypatch.setattr(provider, "_sync_guest_to_host", _sync_guest_to_host)
    monkeypatch.setattr(provider, "_bridge_command", _bridge_command)

    client = MultipassCommandClient(
        provider=provider,
        instance_name="sentinel-runtime-abc",
        host_workspace="/tmp/workspace",
        mount_mode="transfer",
    )
    result = asyncio.run(client.run("pwd", cwd="/home/sentinel/workspace"))

    assert result.exit_status == 0
    assert [step for step, _ in calls] == ["push", "command", "pull"]


def test_multipass_provider_recover_existing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_multipass_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "runtime_multipass_mount_mode", "mount")
    monkeypatch.setattr(settings, "runtime_multipass_bridge_url", "http://bridge.test:47480")
    monkeypatch.setattr(settings, "runtime_multipass_bridge_token", "secret-token")

    provider = MultipassRuntimeProvider()
    calls: list[tuple[str, object]] = []

    async def _bridge_health():
        calls.append(("health", None))
        return {"ok": True}

    async def _list_instances():
        calls.append(("list", None))
        return [
            {"name": "sentinel-runtime-abc123", "state": "Running"},
            {"name": "sentinel-runtime-def456", "state": "Stopped"},
            {"name": "other-vm", "state": "Running"},
        ]

    async def _resolve_full_session_id(prefix: str):
        calls.append(("resolve", prefix))
        return "abc12300-0000-0000-0000-000000000000" if prefix == "abc123" else None

    async def _bridge_ensure_dir(path: str):
        calls.append(("ensure_dir", path))

    async def _instance_host(name: str):
        calls.append(("host", name))
        return "10.0.0.7"

    monkeypatch.setattr(provider, "bridge_health", _bridge_health)
    monkeypatch.setattr(provider, "_list_instances", _list_instances)
    monkeypatch.setattr(provider, "_resolve_full_session_id", _resolve_full_session_id)
    monkeypatch.setattr(provider, "_bridge_ensure_dir", _bridge_ensure_dir)
    monkeypatch.setattr(provider, "_instance_host", _instance_host)

    recovered = asyncio.run(provider.recover_existing())

    assert recovered == 1
    runtime = provider.get("abc12300-0000-0000-0000-000000000000")
    assert runtime is not None
    assert runtime.host == "10.0.0.7"
    assert runtime.metadata["instance_name"] == "sentinel-runtime-abc123"


def test_multipass_provider_ensure_reconciles_cached_instance(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_multipass_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "runtime_multipass_mount_mode", "mount")
    monkeypatch.setattr(settings, "runtime_multipass_bridge_url", "http://bridge.test:47480")
    monkeypatch.setattr(settings, "runtime_multipass_bridge_token", "secret-token")

    provider = MultipassRuntimeProvider()

    from app.services.runtime.base import RuntimeInstance

    cached = RuntimeInstance(
        session_id="session-cached",
        client=MultipassCommandClient(
            provider=provider,
            instance_name="sentinel-runtime-session-cach",
            host_workspace="/tmp/host-workspace",
            mount_mode="mount",
        ),
        workspace_path="/home/sentinel/workspace",
        host="10.0.0.1",
        metadata={
            "provider": "multipass",
            "instance_name": "sentinel-runtime-session-cach",
            "mount_mode": "mount",
            "host_workspace": "/tmp/host-workspace",
        },
    )
    provider._instances["session-cached"] = cached
    calls: list[tuple[str, object]] = []

    async def _bridge_health():
        calls.append(("health", None))
        return {"ok": True}

    async def _inspect_instance(name: str):
        calls.append(("inspect", name))
        return {"state": "Running", "ipv4": ["10.0.0.9"]}

    async def _wait_for_instance_ready(name: str, *, timeout: int = 120):
        calls.append(("wait_ready", (name, timeout)))

    async def _ensure_guest_layout(name: str):
        calls.append(("guest_layout", name))

    async def _ensure_runtime_environment(name: str):
        calls.append(("runtime_environment", name))

    async def _ensure_workspace(name: str, host_workspace: str):
        calls.append(("workspace", (name, host_workspace)))

    async def _instance_host(name: str):
        calls.append(("host", name))
        return "10.0.0.9"

    monkeypatch.setattr(provider, "bridge_health", _bridge_health)
    monkeypatch.setattr(provider, "_inspect_instance", _inspect_instance)
    monkeypatch.setattr(provider, "_wait_for_instance_ready", _wait_for_instance_ready)
    monkeypatch.setattr(provider, "_ensure_guest_layout", _ensure_guest_layout)
    monkeypatch.setattr(provider, "_ensure_runtime_environment", _ensure_runtime_environment)
    monkeypatch.setattr(provider, "_ensure_workspace", _ensure_workspace)
    monkeypatch.setattr(provider, "_instance_host", _instance_host)

    ensured = asyncio.run(provider.ensure("session-cached"))

    assert ensured is cached
    assert ensured.host == "10.0.0.9"
    assert ("runtime_environment", "sentinel-runtime-session-cach") in calls
    assert ("workspace", ("sentinel-runtime-session-cach", str(tmp_path / "session-cached" / "workspace"))) in calls


def test_multipass_provider_ensure_serializes_same_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_multipass_workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "runtime_multipass_mount_mode", "mount")
    monkeypatch.setattr(settings, "runtime_multipass_bridge_url", "http://bridge.test:47480")
    monkeypatch.setattr(settings, "runtime_multipass_bridge_token", "secret-token")

    provider = MultipassRuntimeProvider()
    state = {"launched": False, "inflight": 0, "max_inflight": 0}
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _bridge_health():
        return {"ok": True}

    async def _bridge_ensure_dir(_path: str):
        return None

    async def _inspect_instance(_name: str):
        if state["launched"]:
            return {"state": "Running", "ipv4": ["10.0.0.9"]}
        return None

    async def _launch_instance(_name: str):
        state["launched"] = True

    async def _wait_for_instance_ready(_name: str, *, timeout: int = 120):
        return None

    async def _ensure_guest_layout(_name: str):
        return None

    async def _ensure_runtime_environment(_name: str):
        state["inflight"] += 1
        state["max_inflight"] = max(state["max_inflight"], state["inflight"])
        entered.set()
        try:
            await release.wait()
        finally:
            state["inflight"] -= 1

    async def _ensure_workspace(_name: str, _host_workspace: str):
        return None

    async def _instance_host(_name: str):
        return "10.0.0.9"

    monkeypatch.setattr(provider, "bridge_health", _bridge_health)
    monkeypatch.setattr(provider, "_bridge_ensure_dir", _bridge_ensure_dir)
    monkeypatch.setattr(provider, "_inspect_instance", _inspect_instance)
    monkeypatch.setattr(provider, "_launch_instance", _launch_instance)
    monkeypatch.setattr(provider, "_wait_for_instance_ready", _wait_for_instance_ready)
    monkeypatch.setattr(provider, "_ensure_guest_layout", _ensure_guest_layout)
    monkeypatch.setattr(provider, "_ensure_runtime_environment", _ensure_runtime_environment)
    monkeypatch.setattr(provider, "_ensure_workspace", _ensure_workspace)
    monkeypatch.setattr(provider, "_instance_host", _instance_host)

    async def _run() -> None:
        task1 = asyncio.create_task(provider.ensure("session-serial"))
        await entered.wait()
        task2 = asyncio.create_task(provider.ensure("session-serial"))
        await asyncio.sleep(0)
        assert state["max_inflight"] == 1
        release.set()
        runtime1, runtime2 = await asyncio.gather(task1, task2)
        assert runtime1.host == "10.0.0.9"
        assert runtime2.host == "10.0.0.9"

    asyncio.run(_run())
    assert state["max_inflight"] == 1


def test_multipass_runtime_environment_uses_guest_provision_lock(monkeypatch) -> None:
    provider = MultipassRuntimeProvider()
    calls: list[tuple[str, object]] = []
    state = {"marker_checks": 0}

    async def _exec_as_root(_instance_name: str, script: str, *, timeout_seconds: int = 300):
        calls.append(("exec", script))
        if "test -f" in script:
            state["marker_checks"] += 1
            return {"returncode": 1 if state["marker_checks"] == 1 else 0}
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(provider, "_exec_as_root", _exec_as_root)

    asyncio.run(provider._ensure_runtime_environment("sentinel-runtime-test"))

    provision_scripts = [script for step, script in calls if step == "exec" and "flock -w 1800 9" in script]
    assert provision_scripts


def test_multipass_runtime_environment_skips_restart_when_desktop_ready(monkeypatch) -> None:
    provider = MultipassRuntimeProvider()
    calls: list[str] = []

    async def _exec_as_root(_instance_name: str, script: str, *, timeout_seconds: int = 300):
        calls.append(script)
        if "test -f" in script:
            return {"returncode": 0, "stdout": "", "stderr": ""}
        if "curl -fsS http://127.0.0.1:6080/vnc.html" in script:
            return {"returncode": 0, "stdout": "", "stderr": ""}
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(provider, "_exec_as_root", _exec_as_root)

    asyncio.run(provider._ensure_runtime_environment("sentinel-runtime-test"))

    assert any("systemctl daemon-reload" == script for script in calls)
    assert any("curl -fsS http://127.0.0.1:6080/vnc.html" in script for script in calls)
    assert not any("systemctl enable sentinel-runtime-desktop.service && systemctl restart sentinel-runtime-desktop.service" in script for script in calls)


def test_multipass_wait_for_runtime_services_only_requires_desktop(monkeypatch) -> None:
    provider = MultipassRuntimeProvider()
    scripts: list[str] = []

    async def _exec_as_root(_instance_name: str, script: str, *, timeout_seconds: int = 300):
        scripts.append(script)
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(provider, "_exec_as_root", _exec_as_root)

    asyncio.run(provider._wait_for_runtime_services("sentinel-runtime-test"))

    assert scripts
    assert "http://127.0.0.1:6080/vnc.html" in scripts[0]
    assert "http://127.0.0.1:9223/json/version" not in scripts[0]


def test_multipass_restart_browser_uses_provider_specific_scripts(monkeypatch) -> None:
    provider = MultipassRuntimeProvider()
    scripts: list[str] = []

    from app.services.runtime.base import RuntimeInstance

    runtime = RuntimeInstance(
        session_id="session-browser",
        client=None,  # type: ignore[arg-type]
        workspace_path="/home/sentinel/workspace",
        host="10.0.0.9",
        metadata={"instance_name": "sentinel-runtime-session-brow"},
    )

    async def _exec_as_root(_instance_name: str, script: str, *, timeout_seconds: int = 300):
        scripts.append(script)
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(provider, "_exec_as_root", _exec_as_root)

    asyncio.run(provider.restart_browser("session-browser", runtime))

    assert len(scripts) == 3
    assert "/var/lib/sentinel/browser-provisioned-v1" in scripts[0]
    assert "systemctl restart sentinel-runtime-browser.service" in scripts[1]
    assert "http://127.0.0.1:9223/json/version" in scripts[2]
