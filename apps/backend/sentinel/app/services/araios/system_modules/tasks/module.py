from __future__ import annotations

from app.services.araios.module_types import (
    ActionDefinition,
    FieldDefinition,
    FieldsConfig,
    ModuleDefinition,
)

from .handlers import ALLOWED_TASK_COMMANDS, handle_run


def _tasks_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["command"],
        "properties": {
            "command": {
                "type": "string",
                "enum": list(ALLOWED_TASK_COMMANDS),
                "description": "Task command: list, create, update, or delete.",
            },
            "id": {"type": "string", "description": "Task ID for update/delete."},
            "title": {"type": "string"},
            "status": {"type": "string", "description": "Optional task status or status filter."},
            "priority": {"type": "string", "description": "Optional task priority or priority filter."},
            "owner": {"type": "string"},
            "client": {"type": "string"},
            "summary": {"type": "string"},
            "notes": {"type": "string"},
        },
    }


MODULE = ModuleDefinition(
    name="tasks",
    label="Tasks",
    description="Track and manage tasks with status, priority, ownership, and notes.",
    icon="check-square",
    pinned=False,
    system=True,
    fields=[
        FieldDefinition(key="title", label="Title", type="text", required=True),
        FieldDefinition(
            key="status",
            label="Status",
            type="select",
            options=["open", "in_progress", "review", "done", "closed"],
        ),
        FieldDefinition(
            key="priority",
            label="Priority",
            type="select",
            options=["low", "medium", "high", "critical"],
        ),
        FieldDefinition(key="owner", label="Owner", type="text"),
        FieldDefinition(key="client", label="Client", type="text"),
        FieldDefinition(key="summary", label="Summary", type="textarea"),
        FieldDefinition(key="notes", label="Notes", type="textarea"),
    ],
    fields_config=FieldsConfig(
        titleField="title",
        badgeField="status",
        filterField="status",
    ),
    actions=[
        ActionDefinition(
            id="run",
            label="Tasks",
            description="Unified task entry point. Use command=list, create, update, or delete.",
            handler=handle_run,
            parameters_schema=_tasks_parameters_schema(),
        )
    ],
)
