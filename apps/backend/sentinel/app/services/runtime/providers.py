from __future__ import annotations

import asyncio
import getpass
import platform
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.manager import Machine
from app.schemas.machines import (
    MachineLifecycleResponse,
    MachineResponse,
    MachineCapabilitiesResponse,
    MachineCreateRequest,
    MachineJobEvent,
    MachineJobResponse,
    MachineProvider,
    MachineProviderCapability,
    MachineProviderConfig,
)
from app.services.runtime.machines import (
    create_machine,
    machine_config_status_detail,
    machine_response,
)


class MachineProviderError(RuntimeError):
    pass


class MachineJobNotFound(MachineProviderError):
    pass


@dataclass(slots=True)
class MachineJob:
    id: UUID
    machine_id: UUID | None
    provider: MachineProvider
    action: str
    status: str = "queued"
    events: list[MachineJobEvent] = field(default_factory=list)
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def emit(self, message: str, *, level: str = "info") -> None:
        self.events.append(
            MachineJobEvent(
                timestamp=datetime.now(UTC),
                level="error" if level == "error" else "info",
                message=message,
            )
        )

    def response(self) -> MachineJobResponse:
        return MachineJobResponse(
            id=self.id,
            machine_id=self.machine_id,
            provider=self.provider,
            action=self.action,
            status=self.status,  # type: ignore[arg-type]
            events=list(self.events),
            error=self.error,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )


class MachineProviderBackend:
    name: MachineProvider
    label: str

    def capability(self) -> MachineProviderCapability:
        missing = self.missing_requirements()
        return MachineProviderCapability(
            provider=self.name,
            available=not missing,
            label=self.label,
            detail="Available" if not missing else "Missing required local runtime capabilities.",
            missing=missing,
        )

    def missing_requirements(self) -> list[str]:
        raise NotImplementedError

    async def create(
        self, runtime: Machine, config: MachineProviderConfig, job: MachineJob
    ) -> None:
        raise NotImplementedError

    async def start(self, runtime: Machine, job: MachineJob) -> None:
        raise NotImplementedError

    async def stop(self, runtime: Machine, job: MachineJob) -> None:
        raise NotImplementedError

    async def delete(self, runtime: Machine, job: MachineJob) -> None:
        raise NotImplementedError

    async def rebuild(
        self, runtime: Machine, config: MachineProviderConfig, job: MachineJob
    ) -> None:
        await self.delete(runtime, job)
        await self.create(runtime, config, job)

    async def status_detail(self, runtime: Machine) -> str | None:
        return None


class LocalMachineProvider(MachineProviderBackend):
    """Registers this Mac. Workspace containers own execution and storage."""

    name: MachineProvider = "local"
    label = "Local (this Mac)"

    def missing_requirements(self) -> list[str]:
        if platform.system() != "Darwin":
            return ["macOS"]
        return []

    def capability(self) -> MachineProviderCapability:
        missing = self.missing_requirements()
        if not missing:
            detail = "Available"
        else:
            detail = "The local runtime is currently supported only on macOS."
        return MachineProviderCapability(
            provider=self.name,
            available=not missing,
            label=self.label,
            detail=detail,
            missing=missing,
            has_lifecycle=False,
        )

    async def create(
        self, runtime: Machine, config: MachineProviderConfig, job: MachineJob
    ) -> None:
        job.emit("Registering this computer")
        runtime.host = None
        runtime.port = None
        runtime.username = getpass.getuser()
        runtime.auth_type = None
        runtime.encrypted_secret = None
        runtime.provider_state = {"local": True}

    async def start(self, runtime: Machine, job: MachineJob) -> None:
        job.emit("Local runtime runs on this machine; nothing to start.")

    async def stop(self, runtime: Machine, job: MachineJob) -> None:
        job.emit("Local runtime runs on this machine; nothing to stop.")

    async def delete(self, runtime: Machine, job: MachineJob) -> None:
        job.emit("Local runtime removed; workspace files are preserved.")

    async def status_detail(self, runtime: Machine) -> str | None:
        return None


