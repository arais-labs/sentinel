from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from shlex import quote
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.manager import Runtime
from app.schemas.runtimes import (
    RuntimeActionResponse,
    RuntimeCapabilitiesResponse,
    RuntimeCreateRequest,
    RuntimeJobEvent,
    RuntimeJobResponse,
    RuntimeProvider,
    RuntimeProviderCapability,
    RuntimeProviderConfig,
)
from app.services.runtime.runtimes import (
    create_runtime,
    runtime_config_status_detail,
    runtime_response,
)
from app.services.runtime.provisioning.assets import ansible_config_path, ansible_playbook_path

logger = logging.getLogger(__name__)


class RuntimeProviderError(RuntimeError):
    pass


class RuntimeJobNotFound(RuntimeProviderError):
    pass


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(slots=True)
class RuntimeJob:
    id: UUID
    runtime_id: UUID | None
    provider: RuntimeProvider
    action: str
    status: str = "queued"
    events: list[RuntimeJobEvent] = field(default_factory=list)
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def emit(self, message: str, *, level: str = "info") -> None:
        self.events.append(
            RuntimeJobEvent(
                timestamp=datetime.now(UTC),
                level="error" if level == "error" else "info",
                message=message,
            )
        )

    def response(self) -> RuntimeJobResponse:
        return RuntimeJobResponse(
            id=self.id,
            runtime_id=self.runtime_id,
            provider=self.provider,
            action=self.action,
            status=self.status,  # type: ignore[arg-type]
            events=list(self.events),
            error=self.error,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )


class CommandRunner:
    async def run(
        self,
        argv: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int = 900,
    ) -> CommandResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        return CommandResult(
            returncode=int(process.returncode or 0),
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )


class RuntimeProviderBackend:
    name: RuntimeProvider
    label: str

    def capability(self) -> RuntimeProviderCapability:
        missing = self.missing_requirements()
        return RuntimeProviderCapability(
            provider=self.name,
            available=not missing,
            label=self.label,
            detail="Available" if not missing else "Missing required local runtime capabilities.",
            missing=missing,
        )

    def missing_requirements(self) -> list[str]:
        raise NotImplementedError

    async def create(
        self, runtime: Runtime, config: RuntimeProviderConfig, job: RuntimeJob
    ) -> None:
        raise NotImplementedError

    async def start(self, runtime: Runtime, job: RuntimeJob) -> None:
        raise NotImplementedError

    async def stop(self, runtime: Runtime, job: RuntimeJob) -> None:
        raise NotImplementedError

    async def delete(self, runtime: Runtime, job: RuntimeJob) -> None:
        raise NotImplementedError

    async def rebuild(
        self, runtime: Runtime, config: RuntimeProviderConfig, job: RuntimeJob
    ) -> None:
        await self.delete(runtime, job)
        await self.create(runtime, config, job)

    async def status_detail(self, runtime: Runtime) -> str | None:
        return None


class LocalProviderBase(RuntimeProviderBackend):
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or CommandRunner()

    async def _run(
        self,
        argv: Sequence[str],
        job: RuntimeJob,
        *,
        label: str | None = None,
        expose_output: bool = False,
        env: dict[str, str] | None = None,
        timeout: int = 900,
    ) -> CommandResult:
        if label:
            job.emit(label)
        command = " ".join(quote(part) for part in argv)
        logger.debug("Running runtime provider command: %s", command)
        result = await self._runner.run(argv, env=env, timeout=timeout)
        if expose_output:
            for line in result.stdout.splitlines():
                if line.strip():
                    job.emit(line)
            for line in result.stderr.splitlines():
                if line.strip():
                    job.emit(line, level="error" if result.returncode else "info")
        if result.returncode != 0:
            logger.error(
                "Runtime provider command failed (%s): %s\n%s",
                result.returncode,
                command,
                result.stderr or result.stdout,
            )
            raise RuntimeProviderError(
                f"Command failed during {label or 'runtime provider operation'} "
                f"({result.returncode}): {result.stderr or result.stdout}"
            )
        return result

    @staticmethod
    def _ansible_assets() -> tuple[Path | None, Path | None]:
        playbook = ansible_playbook_path()
        config = ansible_config_path()
        return playbook if playbook.exists() else None, config if config.exists() else None

    def _shared_missing(self) -> list[str]:
        missing: list[str] = []
        if shutil.which("ansible-playbook") is None:
            missing.append("Provisioning engine")
        playbook, _config = self._ansible_assets()
        if playbook is None:
            missing.append("Runtime provisioning profile")
        return missing


