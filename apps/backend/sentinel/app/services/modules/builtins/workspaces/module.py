from __future__ import annotations

from app.services.modules.definitions import ActionDefinition, ModuleDefinition

from . import handlers

# One schema per field: a grouped tool merges the actions' fields into one parameter list.
FIELDS = {
    "workspace_id": {"type": "string", "description": "Workspace id from list."},
    "machine_id": {
        "type": "string",
        "description": "Machine id from list (a machine with workspaces_available).",
    },
    "name": {"type": "string", "minLength": 1, "maxLength": 120, "description": "Workspace name."},
    "directory": {
        "type": "string",
        "description": (
            "Absolute path of an existing project folder on that machine, shared into the "
            "workspace (not /)."
        ),
    },
    "distribution": {
        "type": "string",
        "pattern": "^[a-z0-9][a-z0-9-]{0,63}$",
        "description": (
            "Distribution id. To create or update a workspace: an id the machine's catalog lists. "
            "For the distribution actions: the folder's id."
        ),
    },
    "desktop": {
        "type": "string",
        "pattern": "^[a-z0-9][a-z0-9-]{0,63}$",
        "description": "'none', or a desktop the catalog offers for the distribution.",
    },
    "browser": {
        "type": "string",
        "pattern": "^[a-z0-9][a-z0-9-]{0,63}$",
        "description": "'chromium' (default), or a Browsers tool the catalog offers for it.",
    },
    "development_tools": {
        "type": "array",
        "items": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,63}$"},
        "maxItems": 64,
        "description": "Tool ids the catalog offers for the distribution.",
    },
    "resources": {
        "type": "object",
        "additionalProperties": False,
        "required": ["cpus", "memory_gib", "disk_gib"],
        "properties": {
            "cpus": {"type": "integer", "minimum": 1, "maximum": 32},
            "memory_gib": {"type": "integer", "minimum": 1, "maximum": 64},
            "disk_gib": {"type": "integer", "minimum": 8, "maximum": 1024},
        },
        "description": (
            "All three values. Omit on create for the defaults (2 CPUs, 2 GiB, 32 GiB disk) raised "
            "to the distribution's minimum. Disks only grow."
        ),
    },
    "revision": {
        "type": "integer",
        "minimum": 1,
        "description": "Remote Macs only: the workspace's revision from list.",
    },
    "files": {
        "type": "object",
        "additionalProperties": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["text"],
            "properties": {"text": {"type": "string"}, "executable": {"type": "boolean"}},
        },
        "description": (
            'Files to write, by path in the folder: {"distribution.json": {"text": "..."}, '
            '"install/setup.sh": {"text": "...", "executable": true}}; null removes a file. '
            "Files not named stay as they are."
        ),
    },
    "match": {
        "type": "string",
        "description": "Glob over an archive's member paths, * for all: boot/*, *.ko.",
    },
    "offset": {"type": "integer", "minimum": 0, "description": "First byte of a range."},
    "length": {"type": "integer", "minimum": 1, "description": "Bytes in a range."},
    "paths": {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
        "description": "Files to read by path: a distribution's files, or an archive's members.",
    },
    "topic": {
        "type": "string",
        "enum": ["distributions", "overview"],
        "description": "distributions (default): how to write a distribution. overview: sentineld.",
    },
    "url": {
        "type": "string",
        "pattern": "^https?://",
        "description": "http(s) URL of a file a distribution's install.sources pins.",
    },
    "lines": {
        "type": "integer",
        "minimum": 1,
        "maximum": 5000,
        "description": "Last lines of each console (default 200).",
    },
    "detach": {"type": "boolean", "description": "true: detach this chat from its workspace."},
    "detach_sessions": {
        "type": "boolean",
        "description": "true once the user agreed to detach the chats that use the workspace.",
    },
}


def action(name, label, description, required=(), optional=(), permission="allow"):
    return ActionDefinition(
        id=name,
        label=label,
        description=description,
        handler=getattr(handlers, f"handle_{name}"),
        requires_runtime_context=True,
        permission_default=permission,
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": list(required),
            "properties": {field: FIELDS[field] for field in (*required, *optional)},
        },
    )


