"""Route lifecycle commands to their owner: local desktop or remote worker."""

from __future__ import annotations

import app.services.instance_runtime_context as instance_context
import app.services.runtime.remote_mac as remote_mac

import asyncio
import platform
from uuid import UUID

import httpx

import app.database as database_module
from app.config import settings
from app.models import Workspace
from app.models.manager import Machine
from app.services.runtime.compatibility import RuntimeCompatibilityError


class WorkspaceContainerError(RuntimeError):
    pass


def available() -> bool:
    return bool(settings.workspace_runtime_socket) and platform.system() == "Darwin"


async def local_request(action: str, **values) -> dict:
    if not available():
        raise WorkspaceContainerError(
            "Workspace services are unavailable. Check Sentinel's desktop services."
        )
    async with httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=settings.workspace_runtime_socket),
        base_url="http://workspace-runtime",
        timeout=1900,
        trust_env=False,
        headers={"x-sentinel-desktop-token": settings.sentinel_desktop_token},
    ) as client:
        try:
            response = await client.post("/v1/request", json={"action": action, **values})
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WorkspaceContainerError("Could not reach workspace services.") from exc
        RuntimeCompatibilityError.check(
            data, machine_id=_workspace_machines.get(str(values.get("workspace")))
        )
        if response.is_error or data.get("error"):
            raise WorkspaceContainerError(data.get("error") or "Workspace operation failed")
        return data


# Explicit workspace ownership; populated by workspace routes and session binding.
_workspace_machines: dict[str, str] = {}
_workspace_distributions: dict[str, str] = {}
_workspace_desktops: dict[str, str] = {}
_workspace_browsers: dict[str, str] = {}


def bind(workspace_id, machine_id, distribution=None, desktop=None, browser=None) -> None:
    _workspace_machines[str(workspace_id)] = str(machine_id)
    if distribution is not None:
        _workspace_distributions[str(workspace_id)] = distribution
    if desktop is not None:
        _workspace_desktops[str(workspace_id)] = desktop
    if browser is not None:
        _workspace_browsers[str(workspace_id)] = browser


async def remote_for(workspace_id):

    machine_id = _workspace_machines.get(str(workspace_id))
    if machine_id is None:
        for context in instance_context.instance_runtime_context_registry.all():
            async with context.session_factory() as db:
                workspace = await db.get(Workspace, UUID(str(workspace_id)))
                if workspace:
                    bind(
                        workspace.id,
                        workspace.machine_id,
                        workspace.distribution,
                        workspace.desktop,
                        workspace.browser,
                    )
                    machine_id = str(workspace.machine_id)
                    break
    if machine_id:
        async with database_module.ManagerSessionLocal() as db:
            machine = await db.get(Machine, UUID(machine_id))
        if machine and machine.provider == "ssh":
            return await remote_mac.get_runtime(machine.id)
    return None


async def request(action: str, **values) -> dict:
    try:
        if values.get("workspace"):
            remote = await remote_for(values["workspace"])
            if remote:
                return await remote.operation(action, **values)
        if action == "status":
            return await overview()
        return await local_request(action, **values)
    except (WorkspaceContainerError, RuntimeCompatibilityError):
        raise
    except Exception as exc:
        raise WorkspaceContainerError(str(exc)) from exc


async def overview() -> dict:

    try:
        result = await local_request("status")
    except WorkspaceContainerError as exc:
        result = {
            "states": {
                key: {"state": "unavailable", "error": str(exc)} for key in _workspace_machines
            }
        }
    for machine_id in set(_workspace_machines.values()):
        async with database_module.ManagerSessionLocal() as db:
            machine = await db.get(Machine, UUID(machine_id))
        if machine is None or machine.provider != "ssh":
            continue
        keys = [key for key, value in _workspace_machines.items() if value == machine_id]
        try:
            snapshot = await asyncio.wait_for(
                (await remote_mac.get_runtime(machine.id)).overview(), timeout=8
            )
            for key in keys:
                result.setdefault("states", {})[key] = snapshot.get("states", {}).get(
                    key, {"state": "stopped"}
                )
        except Exception as exc:
            message = "Connecting to remote runtime…" if isinstance(exc, TimeoutError) else str(exc)
            for key in keys:
                result.setdefault("states", {})[key] = {
                    "state": "unavailable",
                    "error": message,
                }
    return result


