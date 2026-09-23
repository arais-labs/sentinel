# Remote workers

The runtime under the SSH account owns the workspace catalog, setup operations,
containers and disks. A desktop holds connection credentials, a pinned SSH host
identity, UI caches and conversation references. Disconnecting a desktop does
not stop worker-owned operations.

`workspaces.json` is protected by the existing runtime owner lock and written
atomically. It contains a durable worker UUID and workspace records keyed by
their original UUIDs. Configuration includes the project path, distribution,
resources, selected tools and a self-contained setup plan. Plans use the same
tool definitions as local workspaces; reconnect/start never regenerates them
from a client's cached settings. Graphics archives are installed from the worker
release, not uploaded from the viewer during setup.

The worker protocol provides catalog discovery, revision-checked configuration,
start, stop, delete and recovery. Configuration changes require the revision the
client read. Lifecycle mutations are serialized on the worker; guest commands
and desktop streams remain independent. A worker restart reports interrupted
operations rather than replaying uncertain work.

Runtime updates retain the catalog and private disks. After activation, the
worker starts approved workspaces from its own saved configuration. Clients do
not need a private copy of the old lifecycle registry to resume them.

Remote discovery refreshes each worker independently. Unreachable workers retain
their cached references and report a connection error. They are not interpreted
as uninstalled. An address change retains the pinned SSH identity; an account
change requires a new enrollment. Connecting to an existing verified worker
does not install or update it.

Runtime upgrade scripts live in `Sources/WorkspaceRuntime/Migrations`. They run
only through **Update runtime**, under the existing update/service ownership
locks. The updater gathers requested client inputs, validates before stopping
workspaces, then backs up affected metadata, applies and verifies each pending
script before activation. `migrations.json` records completed script IDs; this is
an execution ledger, not a separate runtime database schema.

Each script declares its ID, affected metadata files, required inputs and
validate/apply/verify operations. Scripts must be safe to retry after any write.
Backups are retained in `migration-backups/<id>.json`; completed scripts are
never replayed. Add new numbered files and append them to the ordered registry;
never change the meaning of a completed script. Unknown migration history or an
incompatible rollback target is rejected. Failed/interrupted updates use the
existing update journal and recovery flow, without replaying guest commands.

`001_worker_ownership` reconciles the old desktop registry and instance references
with the worker's disks. It preserves UUIDs, resources, project paths and disks;
missing/conflicting inputs block the update before disruption. Its client input
adapter is isolated in `workspace/migrations`, not normal workspace operations.

Database changes remain ordinary Alembic migrations (`0007_workspace_name_cache`).
Workers enforce unique names; client caches use UUIDs, allowing historical
references to retain a reused name. Conversations keep their original UUIDs.
