from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import ALLOWED_MEMORY_COMMANDS, handle_run


def _memory_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["command"],
        "properties": {
            "command": {"type": "string", "enum": list(ALLOWED_MEMORY_COMMANDS)},
            "content": {"type": "string"},
            "category": {
                "type": "string",
                "enum": ["core", "preference", "project", "correction"],
            },
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "parent_id": {"type": "string"},
            "importance": {"type": "integer"},
            "pinned": {"type": "boolean"},
            "metadata": {"type": "object"},
            "embedding": {"type": "array", "items": {"type": "number"}},
            "root_id": {"type": "string"},
            "max_depth": {"type": "integer"},
            "include_content": {"type": "boolean"},
            "id": {"type": "string"},
            "node_ids": {"type": "array", "items": {"type": "string"}},
            "target_parent_id": {"type": "string"},
            "to_root": {"type": "boolean"},
            "query": {"type": "string"},
            "limit": {"type": "integer"},
            "auto_expand": {"type": "boolean"},
        },
    }


MODULE = ModuleDefinition(
    name="memory",
    label="Memory",
    description="Hierarchical memory tree operations from one unified entry point.",
    icon="brain",
    pinned=True,
    system=True,
    actions=[
        ActionDefinition(
            id="run",
            label="Memory",
            description="Unified hierarchical memory tool.",
            handler=handle_run,
            parameters_schema=_memory_parameters_schema(),
        )
    ],
)