async def statuses(workspace_id=None) -> dict:
    if workspace_id is not None:
        try:
            remote = await remote_for(workspace_id)
            snapshot = await remote.overview() if remote else await local_request("status")
            return snapshot.get("states", {})
        except (WorkspaceContainerError, RuntimeCompatibilityError):
            raise
        except Exception as exc:
            raise WorkspaceContainerError(str(exc)) from exc
    return (await overview()).get("states", {})


async def start(
    workspace_id: UUID,
    directory: str,
    tools: list[str],
    *,
    resources: dict[str, int] | None = None,
    notification_context: dict[str, str] | None = None,
) -> None:
    values = {"resources": resources} if resources is not None else {}
    if str(workspace_id) in _workspace_browsers:
        values["browser"] = _workspace_browsers[str(workspace_id)]
    if notification_context:
        values["notificationContext"] = notification_context
    distribution = _workspace_distributions.get(str(workspace_id))
    if distribution and distribution != "alpine":
        values["distribution"] = distribution
    if str(workspace_id) in _workspace_desktops:
        values["desktop"] = _workspace_desktops[str(workspace_id)]
    await request("prepare", workspace=str(workspace_id), project=directory, tools=tools, **values)


async def reinstall(
    workspace_id: UUID,
    directory: str,
    tools: list[str],
    *,
    notification_context: dict[str, str] | None = None,
    distribution: str | None = None,
    desktop: str | None = None,
    browser: str | None = None,
    resources: dict[str, int] | None = None,
    spec: dict | None = None,
    revision: int | None = None,
) -> dict:
    result = await request(
        "reinstall",
        confirmed=True,
        **({"spec": spec} if spec else {}),
        **({"revision": revision} if revision is not None else {}),
        **({"resources": resources} if resources else {}),
        **(
            {"browser": browser or _workspace_browsers.get(str(workspace_id), "chromium")}
            if browser or str(workspace_id) in _workspace_browsers
            else {}
        ),
        **(
            {
                "distribution": distribution
                or _workspace_distributions.get(str(workspace_id), "alpine")
            }
            if distribution is not None or str(workspace_id) in _workspace_distributions
            else {}
        ),
        workspace=str(workspace_id),
        project=directory,
        tools=tools,
        **(
            {"desktop": desktop or _workspace_desktops.get(str(workspace_id), "none")}
            if desktop or str(workspace_id) in _workspace_desktops
            else {}
        ),
        **({"notificationContext": notification_context} if notification_context else {}),
    )
    return result


async def stop(workspace_id: UUID, *, delete: bool = False) -> None:
    await request("delete" if delete else "stop", workspace=str(workspace_id))


async def ensure_ready(
    workspace_id: UUID,
    directory: str,
    tools: list[str],
    *,
    timeout: int = 900,
    retry: bool = False,
) -> None:
    key = str(workspace_id)
    async with asyncio.timeout(timeout):
        state = (await statuses(workspace_id)).get(key, {})
        if state.get("state") in {None, "stopped"} or (retry and state.get("state") == "failed"):
            await start(workspace_id, directory, tools)
            state = (await statuses(workspace_id)).get(key, {})
        while True:
            if state.get("state") == "running":
                return
            if state.get("state") != "preparing":
                raise WorkspaceContainerError(
                    state.get("error") or "The workspace stopped. Start it again from Workspaces."
                )
            await asyncio.sleep(0.5)
            state = (await statuses(workspace_id)).get(key, {})
