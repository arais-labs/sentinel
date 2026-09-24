"""Client data requested by pending runtime upgrade scripts; no startup hooks."""

from sqlalchemy import select

from app.models import Workspace
from app.services.instance_runtime_context import instance_runtime_context_registry
from app.services.runtime import workspace_containers


async def prepare(session, machine_id):
    status = await session.request("migration_status")
    required = status["inputs"]
    if not required:
        return status["target"], {}
    if required != ["001_worker_ownership"]:
        raise ValueError("Update Sentinel before migrating this runtime")
    references = []
    for context in instance_runtime_context_registry.all():
        async with context.session_factory() as db:
            rows = await db.scalars(select(Workspace).where(Workspace.machine_id == machine_id))
            references.extend(
                {
                    "id": str(row.id),
                    "name": row.name,
                    "project": row.directory,
                    "distribution": row.distribution,
                    "desktop": row.desktop,
                    "tools": sorted(row.development_tools),
                }
                for row in rows
            )
    inputs = await workspace_containers.local_request(
        "runtime_migration_inputs", machine=str(machine_id), references=references
    )
    return status["target"], inputs
