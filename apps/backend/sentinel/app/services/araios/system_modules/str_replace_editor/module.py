from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import handle_edit


MODULE = ModuleDefinition(
    name="str_replace_editor",
    label="Str Replace Editor",
    description=(
        "Replace one exact string in a workspace file. The target path is scoped "
        "to the session runtime workspace and the old string must match exactly once."
    ),
    icon="file-edit",
    system=True,
    actions=[
        ActionDefinition(
            id="edit",
            label="Edit File",
            description=(
                "Replace one exact string in a workspace file. Use enough surrounding "
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
                        "description": "File path relative to the session workspace.",
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
