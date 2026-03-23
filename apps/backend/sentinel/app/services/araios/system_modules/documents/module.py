from __future__ import annotations

from app.services.araios.module_types import (
    ActionDefinition,
    FieldDefinition,
    FieldsConfig,
    ModuleDefinition,
)

from .handlers import ALLOWED_DOCUMENT_COMMANDS, handle_run


def _documents_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["command"],
        "properties": {
            "command": {
                "type": "string",
                "enum": list(ALLOWED_DOCUMENT_COMMANDS),
                "description": "Document command: list, get, create, update, or delete.",
            },
            "id": {"type": "string", "description": "Document ID for get, update, or delete."},
            "slug": {"type": "string", "description": "Document slug for get or create."},
            "tag": {"type": "string", "description": "Optional tag filter for list."},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "author": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }


MODULE = ModuleDefinition(
    name="documents",
    label="Documents",
    description="Create, read, update, and delete versioned markdown documents with tags and authorship.",
    icon="file-text",
    pinned=False,
    system=True,
    fields=[
        FieldDefinition(key="title", label="Title", type="text", required=True),
        FieldDefinition(key="slug", label="Slug", type="text", required=True),
        FieldDefinition(key="content", label="Content", type="textarea"),
        FieldDefinition(key="author", label="Author", type="text", required=True),
        FieldDefinition(key="tags", label="Tags", type="tags"),
    ],
    fields_config=FieldsConfig(
        titleField="title",
        subtitleField="slug",
        filterField="tags",
    ),
    actions=[
        ActionDefinition(
            id="run",
            label="Documents",
            description="Unified documents entry point. Use command=list, get, create, update, or delete.",
            handler=handle_run,
            parameters_schema=_documents_parameters_schema(),
        )
    ],
)
