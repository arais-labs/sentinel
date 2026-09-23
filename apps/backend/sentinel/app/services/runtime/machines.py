from __future__ import annotations

import getpass
from dataclasses import dataclass
from typing import get_args
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.instance_sessions import instance_session_registry
from app.models import Workspace
from app.models.manager import Machine, SentinelInstance
from app.schemas.machines import (
    MachineCreateRequest,
    MachineProvider,
    MachineProviderConfig,
    MachineProviderState,
    MachineResponse,
    MachineTestRequest,
    MachineUpdateRequest,
)
from app.services.runtime.ssh_client import SSHClient, SSHCredentials
from app.services.secrets import is_invalid_secret

# Valid providers = the MachineProvider declaration (all resolve to SSH targets),
# derived rather than re-listed so a new provider needs no second edit here.
_KNOWN_PROVIDERS = frozenset(get_args(MachineProvider))


class MachineErrorBase(RuntimeError):
    pass


class MachineNotFound(MachineErrorBase):
    pass


class MachineConflict(MachineErrorBase):
    pass


class InstanceRuntimeNotConfigured(MachineErrorBase):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedMachine:
    id: UUID
    name: str
    provider: str
    host: str
    port: int
    username: str
    auth_type: str
    secret: str
    updated_at_marker: str
    host_key: str | None = None
    runtime_root: str | None = None
    worker_id: str | None = None

    def credentials(self) -> SSHCredentials:
        if self.provider == "local" or self.auth_type == "local":
            raise MachineErrorBase(
                "Local machines run directly on this machine and have no SSH credentials."
            )
        return SSHCredentials(
            host_key=self.host_key,
            host=self.host,
            port=self.port,
            username=self.username,
            private_key=self.secret if self.auth_type == "private_key" else None,
            password=self.secret if self.auth_type == "password" else None,
        )


def machine_response(runtime: Machine, *, status_detail: str | None = None) -> MachineResponse:
    return MachineResponse(
        id=runtime.id,
        name=runtime.name,
        provider=runtime.provider,  # type: ignore[arg-type]
        status=runtime.status,  # type: ignore[arg-type]
        profile=runtime.profile,
        host=runtime.host,
        port=runtime.port,
        username=runtime.username,
        auth_type=runtime.auth_type,  # type: ignore[arg-type]
        provider_config=MachineProviderConfig.model_validate(runtime.provider_config or {}),
        provider_state=MachineProviderState.model_validate(runtime.provider_state or {}),
        status_detail=status_detail,
        last_job_id=runtime.last_job_id,
        last_job_status=runtime.last_job_status,  # type: ignore[arg-type]
        created_at=runtime.created_at,
        updated_at=runtime.updated_at,
    )


def machine_config_status_detail(runtime: Machine) -> str | None:
    try:
        resolve_machine_secret(runtime)
    except MachineErrorBase as exc:
        return str(exc)
    return None


async def list_machines(db: AsyncSession) -> list[Machine]:
    result = await db.execute(select(Machine).order_by(Machine.name))
    return list(result.scalars().all())


async def get_machine(db: AsyncSession, machine_id: UUID) -> Machine:
    runtime = await db.get(Machine, machine_id)
    if runtime is None:
        raise MachineNotFound("Machine not found.")
    return runtime


async def create_machine(
    db: AsyncSession,
    payload: MachineCreateRequest,
) -> Machine:
    runtime = Machine(
        name=payload.name.strip(),
        provider=payload.provider,
        status="ready" if payload.provider == "ssh" else "creating",
        profile=(payload.profile or payload.provider).strip() or payload.provider,
        host=payload.host.strip() if payload.host else None,
        port=int(payload.port or 22) if payload.host else None,
        username=payload.username.strip() if payload.username else None,
        auth_type=payload.auth_type,
        encrypted_secret=(
            _payload_secret(payload.auth_type, payload.private_key, payload.password)
            if payload.auth_type is not None
            else None
        ),
        provider_config=payload.provider_config.model_dump(mode="json"),
        provider_state={},
    )
    db.add(runtime)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise MachineConflict("A machine with that name already exists.") from exc
    await db.refresh(runtime)
    return runtime


