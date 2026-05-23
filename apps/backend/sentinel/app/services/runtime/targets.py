from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.manager import RuntimeSSHTarget, SentinelInstance
from app.schemas.runtime_targets import (
    RuntimeSSHTargetCreateRequest,
    RuntimeSSHTargetResponse,
    RuntimeSSHTargetTestRequest,
    RuntimeSSHTargetUpdateRequest,
)
from app.services.instances import normalize_instance_name
from app.services.runtime.ssh_client import SSHClient, SSHCredentials
from app.services.runtime.target_secrets import decrypt_runtime_secret, encrypt_runtime_secret


class RuntimeTargetError(RuntimeError):
    pass


class RuntimeTargetNotFound(RuntimeTargetError):
    pass


class RuntimeTargetConflict(RuntimeTargetError):
    pass


class InstanceRuntimeTargetNotConfigured(RuntimeTargetError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeTarget:
    id: UUID
    name: str
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


def runtime_target_response(target: RuntimeSSHTarget) -> RuntimeSSHTargetResponse:
    return RuntimeSSHTargetResponse(
        id=target.id,
        name=target.name,
        host=target.host,
        port=target.port,
        username=target.username,
        workspaces_dir=target.workspaces_dir,
        auth_type=target.auth_type,  # type: ignore[arg-type]
        created_at=target.created_at,
        updated_at=target.updated_at,
    )


async def list_runtime_targets(db: AsyncSession) -> list[RuntimeSSHTarget]:
    result = await db.execute(select(RuntimeSSHTarget).order_by(RuntimeSSHTarget.name))
    return list(result.scalars().all())


async def get_runtime_target(db: AsyncSession, target_id: UUID) -> RuntimeSSHTarget:
    target = await db.get(RuntimeSSHTarget, target_id)
    if target is None:
        raise RuntimeTargetNotFound("Runtime target not found.")
    return target


async def create_runtime_target(
    db: AsyncSession,
    payload: RuntimeSSHTargetCreateRequest,
) -> RuntimeSSHTarget:
    target = RuntimeSSHTarget(
        name=payload.name.strip(),
        host=payload.host.strip(),
        port=int(payload.port),
        username=payload.username.strip(),
        workspaces_dir=payload.workspaces_dir.strip(),
        auth_type=payload.auth_type,
        encrypted_secret=encrypt_runtime_secret(_payload_secret(payload.auth_type, payload.private_key, payload.password)),
    )
    db.add(target)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise RuntimeTargetConflict("A runtime target with that name already exists.") from exc
    await db.refresh(target)
    return target


async def update_runtime_target(
    db: AsyncSession,
    target_id: UUID,
    payload: RuntimeSSHTargetUpdateRequest,
) -> RuntimeSSHTarget:
    target = await get_runtime_target(db, target_id)
    if payload.name is not None:
        target.name = payload.name.strip()
    if payload.host is not None:
        target.host = payload.host.strip()
    if payload.port is not None:
        target.port = int(payload.port)
    if payload.username is not None:
        target.username = payload.username.strip()
    if payload.workspaces_dir is not None:
        target.workspaces_dir = payload.workspaces_dir.strip()
    if payload.auth_type is not None:
        target.auth_type = payload.auth_type
        target.encrypted_secret = encrypt_runtime_secret(
            _payload_secret(payload.auth_type, payload.private_key, payload.password)
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise RuntimeTargetConflict("A runtime target with that name already exists.") from exc
    await db.refresh(target)
    return target


async def delete_runtime_target(db: AsyncSession, target_id: UUID) -> None:
    target = await get_runtime_target(db, target_id)
    await db.delete(target)
    await db.commit()


async def assign_instance_runtime_target(
    db: AsyncSession,
    *,
    instance_name: str,
    target_id: UUID | None,
) -> SentinelInstance:
    normalized = normalize_instance_name(instance_name)
    result = await db.execute(select(SentinelInstance).where(SentinelInstance.name == normalized))
    instance = result.scalar_one_or_none()
    if instance is None:
        raise RuntimeTargetNotFound("Instance not found.")
    if target_id is not None:
        await get_runtime_target(db, target_id)
    instance.runtime_target_id = target_id
    await db.commit()
    await db.refresh(instance)
    return instance


async def resolve_instance_runtime_target(
    db: AsyncSession,
    *,
    instance_name: str,
) -> ResolvedRuntimeTarget:
    normalized = normalize_instance_name(instance_name)
    result = await db.execute(select(SentinelInstance).where(SentinelInstance.name == normalized))
    instance = result.scalar_one_or_none()
    if instance is None:
        raise RuntimeTargetNotFound("Instance not found.")
    if instance.runtime_target_id is None:
        raise InstanceRuntimeTargetNotConfigured("No runtime target selected for this instance.")
    target = await get_runtime_target(db, instance.runtime_target_id)
    return resolve_runtime_target_secret(target)


def resolve_runtime_target_secret(target: RuntimeSSHTarget) -> ResolvedRuntimeTarget:
    return ResolvedRuntimeTarget(
        id=target.id,
        name=target.name,
        host=target.host,
        port=int(target.port),
        username=target.username,
        workspaces_dir=target.workspaces_dir,
        auth_type=target.auth_type,
        secret=decrypt_runtime_secret(target.encrypted_secret),
        updated_at_marker=str(target.updated_at or ""),
    )


@dataclass
class RuntimeTargetTestResult:
    resolved_home: str | None
    resolved_workspaces_dir: str


async def test_runtime_target(payload: RuntimeSSHTargetTestRequest) -> RuntimeTargetTestResult:
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
                raise RuntimeTargetError(
                    "Could not resolve $HOME on remote — please enter a workspace root manually."
                )
            target_path = f"{resolved_home.rstrip('/')}/sentinel/workspaces"

        result = await client.run(
            "test -d \"$SENTINEL_WORKSPACES_DIR\" || mkdir -p \"$SENTINEL_WORKSPACES_DIR\"; "
            "test -w \"$SENTINEL_WORKSPACES_DIR\"",
            timeout=15,
            env={"SENTINEL_WORKSPACES_DIR": target_path},
        )
        if result.exit_status not in {0, None}:
            detail = (result.stderr or result.stdout or "Workspace directory is not writable.").strip()
            raise RuntimeTargetError(detail)

        return RuntimeTargetTestResult(
            resolved_home=resolved_home,
            resolved_workspaces_dir=target_path,
        )
    finally:
        await client.close()


def _payload_secret(auth_type: str, private_key: str | None, password: str | None) -> str:
    if auth_type == "private_key":
        return (private_key or "").strip()
    return password or ""
