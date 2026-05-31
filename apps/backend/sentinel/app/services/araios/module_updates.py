"""Shared module-edit logic used by both the REST router and the agent edit_module command.

Keeping the partial-update primitives here (instead of inside the router) means the human UI
path (PATCH /modules/{name}) and the agent path (module_manager command=edit_module) apply edits
through the exact same code and cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.models.araios import AraiosModule
from app.schemas.modules import (
    EditModuleRequest,
    ModuleFieldDefinition,
    ModuleFieldsConfig,
    ModuleSecretDefinition,
)
from app.services.araios.dynamic_modules import normalize_dynamic_module_actions

MODULE_MUTABLE_FIELDS = (
    "label",
    "icon",
    "fields",
    "fields_config",
    "actions",
    "secrets",
    "description",
    "order",
    "page_title",
    "page_content",
)


def validate_action_updates(value: Any) -> list[dict[str, Any]]:
    """Validate/normalize an actions list. Raises ValueError on bad input."""
    if not isinstance(value, list):
        raise ValueError("'actions' must be a list")
    return normalize_dynamic_module_actions(value)


def extract_module_updates(body: Any) -> dict[str, Any]:
    """Pull the set of mutable fields the caller actually provided. Raises ValueError if none."""
    updates: dict[str, Any] = {}
    update_values = body.module_updates()
    for name in MODULE_MUTABLE_FIELDS:
        if name in update_values:
            value = update_values[name]
            if name == "actions":
                value = validate_action_updates(value)
            updates[name] = value
    if not updates:
        raise ValueError(
            "At least one editable module field is required "
            f"({', '.join(MODULE_MUTABLE_FIELDS)})"
        )
    return updates


def merge_action_updates(
    existing_actions: list[Any], patch_actions: list[dict[str, Any]]
) -> list[Any]:
    """Upsert patch actions into existing actions by id (replace matching id, append new)."""
    merged: list[Any] = list(existing_actions)
    action_index: dict[str, int] = {}
    for idx, action in enumerate(merged):
        if not isinstance(action, dict):
            continue
        action_id = action.get("id")
        if isinstance(action_id, str) and action_id and action_id not in action_index:
            action_index[action_id] = idx
    for action in patch_actions:
        action_id = action["id"]
        existing_idx = action_index.get(action_id)
        if existing_idx is None:
            action_index[action_id] = len(merged)
            merged.append(action)
            continue
        merged[existing_idx] = action
    return merged


def apply_module_updates(mod: AraiosModule, updates: dict[str, Any]) -> None:
    """Apply a dict of mutable-field updates to a module, merging actions by id."""
    resolved_updates = dict(updates)
    if "actions" in resolved_updates:
        resolved_updates["actions"] = merge_action_updates(
            list(mod.actions or []),
            resolved_updates["actions"],
        )
    for name, value in resolved_updates.items():
        setattr(mod, name, value)


# ── Ordered-ops engine (agent edit_module) ──


@dataclass
class ModuleEditDelta:
    """The computed result of folding an ops list — applied to the ORM by the handler."""

    updates: dict[str, Any] = field(default_factory=dict)
    final_actions: list[dict[str, Any]] | None = None
    permissions: dict[str, str] | None = None
    record_renames: list[tuple[str, str]] = field(default_factory=list)
    record_purge_keys: set[str] = field(default_factory=set)
    secret_purge_keys: set[str] = field(default_factory=set)

    def is_empty(self) -> bool:
        return not (
            self.updates
            or self.final_actions is not None
            or self.permissions
            or self.record_renames
            or self.record_purge_keys
            or self.secret_purge_keys
        )


def _field_index(fields: list[dict[str, Any]], key: str) -> int | None:
    return next((i for i, f in enumerate(fields) if f.get("key") == key), None)


def _action_index(actions: list[dict[str, Any]], action_id: str) -> int | None:
    return next(
        (i for i, a in enumerate(actions) if str(a.get("id") or "").strip().lower() == action_id),
        None,
    )


def _secret_index(secrets: list[dict[str, Any]], key: str) -> int | None:
    return next((i for i, s in enumerate(secrets) if s.get("key") == key), None)


def fold_ops_into_delta(mod: AraiosModule, request: EditModuleRequest) -> ModuleEditDelta:
    """Pure fold of an ordered ops list over working copies — never touches the ORM.

    Returns a ModuleEditDelta the handler applies transactionally. Any op error raises ValueError
    with an index-qualified message, so a failing op aborts the whole edit (all-or-nothing).
    """
    fields = [dict(f) for f in (mod.fields or []) if isinstance(f, dict)]
    actions = [dict(a) for a in (mod.actions or []) if isinstance(a, dict)]
    secrets = [dict(s) for s in (mod.secrets or []) if isinstance(s, dict)]
    fields_config = dict(mod.fields_config or {})
    scalars: dict[str, Any] = {}
    permissions: dict[str, str] = {}
    record_renames: list[tuple[str, str]] = []
    record_purge_keys: set[str] = set()
    secret_purge_keys: set[str] = set()
    fields_touched = actions_touched = fields_config_touched = secrets_touched = False

    for index, op in enumerate(request.ops):
        where = f"op #{index + 1} ({op.op})"
        kind = op.op

        if kind == "set_meta":
            for attr in ("label", "icon", "description", "order", "page_title", "page_content"):
                value = getattr(op, attr)
                if value is not None:
                    scalars[attr] = value

        elif kind == "add_field":
            field_dict = op.field.model_dump(exclude_none=True)
            if _field_index(fields, field_dict["key"]) is not None:
                raise ValueError(
                    f"{where}: field '{field_dict['key']}' already exists; use update_field"
                )
            if op.position is not None and 0 <= op.position <= len(fields):
                fields.insert(op.position, field_dict)
            else:
                fields.append(field_dict)
            fields_touched = True

        elif kind == "update_field":
            idx = _field_index(fields, op.key)
            if idx is None:
                raise ValueError(f"{where}: field '{op.key}' not found")
            if "key" in op.changes:
                raise ValueError(f"{where}: cannot change 'key' via update_field; use rename_field")
            merged = {**fields[idx], **op.changes}
            try:
                fields[idx] = ModuleFieldDefinition.model_validate(merged).model_dump(
                    exclude_none=True
                )
            except ValidationError as exc:
                raise ValueError(f"{where}: invalid field after changes: {exc}") from exc
            fields_touched = True

        elif kind == "rename_field":
            idx = _field_index(fields, op.from_key)
            if idx is None:
                raise ValueError(f"{where}: field '{op.from_key}' not found")
            if _field_index(fields, op.to_key) is not None:
                raise ValueError(f"{where}: field '{op.to_key}' already exists")
            fields[idx] = {**fields[idx], "key": op.to_key}
            for config_key, config_value in list(fields_config.items()):
                if config_value == op.from_key:
                    fields_config[config_key] = op.to_key
                    fields_config_touched = True
            if op.migrate_record_data:
                record_renames.append((op.from_key, op.to_key))
            fields_touched = True

        elif kind == "remove_field":
            idx = _field_index(fields, op.key)
            if idx is None:
                raise ValueError(f"{where}: field '{op.key}' not found")
            fields.pop(idx)
            for config_key, config_value in list(fields_config.items()):
                if config_value == op.key:
                    fields_config[config_key] = None
                    fields_config_touched = True
            if op.purge_record_data:
                record_purge_keys.add(op.key)
            fields_touched = True

        elif kind == "set_action":
            action_dict = dict(op.action)
            action_id = str(action_dict.get("id") or "").strip().lower()
            if not action_id:
                raise ValueError(f"{where}: action requires a non-empty 'id'")
            action_dict["id"] = action_id
            idx = _action_index(actions, action_id)
            if idx is None:
                actions.append(action_dict)
            else:
                actions[idx] = action_dict
            actions_touched = True

        elif kind == "patch_action":
            action_id = op.id.strip().lower()
            idx = _action_index(actions, action_id)
            if idx is None:
                raise ValueError(
                    f"{where}: action '{action_id}' not found; use set_action to create it"
                )
            actions[idx] = {**actions[idx], **op.set, "id": action_id}
            actions_touched = True

        elif kind == "remove_action":
            action_id = op.id.strip().lower()
            idx = _action_index(actions, action_id)
            if idx is None:
                raise ValueError(f"{where}: action '{action_id}' not found")
            actions.pop(idx)
            actions_touched = True

        elif kind == "set_fields_config":
            fields_config = op.fields_config.model_dump(exclude_none=True)
            fields_config_touched = True

        elif kind == "patch_fields_config":
            fields_config = {**fields_config, **op.config}
            fields_config_touched = True

        elif kind == "upsert_secret":
            secret_dict = op.secret.model_dump(exclude_none=True)
            idx = _secret_index(secrets, secret_dict["key"])
            if idx is None:
                secrets.append(secret_dict)
            else:
                secrets[idx] = secret_dict
            secrets_touched = True

        elif kind == "remove_secret":
            idx = _secret_index(secrets, op.key)
            if idx is not None:
                secrets.pop(idx)
                secrets_touched = True
            if op.purge_value:
                secret_purge_keys.add(op.key)

        elif kind == "set_permissions":
            permissions.update(op.permissions)

        else:  # pragma: no cover - guarded by the discriminated union
            raise ValueError(f"{where}: unknown op")

    if fields_touched or fields_config_touched:
        valid_keys = {f["key"] for f in fields}
        for config_key, config_value in fields_config.items():
            if config_value is not None and config_value not in valid_keys:
                raise ValueError(
                    f"fields_config.{config_key} references unknown field '{config_value}'"
                )

    if scalars:
        updates: dict[str, Any] = dict(scalars)
    else:
        updates = {}
    if fields_touched:
        updates["fields"] = fields
    if fields_config_touched:
        updates["fields_config"] = fields_config
    if secrets_touched:
        updates["secrets"] = secrets

    final_actions = normalize_dynamic_module_actions(actions) if actions_touched else None

    delta = ModuleEditDelta(
        updates=updates,
        final_actions=final_actions,
        permissions=permissions or None,
        record_renames=record_renames,
        record_purge_keys=record_purge_keys,
        secret_purge_keys=secret_purge_keys,
    )
    if delta.is_empty():
        raise ValueError("The provided ops produced no changes")
    return delta