class LimaRuntimeProvider(LocalProviderBase):
    name: RuntimeProvider = "lima"
    label = "Lima VM"

    def missing_requirements(self) -> list[str]:
        missing = self._shared_missing()
        if shutil.which("limactl") is None:
            missing.append("Lima")
        if (
            _find_runtime_asset(
                explicit=settings.runtime_lima_yaml,
                candidates=(
                    "infra/runtime/lima/sentinel-runtime.yaml",
                    "/infra/runtime/lima/sentinel-runtime.yaml",
                ),
            )
            is None
        ):
            missing.append("Lima runtime profile")
        return missing

    async def create(
        self, runtime: Runtime, config: RuntimeProviderConfig, job: RuntimeJob
    ) -> None:
        lima_yaml = _find_runtime_asset(
            explicit=settings.runtime_lima_yaml,
            candidates=(
                "infra/runtime/lima/sentinel-runtime.yaml",
                "/infra/runtime/lima/sentinel-runtime.yaml",
            ),
        )
        playbook, ansible_config = self._ansible_assets()
        if lima_yaml is None or playbook is None:
            raise RuntimeProviderError("Missing Lima or Ansible runtime assets.")

        args = ["limactl", "create", "--yes", "--name", runtime.name]
        if config.cpus is not None:
            args.extend(["--cpus", str(config.cpus)])
        if config.memory:
            args.extend(["--memory", _size_to_gib(config.memory)])
        if config.disk:
            args.extend(["--disk", _size_to_gib(config.disk)])
        args.append(str(lima_yaml))
        await self._run(args, job, label="Creating Lima runtime", timeout=3600)
        await self.start(runtime, job)

        ssh_config = (
            await self._run(
                ["limactl", "list", "--format", "{{.SSHConfigFile}}", runtime.name],
                job,
                label="Inspecting Lima SSH configuration",
                timeout=30,
            )
        ).stdout.strip()
        env = dict(os.environ)
        if ansible_config is not None:
            env["ANSIBLE_CONFIG"] = str(ansible_config)
        await self._run(
            [
                "ansible-playbook",
                "-i",
                f"lima-{runtime.name},",
                "--ssh-common-args",
                f"-F {ssh_config}",
                str(playbook),
                "-e",
                f"sentinel_desktop={config.desktop}",
            ],
            job,
            label="Provisioning runtime environment",
            env=env,
            timeout=3600,
        )
        ssh = await self._lima_ssh_target(runtime.name)
        runtime.host = ssh["host"]
        runtime.port = int(ssh["port"])
        runtime.username = ssh["username"]
        runtime.workspaces_dir = f"/home/{runtime.username}/sentinel/workspaces"
        runtime.auth_type = "private_key"
        runtime.encrypted_secret = Path(ssh["identity_file"]).read_text()
        runtime.provider_state = {
            "lima_name": runtime.name,
            "ssh_config": ssh_config,
            "desktop": config.desktop,
        }

    async def start(self, runtime: Runtime, job: RuntimeJob) -> None:
        await self._run(
            ["limactl", "start", runtime.name],
            job,
            label="Starting Lima runtime",
            timeout=900,
        )

    async def stop(self, runtime: Runtime, job: RuntimeJob) -> None:
        await self._run(
            ["limactl", "stop", runtime.name],
            job,
            label="Stopping Lima runtime",
            timeout=900,
        )

    async def delete(self, runtime: Runtime, job: RuntimeJob) -> None:
        await self._run(
            ["limactl", "delete", "--force", runtime.name],
            job,
            label="Deleting Lima runtime",
            timeout=900,
        )

    async def _lima_ssh_target(self, name: str) -> dict[str, str]:
        result = await self._runner.run(
            ["limactl", "show-ssh", "--format=options", name], timeout=30
        )
        if result.returncode != 0:
            raise RuntimeProviderError(
                result.stderr or result.stdout or "Failed to inspect Lima SSH options."
            )
        options: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, _, value = line.partition("=")
            if key and value:
                options[key.strip()] = value.strip().strip('"')
        return {
            "host": options.get("Hostname", "127.0.0.1"),
            "port": options.get("Port", "22"),
            "username": options.get("User", "lima"),
            "identity_file": options.get("IdentityFile", ""),
        }

    async def status_detail(self, runtime: Runtime) -> str | None:
        name = str((runtime.provider_state or {}).get("lima_name") or runtime.name)
        try:
            result = await self._runner.run(
                ["limactl", "list", "--format", "{{.Status}}", name],
                timeout=30,
            )
        except OSError as exc:
            return str(exc)
        output = (result.stderr or result.stdout).strip()
        if result.returncode != 0:
            return None if runtime.status == "deleted" else output or None
        status = result.stdout.strip()
        if status and status.lower() != "running" and runtime.status not in {"stopped", "deleted"}:
            return f"limactl status={status}"
        return None


