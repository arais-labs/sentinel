from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

import pytest

from app.config import settings
from app.services.runtime.docker import DockerRuntimeProvider


class _FakeSSHClient:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    async def wait_ready(self, *, timeout: int = 60) -> None:
        _ = timeout


def _seed_runtime_keys(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "id_ed25519").write_text("private", encoding="utf-8")
    (path / "id_ed25519.pub").write_text("ssh-ed25519 public sentinel-runtime", encoding="utf-8")


def test_docker_runtime_uses_host_mount_and_backend_workspace(tmp_path, monkeypatch):
    session_id = "00000000-0000-0000-0000-000000000001"
    backend_root = tmp_path / "backend-workspaces"
    host_root = "/host/sentinel/runtime/workspaces"
    ssh_dir = tmp_path / "ssh"
    _seed_runtime_keys(ssh_dir)

    monkeypatch.setattr(settings, "runtime_ssh_key_dir", str(ssh_dir))
    monkeypatch.setattr(settings, "session_runtime_base_dir", str(backend_root))
    monkeypatch.setattr(settings, "runtime_workspaces_host_dir", host_root)
    monkeypatch.setattr(settings, "runtime_image", "sentinel-runtime")
    monkeypatch.setattr(settings, "runtime_docker_network", "sentinel_default")
    monkeypatch.setattr(settings, "runtime_memory_limit", "2g")
    monkeypatch.setattr(settings, "runtime_cpu_limit", 2.0)
    monkeypatch.setattr("app.services.runtime.docker.SSHClient", _FakeSSHClient)

    commands: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        _ = args, kwargs
        commands.append(list(cmd))
        if cmd[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(cmd, 1, "", "missing")
        if cmd[:3] == ["docker", "rm", "-f"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(cmd, 0, "container123\n", "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("app.services.runtime.docker.subprocess.run", fake_run)
    monkeypatch.setattr(
        DockerRuntimeProvider,
        "_get_container_ip",
        lambda self, container_id: asyncio.sleep(0, result="172.18.0.9"),
    )

    provider = DockerRuntimeProvider()
    runtime = asyncio.run(provider.ensure(session_id))

    run_cmd = next(cmd for cmd in commands if cmd[:2] == ["docker", "run"])
    assert runtime.workspace_path == "/home/sentinel/workspace"
    assert backend_root.joinpath(session_id, "workspace").is_dir()
    assert f"{host_root}/{session_id}/workspace:/home/sentinel/workspace" in run_cmd


def test_docker_runtime_failure_surfaces_stderr_and_removes_created_container(tmp_path, monkeypatch, caplog):
    session_id = "00000000-0000-0000-0000-000000000001"
    ssh_dir = tmp_path / "ssh"
    _seed_runtime_keys(ssh_dir)

    monkeypatch.setattr(settings, "runtime_ssh_key_dir", str(ssh_dir))
    monkeypatch.setattr(settings, "session_runtime_base_dir", str(tmp_path / "backend-workspaces"))
    monkeypatch.setattr(settings, "runtime_workspaces_host_dir", "/host/sentinel/runtime/workspaces")
    monkeypatch.setattr(settings, "runtime_image", "sentinel-runtime")
    monkeypatch.setattr(settings, "runtime_docker_network", "sentinel_default")

    commands: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        _ = args, kwargs
        commands.append(list(cmd))
        if cmd[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(cmd, 1, "", "missing")
        if cmd[:3] == ["docker", "rm", "-f"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(cmd, 125, "", "bad bind mount")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("app.services.runtime.docker.subprocess.run", fake_run)

    provider = DockerRuntimeProvider()
    with caplog.at_level(logging.ERROR, logger="app.services.runtime.docker"):
        with pytest.raises(RuntimeError, match="bad bind mount"):
            asyncio.run(provider.ensure(session_id))

    remove_commands = [cmd for cmd in commands if cmd[:3] == ["docker", "rm", "-f"]]
    assert len(remove_commands) == 2
    assert "bad bind mount" in caplog.text
