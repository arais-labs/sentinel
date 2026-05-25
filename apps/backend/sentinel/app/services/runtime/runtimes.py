from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.manager import Runtime, SentinelInstance
from app.schemas.runtimes import (
    RuntimeCreateRequest,
    RuntimeProviderConfig,
    RuntimeProviderState,
    RuntimeResponse,
    RuntimeTestRequest,
    RuntimeUpdateRequest,
)
from app.services.instances import normalize_instance_name
from app.services.runtime.ssh_client import SSHClient, SSHCredentials
from app.services.runtime.target_secrets import decrypt_runtime_secret, encrypt_runtime_secret


class RuntimeErrorBase(RuntimeError):
    pass


class RuntimeNotFound(RuntimeErrorBase):
    pass


class RuntimeConflict(RuntimeErrorBase):
    pass


class InstanceRuntimeNotConfigured(RuntimeErrorBase):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedRuntime:
    id: UUID
    name: str
    provider: str
    host: str
    port: int
    username: str
    workspaces_dir: str
    auth_type: str
    secret: str
    updated_at_marker: str

    def credentials(self) -> SSHCredentials:
        return SSHCredentials(
            host=self.host,
            port=self.port,
            username=self.username,
            private_key=self.secret if self.auth_type == "private_key" else None,
            password=self.secret if self.auth_type == "password" else None,
        )


def runtime_response(runtime: Runtime) -> RuntimeResponse:
    return RuntimeResponse(
        id=runtime.id,
        name=runtime.name,
        provider=runtime.provider,  # type: ignore[arg-type]
        status=runtime.status,  # type: ignore[arg-type]
        profile=runtime.profile,
        host=runtime.host,
        port=runtime.port,
        username=runtime.username,
        workspaces_dir=runtime.workspaces_dir,
        auth_type=runtime.auth_type,  # type: ignore[arg-type]
        provider_config=RuntimeProviderConfig.model_validate(runtime.provider_config or {}),
        provider_state=RuntimeProviderState.model_validate(runtime.provider_state or {}),
        last_job_id=runtime.last_job_id,
        last_job_status=runtime.last_job_status,  # type: ignore[arg-type]
        created_at=runtime.created_at,
        updated_at=runtime.updated_at,
    )


async def list_runtimes(db: AsyncSession) -> list[Runtime]:
    result = await db.execute(select(Runtime).order_by(Runtime.name))
    return list(result.scalars().all())


async def get_runtime(db: AsyncSession, runtime_id: UUID) -> Runtime:
    runtime = await db.get(Runtime, runtime_id)
    if runtime is None:
        raise RuntimeNotFound("Runtime not found.")
    return runtime