class DockerRuntimeProvider(LocalProviderBase):
    name: RuntimeProvider = "docker"
    label = "Docker Linux"

    def missing_requirements(self) -> list[str]:
        missing = self._shared_missing()
        if shutil.which("docker") is None:
            missing.append("Docker")
        elif not _docker_cli_available():
            missing.append("Docker daemon")
        if not os.environ.get("DOCKER_HOST") and not Path("/var/run/docker.sock").exists():
            missing.append("Docker socket")
        return missing

    async def create(
        self, runtime: Runtime, config: RuntimeProviderConfig, job: RuntimeJob
    ) -> None:
        playbook, ansible_config = self._ansible_assets()
        if playbook is None:
            raise RuntimeProviderError("Missing Ansible runtime playbook.")
        await self._remove_container_if_exists(runtime.name, job)
        state_dir = Path(tempfile.gettempdir()) / "sentinel-runtimes" / runtime.name
        state_dir.mkdir(parents=True, exist_ok=True)
        key_path = state_dir / "id_ed25519"
        if not key_path.exists():
            await self._run(
                [
                    "ssh-keygen",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-f",
                    str(key_path),
                    "-C",
                    f"sentinel-{runtime.name}",
                ],
                job,
                label="Generating runtime SSH identity",
            )
        public_key = (state_dir / "id_ed25519.pub").read_text().strip()

        image = settings.runtime_docker_base_image
        workspace_volume = self._workspace_volume_name(runtime.name)
        workspace_dir = "/home/sentinel/sentinel/workspaces"
        compose_project = self._compose_project_name()
        compose_network = f"{compose_project}_default"
        docker_run = [
            "docker",
            "run",
            "-d",
            "--name",
            runtime.name,
            "--label",
            "sentinel.runtime=true",
            "--label",
            f"sentinel.runtime_id={runtime.id}",
            "--label",
            f"com.docker.compose.project={compose_project}",
            "--label",
            f"com.docker.compose.service=runtime-{runtime.name}",
            "--label",
            "com.docker.compose.oneoff=False",
            "--privileged",
            "-p",
            "127.0.0.1::22",
            "-v",
            f"{workspace_volume}:{workspace_dir}",
        ]
        if await self._docker_network_exists(compose_network):
            docker_run.extend(["--network", compose_network])
        docker_run.extend([image, "sleep", "infinity"])
        await self._run(
            ["docker", "pull", image],
            job,
            label="Pulling Docker runtime image",
            timeout=1800,
        )
        await self._run(
            ["docker", "volume", "create", workspace_volume],
            job,
            label="Preparing runtime workspace volume",
            timeout=120,
        )
        await self._run(docker_run, job, label="Creating Docker runtime container")
        await self._run(
            [
                "docker",
                "exec",
                runtime.name,
                "bash",
                "-lc",
                (
                    "set -euo pipefail; "
                    "apt-get update; "
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
                    "openssh-server sudo python3 ca-certificates; "
                    "useradd -m -s /bin/bash sentinel 2>/dev/null || true; "
                    "install -d -o sentinel -g sentinel -m 0755 /home/sentinel; "
                    "install -d -o sentinel -g sentinel -m 0700 /home/sentinel/.ssh; "
                    "install -d -o sentinel -g sentinel -m 0755 /home/sentinel/sentinel; "
                    f"install -d -o sentinel -g sentinel -m 0755 {quote(workspace_dir)}; "
                    "mkdir -p /run/sshd; "
                    f"printf '%s\\n' {quote(public_key)} > /home/sentinel/.ssh/authorized_keys; "
                    "chown sentinel:sentinel /home/sentinel/.ssh/authorized_keys; "
                    "chmod 700 /home/sentinel/.ssh; chmod 600 /home/sentinel/.ssh/authorized_keys; "
                    "printf 'sentinel ALL=(ALL) NOPASSWD:ALL\\n' >/etc/sudoers.d/sentinel; "
                    "chmod 440 /etc/sudoers.d/sentinel; "
                    "/usr/sbin/sshd"
                ),
            ],
            job,
            label="Preparing runtime SSH access",
            timeout=1800,
        )
        port = await self._docker_ssh_port(runtime.name)
        env = dict(os.environ)
        if ansible_config is not None:
            env["ANSIBLE_CONFIG"] = str(ansible_config)
        await self._run(
            [
                "ansible-playbook",
                "-i",
                f"{settings.runtime_docker_ssh_host},",
                "--user",
                "sentinel",
                "--private-key",
                str(key_path),
                "--ssh-common-args",
                f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p {port}",
                str(playbook),
                "-e",
                f"sentinel_desktop={config.desktop}",
                "-e",
                "sentinel_home_override=/home/sentinel",
                "-e",
                f"sentinel_workspaces_dir={workspace_dir}",
                "-e",
                "sentinel_runtime_container=true",
            ],
            job,
            label="Provisioning runtime environment",
            env=env,
            timeout=3600,
        )
        runtime.host = settings.runtime_docker_ssh_host
        runtime.port = port
        runtime.username = "sentinel"
        runtime.workspaces_dir = workspace_dir
        runtime.auth_type = "private_key"
        runtime.encrypted_secret = key_path.read_text()
        runtime.provider_state = {
            "container_name": runtime.name,
            "workspace_volume": workspace_volume,
            "desktop": config.desktop,
        }

    async def start(self, runtime: Runtime, job: RuntimeJob) -> None:
        await self._run(["docker", "start", runtime.name], job, label="Starting Docker runtime")
        await self._ensure_sshd(runtime.name, job)

    async def stop(self, runtime: Runtime, job: RuntimeJob) -> None:
        await self._run(["docker", "stop", runtime.name], job, label="Stopping Docker runtime")

    async def delete(self, runtime: Runtime, job: RuntimeJob) -> None:
        await self._remove_container_if_exists(runtime.name, job)
        volume = (runtime.provider_state or {}).get(
            "workspace_volume"
        ) or self._workspace_volume_name(runtime.name)
        await self._remove_volume_if_exists(str(volume), job)

    async def rebuild(
        self, runtime: Runtime, config: RuntimeProviderConfig, job: RuntimeJob
    ) -> None:
        await self._remove_container_if_exists(runtime.name, job)
        await self.create(runtime, config, job)

    async def _docker_ssh_port(self, name: str) -> int:
        result = await self._runner.run(["docker", "port", name, "22/tcp"], timeout=30)
        if result.returncode != 0:
            raise RuntimeProviderError(
                result.stderr or result.stdout or "Failed to inspect Docker SSH port."
            )
        first = result.stdout.strip().splitlines()[0]
        return int(first.rsplit(":", 1)[-1])

    async def status_detail(self, runtime: Runtime) -> str | None:
        name = str((runtime.provider_state or {}).get("container_name") or runtime.name)
        try:
            result = await self._runner.run(
                [
                    "docker",
                    "container",
                    "inspect",
                    "--format",
                    "{{.State.Status}}\t{{.State.Error}}",
                    name,
                ],
                timeout=30,
            )
        except OSError as exc:
            return str(exc)
        output = (result.stderr or result.stdout).strip()
        if result.returncode != 0:
            return None if runtime.status == "deleted" else output or None
        status, _separator, error = result.stdout.strip().partition("\t")
        if error.strip():
            return error.strip()
        if status and status.lower() != "running" and runtime.status not in {"stopped", "deleted"}:
            return f"docker inspect State.Status={status}"
        return None

    async def _ensure_sshd(self, name: str, job: RuntimeJob) -> None:
        await self._run(
            [
                "docker",
                "exec",
                name,
                "bash",
                "-lc",
                "mkdir -p /run/sshd; pgrep -x sshd >/dev/null || /usr/sbin/sshd",
            ],
            job,
            label="Ensuring runtime SSH service is available",
            timeout=30,
        )

    async def _remove_container_if_exists(self, name: str, job: RuntimeJob) -> None:
        result = await self._runner.run(["docker", "container", "inspect", name], timeout=30)
        if result.returncode == 0:
            await self._run(
                ["docker", "rm", "-f", name],
                job,
                label="Removing existing Docker runtime container",
                timeout=120,
            )

    async def _remove_volume_if_exists(self, name: str, job: RuntimeJob) -> None:
        result = await self._runner.run(["docker", "volume", "inspect", name], timeout=30)
        if result.returncode == 0:
            await self._run(
                ["docker", "volume", "rm", "-f", name],
                job,
                label="Removing runtime workspace volume",
                timeout=120,
            )

    async def _docker_network_exists(self, name: str) -> bool:
        result = await self._runner.run(["docker", "network", "inspect", name], timeout=30)
        return result.returncode == 0

    @staticmethod
    def _compose_project_name() -> str:
        return os.environ.get("COMPOSE_PROJECT_NAME") or "sentinel"

    @staticmethod
    def _workspace_volume_name(name: str) -> str:
        safe = "".join(char if char.isalnum() or char in "_.-" else "-" for char in name)
        return f"sentinel-runtime-{safe}-workspaces"


