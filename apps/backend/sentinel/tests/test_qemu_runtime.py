from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

import app.services.runtime as runtime_module
from app.config import settings
from app.services.runtime.base import RuntimeInstance
from app.services.runtime.qemu import (
    QemuSessionClient,
    QemuRuntimeProvider,
    build_qemu_profile,
)
from app.services.runtime.qemu.controls import QemuBridgeError
from app.services.runtime.qemu.provider import qemu_control_mode


def test_qemu_guest_cleanup_script_is_fail_closed() -> None:
    source = Path("../../../../infra/runtime/qemu/provision/runtime-base.sh")
    script = (Path(__file__).resolve().parent / source).resolve().read_text()

    cleanup_start = script.index("cat > /usr/local/bin/sentinel-session-cleanup.sh")
    cleanup_body_start = script.index("#!/usr/bin/env bash", cleanup_start)
    cleanup = script[cleanup_body_start:script.index("\nEOF\n", cleanup_body_start)]

    assert 'rm -rf "${SESSION_ROOT}"' not in cleanup
    assert 'remove_vm_dir "home" "${SESSION_HOME}"' in cleanup
    assert 'remove_vm_dir "runtime" "${SESSION_RUNTIME_DIR}"' in cleanup
    assert 'remove_vm_dir "browser-profile" "${SESSION_PROFILE}"' in cleanup
    assert 'remove_vm_dir "venvs" "${SESSION_VENV_DIR}"' in cleanup
    assert 'mountpoint -q "${path}"' in cleanup
    assert 'umount "${SESSION_WORKSPACE}" || abort' in cleanup
    assert 'rmdir "${SESSION_WORKSPACE}"' in cleanup
    assert 'rmdir "${SESSION_ROOT}"' in cleanup
    assert "cleanup session=%s user=%s" in cleanup


def test_build_qemu_profile_defaults_to_runtime_workspace_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_qemu_image", "/tmp/runtime.qcow2")
    monkeypatch.setattr(settings, "runtime_qemu_ssh_key_path", "/tmp/runtime.id_ed25519")
    monkeypatch.setattr(settings, "runtime_qemu_workspace_root", None)
    monkeypatch.setattr(settings, "runtime_workspaces_host_dir", str(tmp_path))
    monkeypatch.setattr(settings, "runtime_qemu_cpus", 4)
    monkeypatch.setattr(settings, "runtime_qemu_memory_mb", 4096)
    monkeypatch.setattr(settings, "runtime_qemu_run_root", "/tmp/qemu-run")
    monkeypatch.setattr(settings, "runtime_qemu_ssh_port", 2227)
    monkeypatch.setattr(settings, "runtime_qemu_vnc_port", 16081)
    monkeypatch.setattr(settings, "runtime_qemu_cdp_port", 19224)
    monkeypatch.setattr(settings, "runtime_qemu_host", "host.docker.internal")
    monkeypatch.setattr(settings, "runtime_qemu_public_host", "localhost")
    monkeypatch.setattr(settings, "runtime_qemu_share_tag", "sentinel-host-workspaces")
    monkeypatch.setattr(settings, "runtime_qemu_share_mount", "/mnt/sentinel-host-workspaces")

    profile = build_qemu_profile()

    assert profile.image == "/tmp/runtime.qcow2"
    assert profile.ssh_key_path == "/tmp/runtime.id_ed25519"
    assert profile.workspace_root == str(tmp_path)
    assert profile.share_mount == "/mnt/sentinel-host-workspaces"