async def create_runtime(
    db: AsyncSession,
    payload: RuntimeCreateRequest,
) -> Runtime:
    runtime = Runtime(
        name=payload.name.strip(),
        provider=payload.provider,
        status="ready" if payload.provider == "ssh" else "creating",
        profile=(payload.profile or payload.provider).strip() or payload.provider,
        host=payload.host.strip() if payload.host else None,
        port=int(payload.port or 22) if payload.host else None,
        username=payload.username.strip() if payload.username else None,
        workspaces_dir=payload.workspaces_dir.strip() if payload.workspaces_dir else None,
        auth_type=payload.auth_type,
        encrypted_secret=(
            encrypt_runtime_secret(_payload_secret(payload.auth_type, payload.private_key, payload.password))
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
        raise RuntimeConflict("A runtime with that name already exists.") from exc
    await db.refresh(runtime)
    return runtime


async def update_runtime(
    db: AsyncSession,
    runtime_id: UUID,
    payload: RuntimeUpdateRequest,
) -> Runtime:
    runtime = await get_runtime(db, runtime_id)
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
    if payload.workspaces_dir is not None:
        runtime.workspaces_dir = payload.workspaces_dir.strip()
    if payload.provider_config is not None:
        runtime.provider_config = payload.provider_config.model_dump(mode="json")
    if payload.auth_type is not None:
        runtime.auth_type = payload.auth_type
        runtime.encrypted_secret = encrypt_runtime_secret(
            _payload_secret(payload.auth_type, payload.private_key, payload.password)
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise RuntimeConflict("A runtime with that name already exists.") from exc
    await db.refresh(runtime)
    return runtime


async def delete_runtime(db: AsyncSession, runtime_id: UUID) -> None:
    runtime = await get_runtime(db, runtime_id)
    await db.delete(runtime)
    await db.commit()


async def assign_instance_runtime(
    db: AsyncSession,
    *,
    instance_name: str,
    runtime_id: UUID | None,
) -> SentinelInstance:
    normalized = normalize_instance_name(instance_name)
    result = await db.execute(select(SentinelInstance).where(SentinelInstance.name == normalized))
    instance = result.scalar_one_or_none()
    if instance is None:
        raise RuntimeNotFound("Instance not found.")
    if runtime_id is not None:
        await get_runtime(db, runtime_id)
    instance.runtime_id = runtime_id
    await db.commit()
    await db.refresh(instance)
    return instance


async def resolve_instance_runtime(
    db: AsyncSession,
    *,
    instance_name: str,
) -> ResolvedRuntime:
    normalized = normalize_instance_name(instance_name)
    result = await db.execute(select(SentinelInstance).where(SentinelInstance.name == normalized))
    instance = result.scalar_one_or_none()
    if instance is None:
        raise RuntimeNotFound("Instance not found.")
    if instance.runtime_id is None:
        raise InstanceRuntimeNotConfigured("No runtime selected for this instance.")
    runtime = await get_runtime(db, instance.runtime_id)
    return resolve_runtime_secret(runtime)


def resolve_runtime_secret(runtime: Runtime) -> ResolvedRuntime:
    if runtime.provider not in {"ssh", "lima", "docker"}:
        raise RuntimeErrorBase(f"Unsupported runtime provider: {runtime.provider}")
    missing = [
        name
        for name, value in {
            "host": runtime.host,
            "port": runtime.port,
            "username": runtime.username,
            "workspaces_dir": runtime.workspaces_dir,
            "auth_type": runtime.auth_type,
            "encrypted_secret": runtime.encrypted_secret,
        }.items()
        if value in {None, ""}
    ]
    if missing:
        raise RuntimeErrorBase("Runtime is not ready for SSH execution: " + ", ".join(missing))
    return ResolvedRuntime(
        id=runtime.id,
        name=runtime.name,
        provider=runtime.provider,
        host=str(runtime.host),
        port=int(runtime.port or 22),
        username=str(runtime.username),
        workspaces_dir=str(runtime.workspaces_dir),
        auth_type=str(runtime.auth_type),
        secret=decrypt_runtime_secret(str(runtime.encrypted_secret)),
        updated_at_marker=str(runtime.updated_at or ""),
    )


@dataclass
class RuntimeTestResult:
    resolved_home: str | None
    resolved_workspaces_dir: str


async def test_runtime(payload: RuntimeTestRequest) -> RuntimeTestResult:
    credentials = SSHCredentials(
        host=payload.host.strip(),
        port=int(payload.port),
        username=payload.username.strip(),
        private_key=payload.private_key.strip() if payload.auth_type == "private_key" and payload.private_key else None,
        password=payload.password if payload.auth_type == "password" else None,
    )
    client = SSHClient(credentials)
    try:
        await client.wait_ready(timeout=10)

        resolved_home: str | None = None
        try:
            home_result = await client.run("printf %s \"$HOME\"", timeout=5)
            if home_result.exit_status in {0, None}:
                resolved_home = (home_result.stdout or "").strip() or None
        except Exception:  # noqa: BLE001
            resolved_home = None

        target_path = payload.workspaces_dir.strip()
        if not target_path:
            if not resolved_home:
                raise RuntimeErrorBase("Could not resolve $HOME on remote. Enter a workspace root manually.")
            target_path = f"{resolved_home.rstrip('/')}/sentinel/workspaces"

        result = await client.run(
            "test -d \"$SENTINEL_WORKSPACES_DIR\" || mkdir -p \"$SENTINEL_WORKSPACES_DIR\"; "
            "test -w \"$SENTINEL_WORKSPACES_DIR\"",
            timeout=15,
            env={"SENTINEL_WORKSPACES_DIR": target_path},
        )
        if result.exit_status not in {0, None}:
            detail = (result.stderr or result.stdout or "Workspace directory is not writable.").strip()
            raise RuntimeErrorBase(detail)

        return RuntimeTestResult(
            resolved_home=resolved_home,
            resolved_workspaces_dir=target_path,
        )
    finally:
        await client.close()


def _payload_secret(auth_type: str | None, private_key: str | None, password: str | None) -> str:
    if auth_type == "private_key":
        return (private_key or "").strip()
    return password or ""