MODULE = ModuleDefinition(
    name="workspaces",
    label="Workspaces",
    icon="server",
    system=True,
    grouped_tool=True,
    description=(
        "Manage the user's workspaces exactly as the user does in Sentinel's Workspaces screen: "
        "same checks, defaults and errors. A workspace is a virtual machine on the user's Mac or a "
        "remote Mac; it boots a distribution (an OS recipe: a folder of files), optionally with a "
        "desktop, and shares one existing project folder from the Mac. This tool does not run "
        "commands in a workspace: attach one, then use runtime, computer and port_forward.\n"
        "USE A WORKSPACE: catalog -> create (starts preparing) -> attach once it runs.\n"
        "WAITING: create, start, update, reinstall and recover return at once while the workspace prepares (a first "
        "install takes 10-30 minutes). Never wait for it: no sleep, no polling list, no browser "
        "or http waits. End your turn or continue other work; a [Workspace Report] arrives with "
        "its final state (running, or failed with its error) and wakes you.\n"
        "WRITE A DISTRIBUTION: it needs NO workspace and NO shell. Never attach a workspace or use "
        "runtime or git to write, research or test one. Research on the web with the web and http "
        "tools. Pin each download with source (call it again, sparingly, until ready; use its "
        "sha256), and look inside a downloaded archive with source match and paths. Anything else is installed by the guest itself during its first boot "
        "(install.network). Work end to end without asking the user between steps:\n"
        "1. help (topic distributions), once. 2. distribution_read of ONE similar existing "
        "distribution: list its files, then read the ones you need, several per call. "
        "3. distribution: write "
        "the files over several calls, a few files each (each call keeps the files written "
        "before); the last one's problem must be null, else fix and write again. 4. create "
        "a test workspace from it (and the desktop it offers, if any), then end your turn. 5. on "
        "its [Workspace Report]: failed: log, fix with distribution, then reinstall THE SAME "
        "workspace (never create a new workspace per attempt). Repeat until it runs and its "
        "desktop works. Stop to ask only "
        "for a decision that is truly the user's; approvals already gate every change.\n"
        "RULES: be economical with calls. Take distribution, desktop and tool ids from catalog, "
        "never invent them. Bundled distributions are read-only (writable: false): write a copy "
        "under a new id. stop keeps the disk; delete removes the VM and disk, never the project "
        "folder."
    ),
    actions=[
        action(
            "list",
            "List workspaces",
            "The user's workspaces (id, name, machine_id, directory, distribution, desktop, "
            "development_tools, resources, state preparing|running|stopped|failed|recovering|"
            "unavailable, message, error, recovery_available, revision), this chat's attached_workspace_id, and the machines "
            "(id, name, provider, workspaces_available).",
        ),
        action(
            "catalog",
            "Machine catalog",
            "What a workspace on this machine can be: distributions (id, name, features, minimum "
            "resources), problems (distribution folders that could not be used, and why), desktops "
            "and tools (each with the distributions offering it). Read again on every call. Errors "
            "when the machine is unknown, unreachable or has no Sentinel Runtime.",
            required=["machine_id"],
        ),
        action(
            "usage",
            "Machine disk usage",
            "Bytes Sentinel uses on the machine's disk: workspaces (id, name, bytes: what deleting "
            "it frees), other (workspaces Sentinel does not list), shared (systems, packages, "
            "sources, installs, distributions), total, and the disk's free and capacity.",
            required=["machine_id"],
        ),
        action(
            "help",
            "Read sentineld's guide",
            "sentineld's own guide, as text: topic distributions (default) is the complete "
            "reference for writing a distribution.",
            required=["machine_id"],
            optional=["topic"],
        ),
        action(
            "distribution_read",
            "Read a distribution",
            "Explore a distribution's folder. Without paths: its files [{path, bytes}], folder, "
            "problem (null when usable) and writable. With paths: those files' text and "
            "executable bit, several in one call; a request larger than one reply holds is "
            "refused with each file's size, so ask for fewer. Files are always whole.",
            required=["machine_id", "distribution"],
            optional=["paths"],
        ),
        action(
            "source",
            "Download a source",
            "Download a file for install.sources into the machine's cache (no workspace needed). "
            "Replies {url, state downloading|ready, bytes, sha256 when ready} at once; call again "
            "later to follow it. Errors once when the download failed. Once ready, look inside an "
            "archive without a shell: match (a glob such as boot/*, *.ko, etc/mkinitcpio*) lists "
            "its members {path, bytes, type}; paths reads members whole ({bytes, text} or "
            "{bytes, binary: true}); with one path, offset and length read a byte range of a "
            "large member. A request over what one reply holds is refused with the matches per "
            "top folder or each member's size: narrow it.",
            required=["machine_id", "url"],
            optional=["match", "paths", "offset", "length"],
        ),
        action(
            "log",
            "Read a workspace's consoles",
            "Why a workspace failed or what it is doing: console (end of its newest boot's "
            "console), build (end of the install or layer console that made its disks) and "
            "failure (the workspace's error); null when there is none.",
            required=["workspace_id"],
            optional=["lines"],
        ),
        action(
            "create",
            "Create workspace",
            "Create a workspace and start preparing it; a [Workspace Report] tells you when it is "
            "running or failed. directory is an existing folder on the user's Mac shared into the "
            "workspace: ask the user which folder to use (once per task), never create one. "
            "Defaults: alpine, "
            "desktop none, browser chromium, no tools. Returns the workspace (state preparing, or "
            "failed with error). Errors: machine without Sentinel Runtime, name already used on "
            "the machine, invalid ids.",
            required=["name", "machine_id", "directory"],
            optional=["distribution", "desktop", "browser", "development_tools", "resources"],
            permission="approval",
        ),
        action(
            "start",
            "Start workspace",
            "Start a stopped workspace; returns it with state preparing. A workspace whose "
            "install failed is refused: fix its distribution, then reinstall it. A [Workspace Report] tells you when it is "
            "running or failed; do not wait for it.",
            required=["workspace_id"],
            permission="approval",
        ),
        action(
            "stop",
            "Stop workspace",
            "Power the workspace off; its disk and files stay. Errors while another chat's agent "
            "is running in it.",
            required=["workspace_id"],
            permission="approval",
        ),
        action(
            "update",
            "Update workspace",
            "Change a workspace's settings; omitted fields stay. Tools can only be added, disks "
            "only grow, and only a remote Mac's workspace may change distribution. Changing tools, "
            "desktop, browser, folder or resources applies them by preparing the workspace again "
            "(state preparing; a [Workspace Report] follows). "
            "Errors while other agents work in it or while it is preparing.",
            required=["workspace_id"],
            optional=[
                "name",
                "directory",
                "distribution",
                "desktop",
                "browser",
                "development_tools",
                "resources",
                "revision",
            ],
            permission="approval",
        ),
        action(
            "reinstall",
            "Reinstall workspace",
            "Erase the workspace's Linux disk and install it again from its distribution's current "
            "files, as the user's Reinstall button does: the retry after a failed install, once the "
            "distribution is fixed. The project folder is kept. Optional fields change its settings "
            "on the way (remote Macs: with revision from list). Returns it with state preparing; a "
            "[Workspace Report] follows. Errors while other agents work in it or while it is "
            "preparing.",
            required=["workspace_id"],
            optional=[
                "name",
                "directory",
                "distribution",
                "desktop",
                "browser",
                "development_tools",
                "resources",
                "revision",
            ],
            permission="approval",
        ),
        action(
            "recover",
            "Recover workspace",
            "For a workspace list shows with recovery_available (unresponsive, or a recovery was "
            "interrupted), as the user's Recover button does: powers the VM off, backs up its "
            "disk and checks the filesystem, keeping the disk; it ends stopped (then start it). "
            "Running commands are lost. Not for a failed install: reinstall that. A "
            "[Workspace Report] follows.",
            required=["workspace_id"],
            permission="approval",
        ),
        action(
            "attach",
            "Attach workspace to this chat",
            "Attach a workspace to this chat (workspace_id), as the user's Attach button does, or "
            "detach it (detach: true). runtime, computer and port_forward then work in it; start "
            "it first when it is stopped. Errors in a sub-agent's chat and while the current "
            "workspace's terminals run commands.",
            optional=["workspace_id", "detach"],
            permission="approval",
        ),
        action(
            "distribution",
            "Write distribution",
            "Create or change a distribution in sentineld's own folder on that machine. Write its "
            "files over several calls, a few files each: files a call does not name stay as they "
            "are. Returns its folder, files, problem (null when usable: fix and write again "
            "otherwise) and writable. Errors when the id is a read-only distribution.",
            required=["machine_id", "distribution", "files"],
            permission="approval",
        ),
        action(
            "delete",
            "Delete workspace",
            "Delete a workspace: its VM and disk. The project folder is never touched. Chats "
            "using it are detached, which needs detach_sessions: true; errors while agents run "
            "in them.",
            required=["workspace_id"],
            optional=["detach_sessions"],
            permission="deny",
        ),
        action(
            "distribution_delete",
            "Delete distribution",
            "Remove a distribution written with distribution. Errors while a workspace uses it, "
            "and for bundled distributions.",
            required=["machine_id", "distribution"],
            permission="deny",
        ),
    ],
)
