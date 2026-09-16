from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import PurePosixPath
from shlex import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.runtime.development_tools as development_tools_module
import app.services.runtime.directories as directories_module
import app.services.runtime.machines as machines_module
import app.services.runtime.guest_commands as guest_commands_module
import app.services.runtime.remote_mac as remote_mac_module
import app.services.runtime.remote_verify as remote_verify_module
import app.services.runtime.storage as storage_module
import app.services.runtime.workspace_containers as workspace_containers_module
from app.dependencies import get_manager_db
from app.schemas.machines import (
    MachineCapabilitiesResponse,
    MachineCreateRequest,
    MachineJobResponse,
    MachineLifecycleResponse,
    MachineResponse,
    MachineTestRequest,
    MachineTestResponse,
    MachineUpdateRequest,
)
from app.services.runtime.directories import list_local_directories
from app.services.runtime.distributions import DISTRIBUTIONS
from app.services.runtime.providers import (
    MachineJobNotFound,
    MachineProviderError,
    machine_provider_service,
)
from app.services.runtime.ssh_runtime import (
    close_runtime_terminal_manager,
)

router = APIRouter()


class RuntimeInstallRequest(BaseModel):
    approved: bool = False
    reinstall: bool = False
    approved_workspaces: list[UUID] = Field(default_factory=list, max_length=1000)
    host_key: str = Field(min_length=1, max_length=16384)


@router.get("/machines/{machine_id}/runtime")
async def inspect_remote_runtime(machine_id: UUID, db: AsyncSession = Depends(get_manager_db)):

    try:
        machine = machines_module.resolve_machine_secret(
            await machines_module.get_machine(db, machine_id)
        )
        if machine.provider != "ssh":
            raise HTTPException(422, "This machine uses the local runtime")
        identity = await asyncio.wait_for(remote_mac_module.fingerprint(machine), 15)
        installation = {}
        if not machine.host_key or machine.host_key == identity["host_key"]:
            installation = await asyncio.wait_for(
                remote_mac_module.inspect_installation(
                    replace(machine, host_key=identity["host_key"])
                ),
                15,
            )
        return {
            **identity,
            **installation,
            "progress": remote_mac_module.install_progress.get(str(machine.id)),
            "available_version": await remote_mac_module.available_version(),
            "installed": installation.get("installed", bool(machine.runtime_root)),
            "identity_verified": bool(
                machine.host_key and machine.host_key == identity["host_key"]
            ),
            "path": installation.get("path") or machine.runtime_root or "~/.sentinel/runtime",
            "host_key_changed": bool(machine.host_key and machine.host_key != identity["host_key"]),
        }
    except machines_module.MachineNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/machines/{machine_id}/runtime/verify")
async def verify_remote_runtime(machine_id: UUID, db: AsyncSession = Depends(get_manager_db)):

    try:
        machine = machines_module.resolve_machine_secret(
            await machines_module.get_machine(db, machine_id)
        )
        if machine.provider != "ssh":
            raise HTTPException(422, "Choose an SSH machine")
        if not machine.host_key or not machine.runtime_root:
            raise HTTPException(409, "Install and verify the machine’s SSH identity first")
        await db.close()
        return await asyncio.wait_for(remote_verify_module.verify_installation(machine), 75)
    except machines_module.MachineNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except HTTPException:
        raise
    except TimeoutError as exc:
        raise HTTPException(504, "Runtime verification timed out; nothing was changed") from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/machines/{machine_id}/runtime/plan")
