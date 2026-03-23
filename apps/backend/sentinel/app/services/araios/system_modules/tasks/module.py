from __future__ import annotations

from app.services.araios.module_types import (
    ActionDefinition,
    FieldDefinition,
    FieldsConfig,
    ModuleDefinition,
)

from .handlers import handle_create, handle_delete, handle_list, handle_update


def _id_prop() -> dict:
    return {"type": "string", "description": "Task ID."}


def _title_prop() -> dict:
    return {"type": "string", "description": "Task title."}


def _status_prop() -> dict:
    return {"type": "string", "description": "Task status or status filter."}


def _priority_prop() -> dict:
    return {"type": "string", "description": "Task priority or priority filter."}


def _owner_prop() -> dict:
    return {"type": "string", "description": "Task owner."}


def _client_prop() -> dict:
    return {"type": "string", "description": "Related client."}


def _summary_prop() -> dict:
    return {"type": "string", "description": "Task summary."}


def _notes_prop() -> dict:
    return {"type": "string", "description": "Task notes."}


def _list_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": _status_prop(),
            "priority": _priority_prop(),
        },
    }


def _create_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["title"],
        "properties": {
            "title": _title_prop(),
            "status": _status_prop(),
            "priority": _priority_prop(),
            "owner": _owner_prop(),
            "client": _client_prop(),
            "summary": _summary_prop(),
            "notes": _notes_prop(),
        },
    }


def _update_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["id"],
        "properties": {
            "id": _id_prop(),
            "title": _title_prop(),
            "status": _status_prop(),
            "priority": _priority_prop(),
            "owner": _owner_prop(),
            "client": _client_prop(),
            "summary": _summary_prop(),
            "notes": _notes_prop(),
        },
    }


def _delete_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["id"],
        "properties": {
            "id": _id_prop(),
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
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="list",
            label="List Tasks",
            description="List tasks, optionally filtered by status and priority.",
            handler=handle_list,
            parameters_schema=_list_parameters_schema(),
        ),
        ActionDefinition(
            id="create",
            label="Create Task",
            description="Create a new task.",
            handler=handle_create,
            parameters_schema=_create_parameters_schema(),
        ),
        ActionDefinition(
            id="update",
            label="Update Task",
            description="Update an existing task by ID.",
            handler=handle_update,
            parameters_schema=_update_parameters_schema(),
        ),
        ActionDefinition(
            id="delete",
            label="Delete Task",
            description="Delete a task by ID.",
            handler=handle_delete,
            parameters_schema=_delete_parameters_schema(),
        ),
    ],
)
