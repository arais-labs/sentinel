from __future__ import annotations

from app.services.araios.module_types import (
    ActionDefinition,
    FieldDefinition,
    FieldsConfig,
    ModuleDefinition,
)

from .handlers import handle_create, handle_delete, handle_get, handle_list, handle_update


def _id_prop() -> dict:
    return {"type": "string", "description": "Document ID."}


def _slug_prop() -> dict:
    return {"type": "string", "description": "Document slug."}


def _title_prop() -> dict:
    return {"type": "string", "description": "Document title."}


def _content_prop() -> dict:
    return {"type": "string", "description": "Document markdown content."}


def _author_prop() -> dict:
    return {"type": "string", "description": "Author or editor identifier."}


def _tags_prop() -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": "Document tags."}


def _list_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "tag": {"type": "string", "description": "Optional tag filter."},
        },
    }


def _get_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": _id_prop(),
            "slug": _slug_prop(),
        },
    }


def _create_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "slug", "author"],
        "properties": {
            "title": _title_prop(),
            "slug": _slug_prop(),
            "content": _content_prop(),
            "author": _author_prop(),
            "tags": _tags_prop(),
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
            "content": _content_prop(),
            "author": _author_prop(),
            "tags": _tags_prop(),
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
    name="documents",
    label="Documents",
    description="Create, read, update, and delete versioned markdown documents with tags and authorship.",
    icon="file-text",
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
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="list",
            label="List Documents",
            description="List all documents, optionally filtered by tag.",
            handler=handle_list,
            parameters_schema=_list_parameters_schema(),
        ),
        ActionDefinition(
            id="get",
            label="Get Document",
            description="Get a document by ID or slug.",
            handler=handle_get,
            parameters_schema=_get_parameters_schema(),
        ),
        ActionDefinition(
            id="create",
            label="Create Document",
            description="Create a new document.",
            handler=handle_create,
            parameters_schema=_create_parameters_schema(),
        ),
        ActionDefinition(
            id="update",
            label="Update Document",
            description="Update an existing document by ID.",
            handler=handle_update,
            parameters_schema=_update_parameters_schema(),
        ),
        ActionDefinition(
            id="delete",
            label="Delete Document",
            description="Delete a document by ID.",
            handler=handle_delete,
            parameters_schema=_delete_parameters_schema(),
        ),
    ],
)