class MachineProviderService:
    def __init__(self) -> None:
        self._providers: dict[str, MachineProviderBackend] = {
            "local": LocalMachineProvider(),
        }
        self._jobs: dict[UUID, MachineJob] = {}

    def is_managed(self, provider: str) -> bool:
        """Whether the provider runs the managed create/start/stop/delete flow
        (everything except custom SSH). Read from the registry, not a list."""
        return provider in self._providers

    def capabilities(self) -> MachineCapabilitiesResponse:
        return MachineCapabilitiesResponse(
            providers=[
                MachineProviderCapability(
                    provider="ssh",
                    available=True,
                    label="Custom SSH",
                    detail="Available",
                    missing=[],
                    has_lifecycle=False,
                ),
                *[provider.capability() for provider in self._providers.values()],
            ]
        )

    async def create_managed(
        self, db: AsyncSession, payload: MachineCreateRequest
    ) -> MachineLifecycleResponse:
        provider = self._require_provider(payload.provider)
        runtime = await create_machine(db, payload)
        job = self._start_job(
            runtime,
            provider,
            "create",
            MachineProviderConfig.model_validate(runtime.provider_config or {}),
        )
        runtime.last_job_id = job.id
        runtime.last_job_status = job.status
        await db.commit()
        await db.refresh(runtime)
        return MachineLifecycleResponse(machine=machine_response(runtime), job=job.response())

    async def action(
        self, db: AsyncSession, runtime: Machine, action: str
    ) -> MachineLifecycleResponse:
        provider = self._require_provider(runtime.provider)
        job = self._start_job(runtime, provider, action, None)
        runtime.last_job_id = job.id
        runtime.last_job_status = job.status
        if action == "delete":
            runtime.status = "deleted"
        await db.commit()
        await db.refresh(runtime)
        return MachineLifecycleResponse(machine=machine_response(runtime), job=job.response())

    def get_job(self, job_id: UUID) -> MachineJobResponse:
        job = self._jobs.get(job_id)
        if job is None:
            raise MachineJobNotFound("Machine job not found.")
        return job.response()

    async def machine_response(self, runtime: Machine) -> MachineResponse:
        status_detail = await self.machine_status_detail(runtime)
        if status_detail is None:
            status_detail = machine_config_status_detail(runtime)
        return machine_response(runtime, status_detail=status_detail)

    async def machine_status_detail(self, runtime: Machine) -> str | None:
        provider = self._providers.get(runtime.provider)
        if provider is None:
            return None
        return await provider.status_detail(runtime)

    async def delete_managed_resources(self, runtime: Machine) -> None:
        provider = self._providers.get(runtime.provider)
        if provider is None:
            return
        job = MachineJob(
            id=uuid4(),
            machine_id=runtime.id,
            provider=provider.name,
            action="delete",
            status="running",
        )
        try:
            await provider.delete(runtime, job)
        except Exception as exc:  # noqa: BLE001
            raise MachineProviderError(str(exc)) from exc

    def _require_provider(self, provider_name: str) -> MachineProviderBackend:
        provider = self._providers.get(provider_name)
        if provider is None:
            raise MachineProviderError(f"Unsupported runtime provider: {provider_name}")
        missing = provider.missing_requirements()
        if missing:
            raise MachineProviderError("Machine provider unavailable: " + ", ".join(missing))
        return provider

    def _start_job(
        self,
        runtime: Machine,
        provider: MachineProviderBackend,
        action: str,
        config: MachineProviderConfig | None,
    ) -> MachineJob:
        job = MachineJob(
            id=uuid4(),
            machine_id=runtime.id,
            provider=provider.name,
            action=action,
            status="queued",
        )
        self._jobs[job.id] = job
        asyncio.create_task(self._run_job(job, runtime.id, provider, action, config))
        return job

    async def _run_job(
        self,
        job: MachineJob,
        machine_id: UUID,
        provider: MachineProviderBackend,
        action: str,
        config: MachineProviderConfig | None,
    ) -> None:
        job.status = "running"
        job.emit(f"Starting runtime {action}.")
        async with AsyncSessionLocal() as db:
            runtime = await db.get(Machine, machine_id)
            if runtime is None:
                job.status = "failed"
                job.error = "Machine row disappeared."
                job.finished_at = datetime.now(UTC)
                return
            try:
                if action == "create":
                    if config is None:
                        raise MachineProviderError("Create config missing.")
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
                        MachineProviderConfig.model_validate(runtime.provider_config or {}),
                        job,
                    )
                    runtime.status = "ready"
                else:
                    raise MachineProviderError(f"Unsupported action: {action}")
                job.status = "succeeded"
                job.emit("Machine job completed.")
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


machine_provider_service = MachineProviderService()