async def update_machine(
    db: AsyncSession,
    machine_id: UUID,
    payload: MachineUpdateRequest,
) -> Machine:
    runtime = await get_machine(db, machine_id)
    if payload.username is not None and payload.username.strip() != runtime.username:
        # A different account owns a different worker. Address changes retain the
        # pinned SSH identity; connecting to another host fails verification.
        runtime.provider_config = {}
    if payload.name is not None:
        runtime.name = payload.name.strip()
    if payload.profile is not None:
        runtime.profile = payload.profile.strip() or None
    if payload.host is not None:
        runtime.host = payload.host.strip()
    if payload.port is not None:
        runtime.port = int(payload.port)
    if payload.username is not None:
        runtime.username = payload.username.strip()
    if payload.provider_config is not None:
        runtime.provider_config = payload.provider_config.model_dump(mode="json")
    if payload.auth_type is not None:
        runtime.auth_type = payload.auth_type
        runtime.encrypted_secret = _payload_secret(
            payload.auth_type, payload.private_key, payload.password
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise MachineConflict("A machine with that name already exists.") from exc
    await db.refresh(runtime)
    return runtime


async def delete_machine(db: AsyncSession, machine_id: UUID) -> None:
    runtime = await get_machine(db, machine_id)
    await require_machine_unused(db, machine_id)
    await db.delete(runtime)
    await db.commit()


def resolve_machine_secret(runtime: Machine) -> ResolvedMachine:
    if runtime.provider not in _KNOWN_PROVIDERS:
        raise MachineErrorBase(f"Unsupported machine provider: {runtime.provider}")
    # `local` runs on the host directly — no SSH host/credentials, only a workspace.
    if runtime.provider == "local":
        return ResolvedMachine(
            id=runtime.id,
            name=runtime.name,
            provider=runtime.provider,
            host="127.0.0.1",
            port=22,
            username=getpass.getuser(),
            auth_type="local",
            secret="",
            updated_at_marker=str(runtime.updated_at or ""),
        )
    if is_invalid_secret(runtime.encrypted_secret):
        raise MachineErrorBase("Machine credentials could not be decrypted.")
    missing = [
        name
        for name, value in {
            "host": runtime.host,
            "port": runtime.port,
            "username": runtime.username,
            "auth_type": runtime.auth_type,
            "encrypted_secret": runtime.encrypted_secret,
        }.items()
        if value in {None, ""}
    ]
    if missing:
        raise MachineErrorBase("Machine is not ready for SSH execution: " + ", ".join(missing))
    return ResolvedMachine(
        id=runtime.id,
        name=runtime.name,
        provider=runtime.provider,
        host=str(runtime.host),
        port=int(runtime.port or 22),
        username=str(runtime.username),
        auth_type=str(runtime.auth_type),
        secret=str(runtime.encrypted_secret),
        host_key=(runtime.provider_config or {}).get("host_key"),
        runtime_root=(runtime.provider_config or {}).get("runtime_root"),
        worker_id=(runtime.provider_config or {}).get("worker_id"),
        updated_at_marker=str(runtime.updated_at or ""),
    )


@dataclass
class MachineTestResult:
    resolved_home: str | None


async def test_machine(payload: MachineTestRequest) -> MachineTestResult:
    credentials = SSHCredentials(
        host=payload.host.strip(),
        port=int(payload.port),
        username=payload.username.strip(),
        private_key=(
            payload.private_key.strip()
            if payload.auth_type == "private_key" and payload.private_key
            else None
        ),
        password=payload.password if payload.auth_type == "password" else None,
    )
    client = SSHClient(credentials)
    try:
        await client.wait_ready(timeout=10)

        resolved_home: str | None = None
        try:
            home_result = await client.run('printf %s "$HOME"', timeout=5)
            if home_result.exit_status in {0, None}:
                resolved_home = (home_result.stdout or "").strip() or None
        except Exception:  # noqa: BLE001
            resolved_home = None

        return MachineTestResult(
            resolved_home=resolved_home,
        )
    finally:
        await client.close()


def _payload_secret(auth_type: str | None, private_key: str | None, password: str | None) -> str:
    if auth_type == "private_key":
        return (private_key or "").strip()
    return password or ""


async def runtime_workspace_labels(
    db: AsyncSession, machine_id: UUID, ids: list[str] | None
) -> list[dict]:
    """Resolve restart candidates across instances, retaining unregistered IDs."""

    labels = {}
    instances = list((await db.execute(select(SentinelInstance))).scalars().all())
    for instance in instances:
        async with instance_session_registry.session_factory(
            instance.database_name
        )() as instance_db:
            rows = (
                await instance_db.execute(
                    select(Workspace.id, Workspace.name).where(Workspace.machine_id == machine_id)
                )
            ).all()
            for id, name in rows:
                labels[str(id)] = {
                    "id": str(id),
                    "name": name,
                    "instance": instance.name,
                }
    return [
        labels.get(id, {"id": id, "name": "Unregistered workspace", "instance": None})
        for id in (ids if ids is not None else sorted(labels))
    ]


async def require_machine_unused(db: AsyncSession, machine_id: UUID) -> None:
    """Machines cannot disappear underneath workspace registrations."""

    instances = list((await db.execute(select(SentinelInstance))).scalars().all())
    for instance in instances:
        async with instance_session_registry.session_factory(
            instance.database_name
        )() as instance_db:
            result = await instance_db.execute(
                select(Workspace.id).where(Workspace.machine_id == machine_id).limit(1)
            )
            if result.scalar_one_or_none() is not None:
                raise MachineConflict(
                    f"Remove this machine's workspaces in instance '{instance.name}' first. Workspace files are kept."
                )
