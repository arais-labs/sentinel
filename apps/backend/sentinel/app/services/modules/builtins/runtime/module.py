from app.services.modules.definitions import ActionDefinition, ModuleDefinition

from .handlers import action_handler, handle_workspace

PANE = {
    "type": "string",
    "pattern": "^%[0-9]+$",
    "description": "Stable pane ID from terminal_list, scoped to this chat. Never infer it from UI focus.",
}
WINDOW = {
    "type": "string",
    "pattern": "^@[0-9]+$",
    "description": "Stable window ID from terminal_list.",
}


def action(name, label, description, properties=None, required=None):
    return ActionDefinition(
        id=name,
        label=label,
        description=description,
        handler=handle_workspace if name == "workspace" else action_handler(name),
        requires_runtime_context=True,
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": properties or {},
            "required": required or [],
        },
    )


MODULE = ModuleDefinition(
    name="runtime",
    label="Runtime",
    icon="terminal",
    system=True,
    grouped_tool=True,
    description="Run commands in the attached workspace's persistent Linux container. The shared project retains its absolute host path (shown in workspace context); /root, /tmp, installed packages, and Docker data stay inside this workspace. Install missing tools with the workspace package manager (apk on Alpine, apt-get on Ubuntu/Debian) or under $HOME/.local/bin (on PATH). Reuse setup across sessions. Selected database services and their connection details are in /etc/sentinel/services/README.md. Use exec for shell commands, pane_input for interactive input, and window/pane actions for terminal layout. Container root cannot modify the host outside the shared project.",
    actions=[
        action(
            "workspace",
            "Workspace status",
            "Inspect the attached workspace and available workspaces without creating shells. Ask the user to attach a workspace if needed.",
        ),
        action(
            "exec",
            "Execute shell command",
            "Execute in a sandboxed Bash pane. Does not elevate privileges. Omit pane_id only when no pane exists (creates main) or exactly one pane exists. State persists in that shell; multiline commands use a subshell. background returns immediately and reports completion later; it uses the same execution path. Timeout stops waiting, not the process. Output combines stdout and stderr.",
            {
                "shell_command": {"type": "string", "minLength": 1},
                "pane_id": PANE,
                "cwd": {
                    "type": "string",
                    "description": "Optional directory inside the container; omit to retain shell cwd.",
                },
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
                "background": {"type": "boolean"},
            },
            ["shell_command"],
        ),
        action(
            "terminal_list",
            "List terminal tree",
            "List native windows and all panes, including user-created panes. Does not start a shell.",
        ),
        action(
            "window_create",
            "Create window",
            "Create a window with a sandboxed shell; returns window_id and pane_id.",
            {"name": {"type": "string", "minLength": 1}},
        ),
        action(
            "window_rename",
            "Rename window",
            "Set the window name displayed in the terminal and chat pills. Keep targeting by window_id.",
            {"window_id": WINDOW, "name": {"type": "string", "minLength": 1}},
            ["window_id", "name"],
        ),
        action(
            "pane_rename",
            "Rename pane",
            "Set an optional pane title displayed beside its running process. Empty title clears it. Keep targeting by pane_id.",
            {
                "pane_id": PANE,
                "title": {
                    "type": "string",
                    "description": "Descriptive pane title; empty clears it. The UI also shows the running process.",
                },
            },
            ["pane_id", "title"],
        ),
        action(
            "window_close",
            "Close window",
            "Close a window and all its panes, terminating their processes.",
            {"window_id": WINDOW},
            ["window_id"],
        ),
        action(
            "pane_split",
            "Split pane",
            "Create a sandboxed shell beside a pane (horizontal) or below it (vertical). Returns the new pane ID.",
            {
                "pane_id": PANE,
                "direction": {"type": "string", "enum": ["horizontal", "vertical"]},
                "title": {
                    "type": "string",
                    "description": "Descriptive pane title; empty clears it. The UI also shows the running process.",
                },
            },
            ["pane_id"],
        ),
        action(
            "pane_close",
            "Close pane",
            "Close only this pane, terminating its processes.",
            {"pane_id": PANE},
            ["pane_id"],
        ),
        action(
            "pane_read",
            "Read pane",
            "Read recent output from a pane, including interactive programs. Do not poll for background completion.",
            {
                "pane_id": PANE,
                "lines": {"type": "integer", "minimum": 1, "maximum": 2000},
            },
            ["pane_id"],
        ),
        action(
            "pane_input",
            "Send pane input",
            "Send literal text and/or a key to an interactive process. Text alone does not press Enter. Do not interrupt user work without authorization.",
            {
                "pane_id": PANE,
                "text": {"type": "string"},
                "key": {
                    "type": "string",
                    "enum": [
                        "Enter",
                        "Tab",
                        "Escape",
                        "C-c",
                        "C-d",
                        "C-z",
                        "Up",
                        "Down",
                        "Left",
                        "Right",
                    ],
                },
            },
            ["pane_id"],
        ),
    ],
)