async def plan_remote_runtime(machine_id: UUID, db: AsyncSession = Depends(get_manager_db)):

    inspection = await inspect_remote_runtime(machine_id, db)
    if inspection["host_key_changed"] or inspection.get("progress"):
        return inspection
    try:
        machine = machines_module.resolve_machine_secret(
            await machines_module.get_machine(db, machine_id)
        )
        await db.close()
        running = await remote_mac_module.plan_installation(machine)
        return {
            **inspection,
            "workspaces": await machines_module.runtime_workspace_labels(db, machine_id, running),
            "restart_check_failed": running is None,
        }
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/machines/{machine_id}/runtime")
async def install_remote_runtime(
    machine_id: UUID,
    payload: RuntimeInstallRequest,
    db: AsyncSession = Depends(get_manager_db),
):

    if not payload.approved:
        raise HTTPException(422, "Runtime installation requires explicit approval")
    try:
        row = await machines_module.get_machine(db, machine_id)
        machine = machines_module.resolve_machine_secret(row)
        if machine.provider != "ssh":
            raise HTTPException(422, "Choose an SSH machine")
        if machine.host_key and machine.host_key != payload.host_key:
            raise HTTPException(
                409,
                "SSH host key changed. Verify the machine before enrolling it again.",
            )
        await db.close()
        # Reject stale/missing approval before even preparing local deployment assets.
        running = await remote_mac_module.plan_installation(machine)
        approved = {str(id) for id in payload.approved_workspaces}
        if running is not None and not set(running).issubset(approved):
            return {
                "approval_required": True,
                "workspaces": running,
                "workspace_details": await machines_module.runtime_workspace_labels(
                    db, machine_id, running
                ),
            }
        assets = await workspace_containers_module.local_request("deployment")
        try:
            config = await remote_mac_module.install(
                machine,
                payload.host_key,
                assets,
                [str(id) for id in payload.approved_workspaces],
                reinstall=payload.reinstall,
            )
        except remote_mac_module.UpdateApprovalRequired as exc:
            return {
                "approval_required": True,
                "workspaces": exc.workspaces,
                "workspace_details": await machines_module.runtime_workspace_labels(
                    db, machine_id, exc.workspaces
                ),
            }
        row = await machines_module.get_machine(db, machine_id)
        if str(row.updated_at or "") != machine.updated_at_marker:
            raise HTTPException(
                409,
                "Machine settings changed during installation. Reopen Runtime to check the enrolled host.",
            )
        row.provider_config = {**(row.provider_config or {}), **config}
        await db.commit()
        await db.close()
        await (await remote_mac_module.get_runtime(machine_id)).connect()
        row = await machines_module.get_machine(db, machine_id)
        row.status = "ready"
        await db.commit()
        details = await remote_mac_module.inspect_installation(
            machines_module.resolve_machine_secret(row)
        )
        return {
            "installed": True,
            "path": config["runtime_root"],
            "warning": details.get("update_warning"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/machines/{machine_id}/development-tools")
async def development_tool_options(machine_id: UUID, db: AsyncSession = Depends(get_manager_db)):

    try:
        machine = machines_module.resolve_machine_secret(
            await machines_module.get_machine(db, machine_id)
        )
        os_name = await development_tools_module.machine_os(machine)
    except machines_module.MachineNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except (machines_module.MachineErrorBase, RuntimeError, OSError, TimeoutError) as exc:
        raise HTTPException(
            502,
            "Could not detect this machine's operating system. Check its connection.",
        ) from exc

    return {
        "os": os_name,
        "container_available": os_name == "darwin"
        and (
            (machine.provider == "local" and workspace_containers_module.available())
            or (machine.provider == "ssh" and bool(machine.runtime_root))
        ),
        "distributions": DISTRIBUTIONS if os_name == "darwin" else [],
        "stacks": development_tools_module.STACKS if os_name == "darwin" else [],
        "tools": development_tools_module.TOOLS if os_name == "darwin" else [],
    }


@router.get("/machines/{machine_id}/directories")
async def browse_machine_directories(
    machine_id: UUID,
    path: str = "",
    db: AsyncSession = Depends(get_manager_db),
) -> dict:

    if path and (not path.startswith("/") or "\0" in path):
        raise HTTPException(status_code=422, detail="Choose an absolute directory path")
    try:
        machine = machines_module.resolve_machine_secret(
            await machines_module.get_machine(db, machine_id)
        )
    except machines_module.MachineNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except machines_module.MachineErrorBase as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if machine.provider == "local":

        try:
            return await asyncio.to_thread(list_local_directories, path)
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail="Cannot open this directory. Check its path and permissions.",
            ) from exc
    transport = storage_module.machine_transport(machine)
    try:
        result = await transport.run_script(
            f"set -- {quote(path)}\n"
            + guest_commands_module.load_guest_command("common/directories.sh"),
            timeout=15,
        )
        if result.exit_status != 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot open this directory. Check its path and permissions.",
            )
        fields = result.stdout.split("\0")
        current = fields[0]
        if not current.startswith("/"):
            raise HTTPException(status_code=502, detail="Machine returned an invalid directory")
        return {
            "path": current,
            "parent": str(PurePosixPath(current).parent),
            "directories": sorted((name for name in fields[1:] if name), key=str.casefold),
        }
    except (OSError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not browse folders on this machine. Check its connection.",
        ) from exc
    finally:
        await transport.close()


class DirectoryCreateRequest(BaseModel):
    path: str = Field(max_length=4096)
    name: str = Field(min_length=1, max_length=255)


