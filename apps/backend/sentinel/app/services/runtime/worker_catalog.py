"""Discover worker-owned workspaces; instance rows are references and a UI cache."""

from uuid import UUID
from app.models import Workspace
from app.services.runtime import remote_mac, workspace_containers


async def snapshot(machine_id):
    return await (await remote_mac.get_runtime(machine_id)).overview()


async def cache_record(db, machine_id, workspace_id, record):
    spec = record["spec"]
    row = await db.get(Workspace, UUID(str(workspace_id)))
    if row is not None and row.machine_id != machine_id:
        raise remote_mac.RemoteMacError(
            "Workspace identity is already associated with another machine"
        )
    if row is None:
        row = Workspace(id=UUID(str(workspace_id)), machine_id=machine_id)
        db.add(row)
    row.name = spec["name"]
    row.directory = spec["project"]
    row.distribution = spec["distribution"]
    row.desktop = spec["desktop"]
    row.browser = spec.get("browser", "chromium")
    row.development_tools = spec["tools"]
    workspace_containers.bind(row.id, machine_id, row.distribution, row.desktop, row.browser)
    return row


async def configure(
    db, row, *, name, project, tools, resources, distribution, desktop, revision, browser="chromium"
):
    runtime = await remote_mac.get_runtime(row.machine_id)
    result = await runtime.operation(
        "configure",
        workspace=str(row.id),
        name=name,
        project=project,
        tools=tools,
        resources=resources,
        distribution=distribution,
        desktop=desktop,
        browser=browser,
        revision=revision,
    )
    await cache_record(db, row.machine_id, row.id, result["workspaces"][str(row.id)])
    await db.commit()
    return result["workspaces"][str(row.id)]
