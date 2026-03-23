from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import handle_edit


MODULE = ModuleDefinition(
    name="str_replace_editor",
    label="Str Replace Editor",
    description=(
        "Replace an exact string in a file with a new string. "
        "Runs through the same user sandbox runtime path as runtime_exec and requires a unique exact match."
    ),
    icon="file-edit",
    pinned=True,
    system=True,
    actions=[
        ActionDefinition(
            id="edit",
            label="Edit File",
            description=(
                "Replace an exact string in a file with a new string. "
                "Runs through the same user sandbox runtime path as runtime_exec and requires a unique exact match."
            ),
            handler=handle_edit,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "old_str", "new_str"],
                "properties": {
                    "session_id": {"type": "string", "description": "Current session ID (auto-injected in agent loop)"},
                    "path": {"type": "string", "description": "Path to the file relative to workspace"},
                    "old_str": {"type": "string", "description": "The exact string to find in the file (must be unique)"},
                    "new_str": {"type": "string", "description": "The replacement string"},
                },
            },
        )
    ],
)