@router.post("/machines/{machine_id}/directories", status_code=201)
async def create_machine_directory(
    machine_id: UUID,
    payload: DirectoryCreateRequest,
    db: AsyncSession = Depends(get_manager_db),
) -> dict:

    try:
        directories_module.validate_new_directory(payload.path, payload.name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        machine = machines_module.resolve_machine_secret(
            await machines_module.get_machine(db, machine_id)
        )
    except machines_module.MachineNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except machines_module.MachineErrorBase as exc:
        raise HTTPException(400, str(exc)) from exc
    if machine.provider == "local":
        try:
            return await asyncio.to_thread(
                directories_module.create_local_directory, payload.path, payload.name
            )
        except FileExistsError as exc:
            raise HTTPException(409, "A file or folder with that name already exists.") from exc
        except OSError as exc:
            raise HTTPException(
                400,
                "Cannot create this folder. Check the name and parent folder permissions.",
            ) from exc
    transport = storage_module.machine_transport(machine)
    try:
        result = await transport.run_script(
            f"set -- {quote(payload.path)} {quote(payload.name)}\n"
            + guest_commands_module.load_guest_command("common/create_directory.sh"),
            timeout=15,
        )
        if result.exit_status == 17:
            raise HTTPException(409, "A file or folder with that name already exists.")
        if result.exit_status != 0:
            raise HTTPException(
                400,
                "Cannot create this folder. Check the name and parent folder permissions.",
            )
        created = result.stdout.rstrip("\n")
        if not created.startswith("/"):
            raise HTTPException(502, "Machine returned an invalid directory")
        return {
            "path": created,
            "parent": str(PurePosixPath(created).parent),
            "directories": [],
        }
    except (OSError, TimeoutError) as exc:
        raise HTTPException(
            502, "Could not create folder on this machine. Check its connection."
        ) from exc
    finally:
        await transport.close()


@router.get("/machines", response_model=list[MachineResponse])
async def list_machine_rows(
    db: AsyncSession = Depends(get_manager_db),
) -> list[MachineResponse]:
    return [
        await machine_provider_service.machine_response(runtime)
        for runtime in await machines_module.list_machines(db)
    ]


@router.post(
    "/machines",
    response_model=MachineResponse | MachineLifecycleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_machine_row(
    payload: MachineCreateRequest,
    db: AsyncSession = Depends(get_manager_db),
) -> MachineResponse | MachineLifecycleResponse:
    try:
        if machine_provider_service.is_managed(payload.provider):
            return await machine_provider_service.create_managed(db, payload)
        runtime = await machines_module.create_machine(db, payload)
    except machines_module.MachineConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except MachineProviderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await machine_provider_service.machine_response(runtime)


@router.get("/machines/capabilities", response_model=MachineCapabilitiesResponse)
async def machine_capabilities() -> MachineCapabilitiesResponse:
    return machine_provider_service.capabilities()


@router.get("/machines/jobs/{job_id}", response_model=MachineJobResponse)
async def machine_job(
    job_id: UUID,
) -> MachineJobResponse:
    try:
        return machine_provider_service.get_job(job_id)
    except MachineJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/machines/{machine_id}", response_model=MachineResponse)
async def get_machine_row(
    machine_id: UUID,
    db: AsyncSession = Depends(get_manager_db),
) -> MachineResponse:
    try:
        return await machine_provider_service.machine_response(
            await machines_module.get_machine(db, machine_id)
        )
    except machines_module.MachineConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except machines_module.MachineNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/machines/{machine_id}", response_model=MachineResponse)
async def update_machine_row(
    machine_id: UUID,
    payload: MachineUpdateRequest,
    db: AsyncSession = Depends(get_manager_db),
) -> MachineResponse:
    try:
        runtime = await machines_module.update_machine(db, machine_id, payload)
    except machines_module.MachineConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except machines_module.MachineNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await close_runtime_terminal_manager()
    return await machine_provider_service.machine_response(runtime)


@router.delete("/machines/{machine_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_machine_row(
    machine_id: UUID,
    db: AsyncSession = Depends(get_manager_db),
) -> None:
    try:
        runtime = await machines_module.get_machine(db, machine_id)
        await machines_module.require_machine_unused(db, machine_id)
        if machine_provider_service.is_managed(runtime.provider):
            await machine_provider_service.delete_managed_resources(runtime)
        await machines_module.delete_machine(db, machine_id)
    except machines_module.MachineConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except machines_module.MachineNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MachineProviderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await close_runtime_terminal_manager()


@router.post("/machines/{machine_id}/{action}", response_model=MachineLifecycleResponse)
async def machine_action(
    machine_id: UUID,
    action: str,
    db: AsyncSession = Depends(get_manager_db),
) -> MachineLifecycleResponse:
    if action not in {"start", "stop", "rebuild", "delete"}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unsupported machine action."
        )
    try:
        runtime = await machines_module.get_machine(db, machine_id)
        if not machine_provider_service.is_managed(runtime.provider):
            raise MachineProviderError("This machine type has no managed lifecycle actions.")
        if action in {"delete", "rebuild"}:
            await machines_module.require_machine_unused(db, machine_id)
        return await machine_provider_service.action(db, runtime, action)
    except machines_module.MachineConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except machines_module.MachineNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MachineProviderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/machines/test", response_model=MachineTestResponse)
async def test_machine_row(
    payload: MachineTestRequest,
) -> MachineTestResponse:
    try:
        result = await machines_module.test_machine(payload)
    except machines_module.MachineErrorBase as exc:
        return MachineTestResponse(ok=False, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        return MachineTestResponse(ok=False, detail=str(exc))
    return MachineTestResponse(
        ok=True,
        detail="Machine is reachable.",
        resolved_home=result.resolved_home,
    )