class RuntimeProviderService:
    def __init__(self) -> None:
        self._providers: dict[str, RuntimeProviderBackend] = {
            "lima": LimaRuntimeProvider(),
            "docker": DockerRuntimeProvider(),
        }
        self._jobs: dict[UUID, RuntimeJob] = {}

    def capabilities(self) -> RuntimeCapabilitiesResponse:
        return RuntimeCapabilitiesResponse(
            providers=[
                RuntimeProviderCapability(
                    provider="ssh",
                    available=True,
                    label="Custom SSH",
                    detail="Available",
                    missing=[],
                ),
                *[provider.capability() for provider in self._providers.values()],
            ]
        )

    async def create_managed(
        self, db: AsyncSession, payload: RuntimeCreateRequest
    ) -> RuntimeActionResponse:
        provider = self._require_provider(payload.provider)
        runtime = await create_runtime(db, payload)
        job = self._start_job(
            runtime,
            provider,
            "create",
            RuntimeProviderConfig.model_validate(runtime.provider_config or {}),
        )
        runtime.last_job_id = job.id
        runtime.last_job_status = job.status
        await db.commit()
        await db.refresh(runtime)
        return RuntimeActionResponse(runtime=runtime_response(runtime), job=job.response())

    async def action(
        self, db: AsyncSession, runtime: Runtime, action: str
    ) -> RuntimeActionResponse:
        provider = self._require_provider(runtime.provider)
        job = self._start_job(runtime, provider, action, None)
        runtime.last_job_id = job.id
        runtime.last_job_status = job.status
        if action == "delete":
            runtime.status = "deleted"
        await db.commit()
        await db.refresh(runtime)
        return RuntimeActionResponse(runtime=runtime_response(runtime), job=job.response())

    def get_job(self, job_id: UUID) -> RuntimeJobResponse:
        job = self._jobs.get(job_id)
        if job is None:
            raise RuntimeJobNotFound("Runtime job not found.")
        return job.response()

    async def runtime_response(self, runtime: Runtime) -> RuntimeResponse:
        status_detail = await self.runtime_status_detail(runtime)
        if status_detail is None:
            status_detail = runtime_config_status_detail(runtime)
        return runtime_response(runtime, status_detail=status_detail)

    async def runtime_status_detail(self, runtime: Runtime) -> str | None:
        provider = self._providers.get(runtime.provider)
        if provider is None:
            return None
        return await provider.status_detail(runtime)

    async def delete_managed_resources(self, runtime: Runtime) -> None:
        provider = self._providers.get(runtime.provider)
        if provider is None:
            return
        job = RuntimeJob(
            id=uuid4(),
            runtime_id=runtime.id,
            provider=provider.name,
            action="delete",
            status="running",
        )
        try:
            await provider.delete(runtime, job)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeProviderError(str(exc)) from exc

    def _require_provider(self, provider_name: str) -> RuntimeProviderBackend:
        provider = self._providers.get(provider_name)
        if provider is None:
            raise RuntimeProviderError(f"Unsupported runtime provider: {provider_name}")
        missing = provider.missing_requirements()
        if missing:
            raise RuntimeProviderError("Runtime provider unavailable: " + ", ".join(missing))
        return provider

    def _start_job(
        self,
        runtime: Runtime,
        provider: RuntimeProviderBackend,
        action: str,
        config: RuntimeProviderConfig | None,
    ) -> RuntimeJob:
        job = RuntimeJob(
            id=uuid4(),
            runtime_id=runtime.id,
            provider=provider.name,
            action=action,
            status="queued",
        )
        self._jobs[job.id] = job
        asyncio.create_task(self._run_job(job, runtime.id, provider, action, config))
        return job

    async def _run_job(
        self,
        job: RuntimeJob,
        runtime_id: UUID,
        provider: RuntimeProviderBackend,
        action: str,
        config: RuntimeProviderConfig | None,
    ) -> None:
        job.status = "running"
        job.emit(f"Starting runtime {action}.")
        async with AsyncSessionLocal() as db:
            runtime = await db.get(Runtime, runtime_id)
            if runtime is None:
                job.status = "failed"
                job.error = "Runtime row disappeared."
                job.finished_at = datetime.now(UTC)
                return
            try:
                if action == "create":
                    if config is None:
                        raise RuntimeProviderError("Create config missing.")
                    await provider.create(runtime, config, job)
                    runtime.status = "ready"
                elif action == "start":
                    await provider.start(runtime, job)
                    runtime.status = "running"
                elif action == "stop":
                    await provider.stop(runtime, job)
                    runtime.status = "stopped"
                elif action == "delete":
                    await provider.delete(runtime, job)
                    runtime.status = "deleted"
                elif action == "rebuild":
                    await provider.rebuild(
                        runtime,
                        RuntimeProviderConfig.model_validate(runtime.provider_config or {}),
                        job,
                    )
                    runtime.status = "ready"
                else:
                    raise RuntimeProviderError(f"Unsupported action: {action}")
                job.status = "succeeded"
                job.emit("Runtime job completed.")
            except Exception as exc:  # noqa: BLE001
                runtime.status = "error"
                job.status = "failed"
                job.error = str(exc)
                job.emit(str(exc), level="error")
            finally:
                job.finished_at = datetime.now(UTC)
                runtime.last_job_id = job.id
                runtime.last_job_status = job.status
                await db.commit()


def _find_runtime_asset(*, explicit: str, candidates: Sequence[str]) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.exists() else None
    roots = [Path.cwd(), *Path(__file__).resolve().parents]
    for candidate in candidates:
        path = Path(candidate)
        if path.is_absolute() and path.exists():
            return path
        for root in roots:
            resolved = root / candidate
            if resolved.exists():
                return resolved
    return None


def _size_to_gib(value: str) -> str:
    trimmed = value.strip()
    if trimmed.lower().endswith("gib"):
        return trimmed[:-3]
    if trimmed.lower().endswith("gb"):
        return trimmed[:-2]
    if trimmed.lower().endswith("g"):
        return trimmed[:-1]
    return trimmed


def _docker_cli_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


runtime_provider_service = RuntimeProviderService()
