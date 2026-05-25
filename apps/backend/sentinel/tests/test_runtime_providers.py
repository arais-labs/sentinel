from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.config import settings
from app.models.manager import Runtime
from app.schemas.runtimes import RuntimeProviderConfig
from app.services.runtime.providers import CommandResult, DockerRuntimeProvider, RuntimeJob
from app.services.runtime.target_secrets import decrypt_runtime_secret


class _DockerRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    async def run(self, argv, *, env=None, timeout=900):  # noqa: ANN001
        command = list(argv)
        self.commands.append(command)
        if command[0] == "ssh-keygen":
            key_path = Path(command[command.index("-f") + 1])
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_text("PRIVATE KEY\n")
            key_path.with_suffix(key_path.suffix + ".pub").write_text("PUBLIC KEY\n")
        if command[:3] == ["docker", "port", "runtime-one"]:
            return CommandResult(returncode=0, stdout="127.0.0.1:49154\n", stderr="")
        if command[:3] == ["docker", "container", "inspect"]:
            return CommandResult(returncode=1, stdout="", stderr="")
        if command[:3] == ["docker", "volume", "inspect"]:
            return CommandResult(returncode=1, stdout="", stderr="")
        return CommandResult(returncode=0, stdout="", stderr="")


@pytest.mark.asyncio
async def test_docker_runtime_create_uses_volume_and_packaged_ansible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "runtime_docker_base_image", "debian:trixie")
    monkeypatch.setattr(settings, "runtime_docker_ssh_host", "host.docker.internal")
    runner = _DockerRunner()
    provider = DockerRuntimeProvider(runner=runner)  # type: ignore[arg-type]
    runtime = Runtime(
        id=uuid4(),
        name="runtime-one",
        provider="docker",
        status="creating",
        provider_config={},
        provider_state={},
    )
    job = RuntimeJob(id=uuid4(), runtime_id=runtime.id, provider="docker", action="create")

    await provider.create(runtime, RuntimeProviderConfig(desktop="xfce"), job)

    joined_commands = [" ".join(command) for command in runner.commands]
    docker_run = next(command for command in runner.commands if command[:2] == ["docker", "run"])
    ansible = next(command for command in runner.commands if command[0] == "ansible-playbook")

    assert "--privileged" in docker_run
    assert "--network" in docker_run
    assert "sentinel_default" in docker_run
    assert "sentinel.runtime=true" in docker_run
    assert f"sentinel.runtime_id={runtime.id}" in docker_run
    assert "com.docker.compose.project=sentinel" in docker_run
    assert "com.docker.compose.service=runtime-runtime-one" in docker_run
    assert "-v" in docker_run
    assert (
        "sentinel-runtime-runtime-one-workspaces:/home/sentinel/sentinel/workspaces" in docker_run
    )
    bootstrap = next(
        command for command in runner.commands if command[:3] == ["docker", "exec", "runtime-one"]
    )
    bootstrap_script = bootstrap[-1]
    assert "install -d -o sentinel -g sentinel -m 0755 /home/sentinel" in bootstrap_script
    assert "install -d -o sentinel -g sentinel -m 0700 /home/sentinel/.ssh" in bootstrap_script
    assert all("docker commit" not in command for command in joined_commands)
    assert "-e" in ansible
    assert "sentinel_runtime_container=true" in ansible
    assert "sentinel_workspaces_dir=/home/sentinel/sentinel/workspaces" in ansible
    assert runtime.host == "host.docker.internal"
    assert runtime.port == 49154
    assert runtime.username == "sentinel"
    assert runtime.workspaces_dir == "/home/sentinel/sentinel/workspaces"
    assert runtime.provider_state["workspace_volume"] == "sentinel-runtime-runtime-one-workspaces"
    assert decrypt_runtime_secret(runtime.encrypted_secret or "") == "PRIVATE KEY\n"
    job_event_text = "\n".join(event.message for event in job.events)
    assert "Preparing runtime SSH access" in job_event_text
    assert "Provisioning runtime environment" in job_event_text
    assert "docker exec" not in job_event_text
    assert "PUBLIC KEY" not in job_event_text
    assert "authorized_keys" not in job_event_text
    assert "apt-get install" not in job_event_text


@pytest.mark.asyncio
async def test_docker_runtime_rebuild_preserves_workspace_volume() -> None:
    runner = _DockerRunner()
    provider = DockerRuntimeProvider(runner=runner)  # type: ignore[arg-type]
    runtime = Runtime(
        id=uuid4(),
        name="runtime-one",
        provider="docker",
        status="ready",
        provider_config={},
        provider_state={"workspace_volume": "sentinel-runtime-runtime-one-workspaces"},
    )
    job = RuntimeJob(id=uuid4(), runtime_id=runtime.id, provider="docker", action="rebuild")

    await provider.rebuild(runtime, RuntimeProviderConfig(desktop="xfce"), job)

    assert not any(command[:3] == ["docker", "volume", "rm"] for command in runner.commands)