def test_get_runtime_selects_qemu(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_exec_backend", "qemu")
    monkeypatch.setattr(settings, "runtime_qemu_image", str(tmp_path / "runtime.qcow2"))
    monkeypatch.setattr(settings, "runtime_qemu_ssh_key_path", str(tmp_path / "runtime.id_ed25519"))
    monkeypatch.setattr(settings, "runtime_qemu_workspace_root", str(tmp_path / "workspaces"))
    monkeypatch.setattr(runtime_module, "_qemu_provider", None)

    provider = runtime_module.get_runtime()

    assert isinstance(provider, QemuRuntimeProvider)
    assert provider.profile.workspace_root == str(tmp_path / "workspaces")


def test_qemu_provider_bridge_health(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_qemu_image", str(tmp_path / "runtime.qcow2"))
    monkeypatch.setattr(settings, "runtime_qemu_ssh_key_path", str(tmp_path / "runtime.id_ed25519"))
    monkeypatch.setattr(settings, "runtime_qemu_workspace_root", str(tmp_path / "workspaces"))
    monkeypatch.setattr(settings, "runtime_qemu_bridge_url", "http://bridge.test:47481")
    monkeypatch.setattr(settings, "runtime_qemu_bridge_token", "secret-token")

    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        def json(self):
            return {"ok": True, "qemu_version": "QEMU emulator version 10.2.2"}

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

    monkeypatch.setattr("app.services.runtime.qemu.controls.bridge.httpx.AsyncClient", _Client)

    provider = QemuRuntimeProvider()
    payload = asyncio.run(provider.bridge_health())

    assert payload["ok"] is True
    assert captured["url"] == "http://bridge.test:47481/healthz"
    assert captured["headers"] == {"X-Sentinel-Bridge-Token": "secret-token"}


def test_qemu_control_mode_defaults_to_desktop_only_in_desktop_app(monkeypatch) -> None:
    monkeypatch.setattr(settings, "runtime_qemu_control", None)

    monkeypatch.setattr(settings, "app_env", "development")
    assert qemu_control_mode() == "bridge"

    monkeypatch.setattr(settings, "app_env", "desktop")
    assert qemu_control_mode() == "desktop"

    monkeypatch.setattr(settings, "runtime_qemu_control", "bridge")
    assert qemu_control_mode() == "bridge"


def test_qemu_provider_base_image_prepare_is_serialized(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_qemu_control", "bridge")
    monkeypatch.setattr(settings, "runtime_qemu_image", str(tmp_path / "runtime.qcow2"))
    monkeypatch.setattr(settings, "runtime_qemu_ssh_key_path", str(tmp_path / "runtime.id_ed25519"))
    monkeypatch.setattr(settings, "runtime_qemu_workspace_root", str(tmp_path / "workspaces"))

    provider = QemuRuntimeProvider()
    calls = {"build": 0}
    ready = False

    class _FakeControl:
        async def base_image_status(self, profile):
            _ = profile
            return {"state": "ready" if ready else "missing"}

        async def ensure_base_image(self, profile):
            _ = profile
            nonlocal ready
            calls["build"] += 1
            await asyncio.sleep(0)
            ready = True

    provider._control = _FakeControl()  # type: ignore[assignment]

    async def _run():
        await asyncio.gather(provider._ensure_base_image(), provider._ensure_base_image())

    asyncio.run(_run())

    assert calls["build"] == 1
    assert provider._base_image_status["state"] == "ready"


def test_qemu_provider_restart_browser_uses_session_metadata(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_qemu_image", str(tmp_path / "runtime.qcow2"))
    monkeypatch.setattr(settings, "runtime_qemu_ssh_key_path", str(tmp_path / "runtime.id_ed25519"))
    monkeypatch.setattr(settings, "runtime_qemu_workspace_root", str(tmp_path / "workspaces"))

    provider = QemuRuntimeProvider()
    captured: dict[str, object] = {}

    async def _run_root(command: str, *, timeout: int = 120):
        captured["command"] = command
        captured["timeout"] = timeout

        class _Result:
            exit_status = 0
            stdout = ""
            stderr = ""

        return _Result()

    monkeypatch.setattr(provider, "_run_root", _run_root)

    instance = RuntimeInstance(
        session_id="session-123",
        client=object(),  # type: ignore[arg-type]
        workspace_path="/srv/sentinel/sessions/session-123/workspace",
        host="host.docker.internal",
        metadata={
            "session_user": "ssn-session123",
            "session_profile": "/srv/sentinel/sessions/session-123/browser-profile",
            "session_runtime_dir": "/srv/sentinel/sessions/session-123/runtime",
        },
    )

    asyncio.run(provider.restart_browser("session-123", instance))

    command = str(captured["command"])
    assert "systemctl set-environment" in command
    assert "SENTINEL_BROWSER_USER='ssn-session123'" in command
    assert "SENTINEL_BROWSER_PROFILE='/srv/sentinel/sessions/session-123/browser-profile'" in command
    assert "SENTINEL_BROWSER_RUNTIME_DIR='/srv/sentinel/sessions/session-123/runtime'" in command
    assert "systemctl restart sentinel-runtime-browser.service" in command


def test_qemu_provider_destroy_logs_cleanup_stdout_and_stderr(monkeypatch, tmp_path, caplog) -> None:
    monkeypatch.setattr(settings, "runtime_qemu_image", str(tmp_path / "runtime.qcow2"))
    monkeypatch.setattr(settings, "runtime_qemu_ssh_key_path", str(tmp_path / "runtime.id_ed25519"))
    monkeypatch.setattr(settings, "runtime_qemu_workspace_root", str(tmp_path / "workspaces"))

    provider = QemuRuntimeProvider()
    commands: list[str] = []

    async def _run_root(command: str, *, timeout: int = 120):
        commands.append(command)

        class _Result:
            stdout = "cleanup stdout line"
            stderr = "cleanup stderr line"
            exit_status = 23

        return _Result()

    monkeypatch.setattr(provider, "_run_root", _run_root)
    provider._instances["session-fail"] = RuntimeInstance(
        session_id="session-fail",
        client=object(),  # type: ignore[arg-type]
        workspace_path="/srv/sentinel/sessions/session-fail/workspace",
        host="host.docker.internal",
        metadata={},
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(QemuBridgeError):
            asyncio.run(provider.destroy("session-fail"))

    assert commands == ["/usr/local/bin/sentinel-session-cleanup.sh --session-id 'session-fail'"]
    assert "cleanup stdout line" in caplog.text
    assert "cleanup stderr line" in caplog.text
    assert "session-fail" in provider._instances


def test_qemu_provider_ensure_does_not_activate_visual_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_qemu_image", str(tmp_path / "runtime.qcow2"))
    monkeypatch.setattr(settings, "runtime_qemu_ssh_key_path", str(tmp_path / "runtime.id_ed25519"))
    monkeypatch.setattr(settings, "runtime_qemu_workspace_root", str(tmp_path / "workspaces"))

    provider = QemuRuntimeProvider()
    browser_calls: list[str] = []
    terminal_calls: list[str] = []

    class _FakeSSH:
        async def wait_ready(self, *, timeout: int = 60):
            return None

        async def run(self, command: str, *, timeout: int = 300, cwd=None, env=None, as_root: bool = False):
            _ = as_root
            class _Result:
                exit_status = 0
                stdout = ""
                stderr = ""

            return _Result()

        async def run_detached(self, command: str, *, stdout_path: str, stderr_path: str, cwd=None, env=None, as_root: bool = False):
            _ = as_root
            return 123

        async def run_detached_script(self, script: str, *, stdout_path: str, stderr_path: str, shell_prefix: str = "bash -lc"):
            return 123

        async def close(self):
            return None

    async def _bridge_health():
        return {"ok": True}

    async def _ensure_vm():
        return None

    async def _ensure_base_image():
        return None

    async def _ensure_ssh():
        return _FakeSSH()

    async def _ensure_workspace_share_mount():
        return None

    async def _prepare_session(session_id: str):
        return {
            "SESSION_USER": f"ssn-{session_id}",
            "SESSION_WORKSPACE": f"/srv/sentinel/sessions/{session_id}/workspace",
            "SESSION_ROOT": f"/srv/sentinel/sessions/{session_id}",
            "SESSION_HOME": f"/srv/sentinel/sessions/{session_id}/home",
            "SESSION_PROFILE": f"/srv/sentinel/sessions/{session_id}/browser-profile",
            "SESSION_RUNTIME_DIR": f"/srv/sentinel/sessions/{session_id}/runtime",
            "HOST_WORKSPACE": f"/tmp/workspaces/{session_id}/workspace",
        }

    async def _restart_browser(session_id, runtime):
        browser_calls.append(str(session_id))

    async def _restart_terminal(session_id, runtime):
        terminal_calls.append(str(session_id))

    monkeypatch.setattr(provider, "bridge_health", _bridge_health)
    monkeypatch.setattr(provider, "_ensure_base_image", _ensure_base_image)
    monkeypatch.setattr(provider, "_ensure_vm", _ensure_vm)
    monkeypatch.setattr(provider, "_ensure_ssh", _ensure_ssh)
    monkeypatch.setattr(provider, "_ensure_workspace_share_mount", _ensure_workspace_share_mount)
    monkeypatch.setattr(provider, "_prepare_session", _prepare_session)
    monkeypatch.setattr(provider, "restart_browser", _restart_browser)
    monkeypatch.setattr(provider, "restart_terminal", _restart_terminal)

    first = asyncio.run(provider.ensure("session-a"))
    second = asyncio.run(provider.ensure("session-a"))
    third = asyncio.run(provider.ensure("session-b"))

    assert first is second
    assert third.session_id == "session-b"
    assert browser_calls == []
    assert terminal_calls == []
    assert first.metadata["python_venv_root"] == "/srv/sentinel/sessions/session-a/venvs"
    assert third.metadata["python_venv_root"] == "/srv/sentinel/sessions/session-b/venvs"
    assert first.terminal is not None
    assert first.terminal.session_user == "ssn-session-a"
    assert first.terminal.workspace_path == "/srv/sentinel/sessions/session-a/workspace"


def test_qemu_provider_activate_session_switches_visual_session_once(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "runtime_qemu_image", str(tmp_path / "runtime.qcow2"))
    monkeypatch.setattr(settings, "runtime_qemu_ssh_key_path", str(tmp_path / "runtime.id_ed25519"))
    monkeypatch.setattr(settings, "runtime_qemu_workspace_root", str(tmp_path / "workspaces"))

    provider = QemuRuntimeProvider()
    browser_calls: list[str] = []
    terminal_calls: list[str] = []

    class _FakeSSH:
        async def wait_ready(self, *, timeout: int = 60):
            return None

        async def run(self, command: str, *, timeout: int = 300, cwd=None, env=None, as_root: bool = False):
            _ = as_root
            class _Result:
                exit_status = 0
                stdout = ""
                stderr = ""

            return _Result()

        async def run_detached(self, command: str, *, stdout_path: str, stderr_path: str, cwd=None, env=None, as_root: bool = False):
            _ = as_root
            return 123

        async def run_detached_script(self, script: str, *, stdout_path: str, stderr_path: str, shell_prefix: str = "bash -lc"):
            return 123

        async def close(self):
            return None

    async def _bridge_health():
        return {"ok": True}

    async def _ensure_vm():
        return None

    async def _ensure_base_image():
        return None

    async def _ensure_ssh():
        return _FakeSSH()

    async def _ensure_workspace_share_mount():
        return None

    async def _prepare_session(session_id: str):
        return {
            "SESSION_USER": f"ssn-{session_id}",
            "SESSION_WORKSPACE": f"/srv/sentinel/sessions/{session_id}/workspace",
            "SESSION_ROOT": f"/srv/sentinel/sessions/{session_id}",
            "SESSION_HOME": f"/srv/sentinel/sessions/{session_id}/home",
            "SESSION_PROFILE": f"/srv/sentinel/sessions/{session_id}/browser-profile",
            "SESSION_RUNTIME_DIR": f"/srv/sentinel/sessions/{session_id}/runtime",
            "HOST_WORKSPACE": f"/tmp/workspaces/{session_id}/workspace",
        }

    async def _restart_browser(session_id, runtime):
        browser_calls.append(str(session_id))

    async def _restart_terminal(session_id, runtime):
        terminal_calls.append(str(session_id))

    monkeypatch.setattr(provider, "bridge_health", _bridge_health)
    monkeypatch.setattr(provider, "_ensure_base_image", _ensure_base_image)
    monkeypatch.setattr(provider, "_ensure_vm", _ensure_vm)
    monkeypatch.setattr(provider, "_ensure_ssh", _ensure_ssh)
    monkeypatch.setattr(provider, "_ensure_workspace_share_mount", _ensure_workspace_share_mount)
    monkeypatch.setattr(provider, "_prepare_session", _prepare_session)
    monkeypatch.setattr(provider, "restart_browser", _restart_browser)
    monkeypatch.setattr(provider, "restart_terminal", _restart_terminal)

    asyncio.run(provider.activate_session("session-a"))
    asyncio.run(provider.activate_session("session-a"))
    asyncio.run(provider.activate_session("session-b"))

    assert browser_calls == ["session-a", "session-b"]
    assert terminal_calls == ["session-a", "session-b"]


def test_qemu_session_client_detached_user_command_redirects_in_session_shell() -> None:
    captured: dict[str, object] = {}

    class _FakeSSH:
        async def run_detached_script(self, script: str, *, stdout_path: str, stderr_path: str, shell_prefix: str = "bash -lc"):
            captured["script"] = script
            captured["stdout_path"] = stdout_path
            captured["stderr_path"] = stderr_path
            captured["shell_prefix"] = shell_prefix
            return 321

        async def run_detached(self, command: str, *, stdout_path: str, stderr_path: str, cwd=None, env=None, as_root: bool = False):
            captured["root_command"] = command
            captured["root_as_root"] = as_root
            return 999

    client = QemuSessionClient(
        ssh=_FakeSSH(),  # type: ignore[arg-type]
        session_user="ssn-example",
        workspace_path="/srv/sentinel/sessions/example/workspace",
    )

    pid = asyncio.run(
        client.run_detached(
            "curl -fsSL https://bun.sh/install | bash",
            stdout_path="/srv/sentinel/sessions/example/workspace/.runtime/logs/out.log",
            stderr_path="/srv/sentinel/sessions/example/workspace/.runtime/logs/err.log",
            cwd="/srv/sentinel/sessions/example/workspace",
            env={"HOME": "/srv/sentinel/sessions/example/workspace"},
        )
    )

    assert pid == 321
    assert captured["shell_prefix"] == "sudo -u ssn-example bash -lc"
    assert "exec >" not in str(captured["script"])
    assert "export HOME='/srv/sentinel/sessions/example/workspace';" in str(captured["script"])
    assert "cd '/srv/sentinel/sessions/example/workspace' &&" in str(captured["script"])
    assert "curl -fsSL https://bun.sh/install | bash" in str(captured["script"])
