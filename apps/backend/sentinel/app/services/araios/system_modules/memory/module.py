from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import (
    handle_delete,
    handle_get_node,
    handle_list_children,
    handle_move,
    handle_roots,
    handle_search,
    handle_store,
    handle_touch,
    handle_tree,
    handle_update,
)


def _id_prop() -> dict:
    return {"type": "string", "description": "Memory node ID."}


def _content_prop() -> dict:
    return {"type": "string", "description": "Memory content."}


def _category_prop() -> dict:
    return {
        "type": "string",
        "enum": ["core", "preference", "project", "correction"],
        "description": "Memory category.",
    }


def _title_prop() -> dict:
    return {"type": "string", "description": "Optional memory title."}


def _summary_prop() -> dict:
    return {"type": "string", "description": "Optional summary."}


def _parent_id_prop() -> dict:
    return {"type": "string", "description": "Parent node ID."}


def _importance_prop() -> dict:
    return {"type": "integer", "description": "Importance score."}


def _pinned_prop() -> dict:
    return {"type": "boolean", "description": "Whether the memory is pinned."}


def _metadata_prop() -> dict:
    return {"type": "object", "description": "Optional memory metadata."}


def _embedding_prop() -> dict:
    return {"type": "array", "items": {"type": "number"}, "description": "Optional embedding vector."}


def _root_id_prop() -> dict:
    return {"type": "string", "description": "Root memory node ID."}


def _max_depth_prop() -> dict:
    return {"type": "integer", "description": "Maximum tree depth to expand."}


def _include_content_prop() -> dict:
    return {"type": "boolean", "description": "Include full content in the response."}


def _node_ids_prop() -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": "Memory node IDs."}


def _target_parent_id_prop() -> dict:
    return {"type": "string", "description": "Target parent node ID."}


def _to_root_prop() -> dict:
    return {"type": "boolean", "description": "Move the node to the root level."}


def _query_prop() -> dict:
    return {"type": "string", "description": "Search query."}


def _limit_prop() -> dict:
    return {"type": "integer", "description": "Maximum number of results."}


def _auto_expand_prop() -> dict:
    return {"type": "boolean", "description": "Auto-expand related results."}


def _store_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["content"],
        "properties": {
            "content": _content_prop(),
            "category": _category_prop(),
            "title": _title_prop(),
            "summary": _summary_prop(),
            "parent_id": _parent_id_prop(),
            "importance": _importance_prop(),
            "pinned": _pinned_prop(),
            "metadata": _metadata_prop(),
            "embedding": _embedding_prop(),
        },
    }


def _roots_parameters_schema() -> dict:
    return {"type": "object", "additionalProperties": False, "properties": {}}


def _tree_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "category": _category_prop(),
            "root_id": _root_id_prop(),
            "max_depth": _max_depth_prop(),
            "include_content": _include_content_prop(),
        },
    }


def _get_node_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["id"],
        "properties": {
            "id": _id_prop(),
            "include_content": _include_content_prop(),
        },
    }


def _list_children_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["parent_id"],
        "properties": {
            "parent_id": _parent_id_prop(),
            "include_content": _include_content_prop(),
        },
    }


def _update_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["id"],
        "properties": {
            "id": _id_prop(),
            "content": _content_prop(),
            "category": _category_prop(),
            "title": _title_prop(),
            "summary": _summary_prop(),
            "importance": _importance_prop(),
            "pinned": _pinned_prop(),
            "metadata": _metadata_prop(),
        },
    }


def _touch_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["node_ids"],
        "properties": {
            "node_ids": _node_ids_prop(),
        },
    }


def _move_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["node_ids"],
        "properties": {
            "node_ids": _node_ids_prop(),
            "target_parent_id": _target_parent_id_prop(),
            "to_root": _to_root_prop(),
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


def _search_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {
            "query": _query_prop(),
            "limit": _limit_prop(),
            "auto_expand": _auto_expand_prop(),
        },
    }


MODULE = ModuleDefinition(
    name="memory",
    label="Memory",
    description="Hierarchical memory tree operations from one unified entry point.",
    icon="brain",
    pinned=True,
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="store",
            label="Store Memory",
            description="Create a new memory node.",
            handler=handle_store,
            parameters_schema=_store_parameters_schema(),
        ),
        ActionDefinition(
            id="roots",
            label="List Memory Roots",
            description="List root memory nodes.",
            handler=handle_roots,
            parameters_schema=_roots_parameters_schema(),
        ),
        ActionDefinition(
            id="tree",
            label="Get Memory Tree",
            description="Expand one memory tree from a root node.",
            handler=handle_tree,
            parameters_schema=_tree_parameters_schema(),
        ),
        ActionDefinition(
            id="get_node",
            label="Get Memory Node",
            description="Get one memory node by ID.",
            handler=handle_get_node,
            parameters_schema=_get_node_parameters_schema(),
        ),
        ActionDefinition(
            id="list_children",
            label="List Memory Children",
            description="List direct children for a parent node.",
            handler=handle_list_children,
            parameters_schema=_list_children_parameters_schema(),
        ),
        ActionDefinition(
            id="update",
            label="Update Memory Node",
            description="Update one memory node by ID.",
            handler=handle_update,
            parameters_schema=_update_parameters_schema(),
        ),
        ActionDefinition(
            id="touch",
            label="Touch Memory Nodes",
            description="Refresh recency for one or more memory nodes.",
            handler=handle_touch,
            parameters_schema=_touch_parameters_schema(),
        ),
        ActionDefinition(
            id="move",
            label="Move Memory Node",
            description="Move a memory node under another parent or to the root.",
            handler=handle_move,
            parameters_schema=_move_parameters_schema(),
        ),
        ActionDefinition(
            id="delete",
            label="Delete Memory Node",
            description="Delete a memory node by ID.",
            handler=handle_delete,
            parameters_schema=_delete_parameters_schema(),
        ),
        ActionDefinition(
            id="search",
            label="Search Memory",
            description="Search memory content and metadata.",
            handler=handle_search,
            parameters_schema=_search_parameters_schema(),
        ),
    ],
)
