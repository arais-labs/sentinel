from __future__ import annotations

from app.services.modules.definitions import ActionDefinition, ModuleDefinition

from .handlers import handle_edit

MODULE = ModuleDefinition(
    name="str_replace_editor",
    label="Str Replace Editor",
    description=(
        "Replace one exact string in a file inside the attached workspace container. "
        "The old string must match exactly once."
    ),
    icon="file-edit",
    system=True,
    actions=[
        ActionDefinition(
            id="edit",
            label="Edit File",
            description=(
                "Replace one exact string in a container file. Use enough surrounding "
                "context in old_str to make the match unique."
            ),
            handler=handle_edit,
            requires_runtime_context=True,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "old_str", "new_str"],
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path inside the container. Relative paths start at the attached project directory; absolute paths within the shared project are identical on host and container. ~/ refers to the container home, not the host home.",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "Exact string to replace. It must appear exactly once.",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Replacement string.",
                    },
                },
            },
        )
    ],
)
