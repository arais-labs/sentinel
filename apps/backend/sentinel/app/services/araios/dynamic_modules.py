from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.araios import (
    AraiosModule,
    AraiosModuleRecord,
    AraiosModuleSecret,
    AraiosPermission,
    araios_gen_id,
)
from app.services.araios.executor import execute_action
from app.services.araios.module_types import ActionDefinition, ModuleDefinition, ParamDefinition
from app.services.secrets import is_invalid_secret

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


VALID_PERMISSION_LEVELS = {"allow", "approval", "deny"}
RESERVED_DYNAMIC_MODULE_COMMANDS = (
    "list_records",
    "get_record",
    "create_records",
    "update_records",
    "delete_records",
    "get_page",
    "edit_page",
)
_RECORD_ACTION_TYPES = {"record", "detail"}
_NON_EXECUTABLE_ACTION_TYPES = {"create", "delete"}
_DEFAULT_COMMAND_PERMISSION_LEVELS: dict[str, str] = {
    "list_records": "allow",
    "get_record": "allow",
    "create_records": "allow",
    "update_records": "allow",
    "delete_records": "approval",
    "get_page": "allow",
    "edit_page": "approval",
}
_RECORD_ID_PROPERTY = {"type": "string", "description": "Record ID."}
_RECORD_DATA_PROPERTY = {"type": "object", "description": "Record data."}
_LEGACY_COMMAND_ALIASES = {
    "create_records": "create_record",
    "update_records": "update_record",
    "delete_records": "delete_record",
}


def build_dynamic_module_definition(
    module: AraiosModule,
    *,
    permission_levels: dict[str, str] | None = None,
    session_factory: "async_sessionmaker[AsyncSession]",
) -> ModuleDefinition:
    normalized_actions = normalize_dynamic_module_actions(module.actions or [])
    custom_commands = _custom_action_commands(normalized_actions)
    levels = build_dynamic_module_permission_levels(
        module_name=module.name,
        actions=normalized_actions,
        permissions=permission_levels,
    )

    actions: list[ActionDefinition] = [
        ActionDefinition(
            id="list_records",
            label="List Records",
            description=f"List records in the {module.label} module.",
            handler=_make_list_records_handler(module.name, session_factory),
            parameters_schema={"type": "object", "additionalProperties": False, "properties": {}},
            permission_default=levels["list_records"],
        ),
        ActionDefinition(
            id="get_record",
            label="Get Record",
            description=f"Get one record from the {module.label} module.",
            handler=_make_get_record_handler(module.name, session_factory),
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["record_id"],
                "properties": {"record_id": dict(_RECORD_ID_PROPERTY)},
            },
            permission_default=levels["get_record"],
        ),
        ActionDefinition(
            id="create_records",
            label="Create Records",
            description=f"Create one or more records in the {module.label} module.",
            handler=_make_create_records_handler(module.name, session_factory),
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["records"],
                "properties": {
                    "records": {
                        "type": "array",
                        "minItems": 1,
                        "items": dict(_RECORD_DATA_PROPERTY),
                    }
                },
            },
            permission_default=levels["create_records"],
        ),
        ActionDefinition(
            id="update_records",
            label="Update Records",
            description=f"Update one or more existing records in the {module.label} module.",
            handler=_make_update_records_handler(module.name, session_factory),
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["updates"],
                "properties": {
                    "updates": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["record_id", "data"],
                            "properties": {
                                "record_id": dict(_RECORD_ID_PROPERTY),
                                "data": dict(_RECORD_DATA_PROPERTY),
                            },
                        },
                    },
                },
            },
            permission_default=levels["update_records"],
        ),
        ActionDefinition(
            id="delete_records",
            label="Delete Records",
            description=f"Delete one or more records from the {module.label} module.",
            handler=_make_delete_records_handler(module.name, session_factory),
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["record_ids"],
                "properties": {
                    "record_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    }
                },
            },
            permission_default=levels["delete_records"],
        ),
        ActionDefinition(
            id="get_page",
            label="Get Page",
            description=f"Get the markdown page for the {module.label} module.",
            handler=_make_get_page_handler(module.name, session_factory),
            parameters_schema={"type": "object", "additionalProperties": False, "properties": {}},
            permission_default=levels["get_page"],
        ),
        ActionDefinition(
            id="edit_page",
            label="Edit Page",
            description=f"Edit the markdown page for the {module.label} module.",
            handler=_make_edit_page_handler(module.name, session_factory),
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "page_title": {"type": "string", "description": "Updated page title."},
                    "page_content": {"type": "string", "description": "Updated page markdown."},
                },
            },
            permission_default=levels["edit_page"],
        ),
    ]

    for action in custom_commands:
        action_id = str(action["id"]).strip().lower()
        action_type = _normalized_action_type(action)
        is_record = action_type in _RECORD_ACTION_TYPES
        params = _params_from_action(action)
        if is_record:
            params = [ParamDefinition(key="record_id", label="Record ID", required=True), *params]
        actions.append(
            ActionDefinition(
                id=action_id,
                label=str(action.get("label") or action_id),
                description=str(action.get("description") or "").strip(),
                type="record" if is_record else "standalone",
                parameters_schema=_build_custom_action_parameters_schema(
                    raw_action=action,
                    requires_record=is_record,
                ),
                params=params or None,
                handler=_make_custom_action_handler(
                    module_name=module.name,
                    action=ActionDefinition(
                        id=action_id,
                        label=str(action.get("label") or action_id),
                        description=str(action.get("description") or "").strip(),
                        type="record" if is_record else "standalone",
                        code=str(action.get("code") or ""),
                    ),
                    raw_action=action,
                    session_factory=session_factory,
                ),
                permission_default=levels[action_id],
            )
        )

    return ModuleDefinition(
        name=module.name,
        label=module.label,
        description=module.description or "",
        icon=module.icon or "box",
        actions=actions,
        page_title=module.page_title,
        page_content=module.page_content,
        system=bool(module.system),
        order=int(module.order or 100),
        grouped_tool=True,
    )


def normalize_dynamic_module_actions(actions: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for entry in actions:
        if not isinstance(entry, dict):
            raise ValueError("Each action must be an object.")
        raw_id = entry.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ValueError("Each action requires a non-empty 'id'.")
        normalized_entry = dict(entry)
        normalized_entry["id"] = raw_id.strip().lower()
        raw_type = entry.get("type", entry.get("placement", "standalone"))
        normalized_entry["type"] = str(raw_type or "standalone").strip().lower()
        normalized.append(normalized_entry)

    seen: set[str] = set()
    for entry in _custom_action_commands(normalized):
        action_id = str(entry["id"]).strip().lower()
        if action_id in RESERVED_DYNAMIC_MODULE_COMMANDS:
            raise ValueError(f"Action '{action_id}' uses a reserved command name.")
        if action_id in seen:
            raise ValueError(f"Action '{action_id}' is duplicated across module actions.")
        seen.add(action_id)

    return normalized


def build_dynamic_module_permission_levels(
    *,
    module_name: str,
    actions: list[dict[str, Any]],
    permissions: dict[str, Any] | None = None,
    existing: dict[str, str] | None = None,
) -> dict[str, str]:
    commands = set(RESERVED_DYNAMIC_MODULE_COMMANDS)
    commands.update(
        str(action["id"]).strip().lower() for action in _custom_action_commands(actions)
    )

    overrides = {
        str(key).strip().lower(): _normalize_permission_level(value)
        for key, value in (permissions or {}).items()
    }
    invalid_override = next((key for key, value in overrides.items() if value is None), None)
    if invalid_override is not None:
        raise ValueError(f"Permission '{invalid_override}' must be one of: allow, approval, deny.")
    legacy_override_commands = set(_LEGACY_COMMAND_ALIASES.values())
    unknown_override = sorted(
        key for key in overrides if key not in commands and key not in legacy_override_commands
    )
    if unknown_override:
        raise ValueError(
            f"Unknown permission command(s) for module '{module_name}': {', '.join(unknown_override)}."
        )

    existing_levels = {
        str(key).strip().lower(): _normalize_permission_level(value) or "allow"
        for key, value in (existing or {}).items()
    }
    levels: dict[str, str] = {}
    for command in sorted(commands):
        if command in overrides:
            levels[command] = overrides[command] or "allow"
            continue
        legacy_command = _LEGACY_COMMAND_ALIASES.get(command)
        if legacy_command and legacy_command in overrides:
            levels[command] = overrides[legacy_command] or "allow"
            continue
        if command in existing_levels:
            levels[command] = existing_levels[command]
            continue
        if legacy_command and legacy_command in existing_levels:
            levels[command] = existing_levels[legacy_command]
            continue
        levels[command] = _DEFAULT_COMMAND_PERMISSION_LEVELS.get(command, "allow")
    return levels


async def sync_dynamic_module_permissions(
    db: "AsyncSession",
    *,
    module_name: str,
    actions: list[dict[str, Any]],
    permissions: dict[str, Any] | None = None,
) -> dict[str, str]:
    result = await db.execute(select(AraiosPermission))
    existing_rows = [
        row
        for row in result.scalars().all()
        if isinstance(getattr(row, "action", None), str)
        and row.action.startswith(f"{module_name}.")
    ]
    existing = {
        row.action[len(module_name) + 1 :]: str(row.level or "").strip().lower()
        for row in existing_rows
    }
    levels = build_dynamic_module_permission_levels(
        module_name=module_name,
        actions=actions,
        permissions=permissions,
        existing=existing,
    )
    kept_actions = {f"{module_name}.{command}" for command in levels}
    for row in existing_rows:
        if row.action not in kept_actions:
            await db.delete(row)
            continue
        row.level = levels[row.action[len(module_name) + 1 :]]
    existing_actions = {row.action for row in existing_rows}
    for command, level in levels.items():
        action_key = f"{module_name}.{command}"
        if action_key in existing_actions:
            continue
        legacy_command = _LEGACY_COMMAND_ALIASES.get(command)
        legacy_key = f"{module_name}.{legacy_command}" if legacy_command else None
        if legacy_key and legacy_key in existing_actions:
            row = next(
                (
                    existing_row
                    for existing_row in existing_rows
                    if existing_row.action == legacy_key
                ),
                None,
            )
            if row is not None:
                row.action = action_key
                row.level = level
            continue
        db.add(AraiosPermission(action=action_key, level=level))
    await db.commit()
    return levels


async def delete_dynamic_module_permissions(
    db: "AsyncSession",
    *,
    module_name: str,
) -> None:
    result = await db.execute(select(AraiosPermission))
    for row in result.scalars().all():
        action_key = getattr(row, "action", None)
        if isinstance(action_key, str) and action_key.startswith(f"{module_name}."):
            await db.delete(row)
    await db.commit()


async def load_dynamic_module_tool_definitions(
    *,
    session_factory: "async_sessionmaker[AsyncSession]",
) -> list[Any]:
    async with session_factory() as db:
        result = await db.execute(
            select(AraiosModule)
            .where(AraiosModule.system.is_(False))
            .order_by(AraiosModule.order, AraiosModule.name)
        )
        modules = result.scalars().all()
        permission_result = await db.execute(select(AraiosPermission))
        permission_rows = permission_result.scalars().all()

    permissions_by_module: dict[str, dict[str, str]] = {}
    for row in permission_rows:
        action_key = getattr(row, "action", None)
        if not isinstance(action_key, str) or "." not in action_key:
            continue
        module_name, command = action_key.split(".", 1)
        permissions_by_module.setdefault(module_name, {})[command] = (
            str(getattr(row, "level", "") or "").strip().lower()
        )

    tool_defs = []
    for module in modules:
        try:
            definition = build_dynamic_module_definition(
                module,
                permission_levels=permissions_by_module.get(module.name),
                session_factory=session_factory,
            )
            tool_defs.extend(definition.to_tool_definitions(session_factory=session_factory))
        except Exception:
            logger.exception("tool_registry_skip_dynamic_module module=%s", module.name)
    return tool_defs


def _custom_action_commands(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for action in actions:
        action_type = _normalized_action_type(action)
        if action_type in _NON_EXECUTABLE_ACTION_TYPES:
            continue
        commands.append(action)
    return commands


def _normalized_action_type(action: dict[str, Any]) -> str:
    raw = action.get("type", action.get("placement", "standalone"))
    return str(raw or "standalone").strip().lower()


def _normalize_permission_level(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in VALID_PERMISSION_LEVELS:
        return normalized
    return None


def _params_from_action(action: dict[str, Any]) -> list[ParamDefinition]:
    params: list[ParamDefinition] = []
    raw_params = action.get("params")
    if not isinstance(raw_params, list):
        return params
    for entry in raw_params:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if not isinstance(key, str) or not key.strip():
            continue
        params.append(
            ParamDefinition(
                key=key.strip(),
                label=str(entry.get("label") or key.strip()),
                type=str(entry.get("type") or "text"),
                required=bool(entry.get("required")),
            )
        )
    return params


def _build_custom_action_parameters_schema(
    *,
    raw_action: dict[str, Any],
    requires_record: bool,
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    if requires_record:
        properties["record_id"] = dict(_RECORD_ID_PROPERTY)
        required.append("record_id")

    for param in _params_from_action(raw_action):
        prop: dict[str, Any] = {"type": "string"}
        if param.type == "number":
            prop["type"] = "number"
        elif param.type == "textarea":
            prop["type"] = "string"
        properties[param.key] = prop
        if param.required:
            required.append(param.key)

    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def _serialize_record(record: AraiosModuleRecord) -> dict[str, Any]:
    data = dict(record.data or {})
    data["id"] = record.id
    data["module_name"] = record.module_name
    data["created_at"] = record.created_at.isoformat() if record.created_at else None
    data["updated_at"] = record.updated_at.isoformat() if record.updated_at else None
    return data


def _normalize_record_objects(
    payload: dict[str, Any], *, key: str = "records"
) -> list[dict[str, Any]]:
    records = payload.get(key)
    if not isinstance(records, list) or not records:
        raise ValueError(f"'{key}' must be a non-empty array of objects")
    normalized: list[dict[str, Any]] = []
    for index, entry in enumerate(records):
        if not isinstance(entry, dict):
            raise ValueError(f"'{key}[{index}]' must be an object")
        normalized.append(entry)
    return normalized


def _normalize_record_updates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    updates = payload.get("updates")
    if not isinstance(updates, list) or not updates:
        raise ValueError("'updates' must be a non-empty array of objects")
    normalized: list[dict[str, Any]] = []
    for index, entry in enumerate(updates):
        if not isinstance(entry, dict):
            raise ValueError(f"'updates[{index}]' must be an object")
        record_id = entry.get("record_id")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError(f"'updates[{index}].record_id' must be a non-empty string")
        data = entry.get("data")
        if not isinstance(data, dict):
            raise ValueError(f"'updates[{index}].data' must be an object")
        normalized.append({"record_id": record_id.strip(), "data": data})
    return normalized


def _normalize_record_ids(payload: dict[str, Any]) -> list[str]:
    record_ids = payload.get("record_ids")
    if not isinstance(record_ids, list) or not record_ids:
        raise ValueError("'record_ids' must be a non-empty array of strings")
    normalized: list[str] = []
    for index, record_id in enumerate(record_ids):
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError(f"'record_ids[{index}]' must be a non-empty string")
        normalized.append(record_id.strip())
    return normalized


async def _require_module(
    session_factory: "async_sessionmaker[AsyncSession]",
    module_name: str,
) -> AraiosModule:
    async with session_factory() as db:
        result = await db.execute(select(AraiosModule).where(AraiosModule.name == module_name))
        module = result.scalars().first()
    if module is None:
        raise ValueError(f"Module '{module_name}' not found")
    return module


async def _load_secrets(
    session_factory: "async_sessionmaker[AsyncSession]",
    module_name: str,
) -> dict[str, str]:
    async with session_factory() as db:
        result = await db.execute(
            select(AraiosModuleSecret).where(AraiosModuleSecret.module_name == module_name)
        )
        secrets = result.scalars().all()
        valid: list[AraiosModuleSecret] = []
        deleted = False
        for secret in secrets:
            if is_invalid_secret(secret.value):
                await db.delete(secret)
                deleted = True
                continue
            valid.append(secret)
        if deleted:
            await db.commit()
        secrets = valid
    return {secret.key: secret.value for secret in secrets}


def _check_required_secrets(module: AraiosModule, secrets: dict[str, str]) -> None:
    missing = [
        secret["key"]
        for secret in (module.secrets or [])
        if isinstance(secret, dict)
        and secret.get("required")
        and not secrets.get(secret.get("key"))
    ]
    if missing:
        raise ValueError(f"Module '{module.name}' is missing required secrets: {missing}")


def _make_list_records_handler(
    module_name: str, session_factory: "async_sessionmaker[AsyncSession]"
):
    async def _handler(_payload: dict[str, Any]) -> dict[str, Any]:
        async with session_factory() as db:
            result = await db.execute(
                select(AraiosModuleRecord)
                .where(AraiosModuleRecord.module_name == module_name)
                .order_by(AraiosModuleRecord.created_at.desc())
            )
            records = result.scalars().all()
        return {"records": [_serialize_record(record) for record in records], "count": len(records)}

    return _handler


def _make_get_record_handler(module_name: str, session_factory: "async_sessionmaker[AsyncSession]"):
    async def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        record_id = payload.get("record_id")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError("'record_id' is required")
        async with session_factory() as db:
            result = await db.execute(
                select(AraiosModuleRecord).where(
                    AraiosModuleRecord.module_name == module_name,
                    AraiosModuleRecord.id == record_id.strip(),
                )
            )
            record = result.scalars().first()
        if record is None:
            raise ValueError(f"Record '{record_id}' not found")
        return _serialize_record(record)

    return _handler


def _make_create_records_handler(
    module_name: str, session_factory: "async_sessionmaker[AsyncSession]"
):
    async def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        records_data = _normalize_record_objects(payload)
        async with session_factory() as db:
            records: list[AraiosModuleRecord] = []
            for data in records_data:
                record = AraiosModuleRecord(id=araios_gen_id(), module_name=module_name, data=data)
                db.add(record)
                records.append(record)
            await db.commit()
            for record in records:
                await db.refresh(record)
        return {"records": [_serialize_record(record) for record in records], "count": len(records)}

    return _handler


def _make_update_records_handler(
    module_name: str, session_factory: "async_sessionmaker[AsyncSession]"
):
    async def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        updates = _normalize_record_updates(payload)
        record_ids = [entry["record_id"] for entry in updates]
        async with session_factory() as db:
            result = await db.execute(
                select(AraiosModuleRecord).where(
                    AraiosModuleRecord.module_name == module_name,
                    AraiosModuleRecord.id.in_(record_ids),
                )
            )
            records_by_id = {record.id: record for record in result.scalars().all()}
            missing = [record_id for record_id in record_ids if record_id not in records_by_id]
            if missing:
                raise ValueError(f"Record(s) not found: {', '.join(missing)}")
            updated_records: list[AraiosModuleRecord] = []
            for entry in updates:
                record = records_by_id[entry["record_id"]]
                merged = dict(record.data or {})
                merged.update(entry["data"])
                record.data = merged
                updated_records.append(record)
            await db.commit()
            for record in updated_records:
                await db.refresh(record)
        return {
            "records": [_serialize_record(record) for record in updated_records],
            "count": len(updated_records),
        }

    return _handler


def _make_delete_records_handler(
    module_name: str, session_factory: "async_sessionmaker[AsyncSession]"
):
    async def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        record_ids = _normalize_record_ids(payload)
        async with session_factory() as db:
            result = await db.execute(
                select(AraiosModuleRecord).where(
                    AraiosModuleRecord.module_name == module_name,
                    AraiosModuleRecord.id.in_(record_ids),
                )
            )
            records_by_id = {record.id: record for record in result.scalars().all()}
            missing = [record_id for record_id in record_ids if record_id not in records_by_id]
            if missing:
                raise ValueError(f"Record(s) not found: {', '.join(missing)}")
            for record_id in record_ids:
                await db.delete(records_by_id[record_id])
            await db.commit()
        return {"ok": True, "record_ids": record_ids, "count": len(record_ids)}

    return _handler


def _make_get_page_handler(module_name: str, session_factory: "async_sessionmaker[AsyncSession]"):
    async def _handler(_payload: dict[str, Any]) -> dict[str, Any]:
        module = await _require_module(session_factory, module_name)
        return {
            "module": module_name,
            "page_title": module.page_title,
            "page_content": module.page_content or "",
        }

    return _handler


def _make_edit_page_handler(module_name: str, session_factory: "async_sessionmaker[AsyncSession]"):
    async def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        if "page_title" not in payload and "page_content" not in payload:
            raise ValueError("At least one of 'page_title' or 'page_content' is required")
        async with session_factory() as db:
            result = await db.execute(select(AraiosModule).where(AraiosModule.name == module_name))
            module = result.scalars().first()
            if module is None:
                raise ValueError(f"Module '{module_name}' not found")
            if "page_title" in payload:
                module.page_title = payload.get("page_title")
            if "page_content" in payload:
                page_content = payload.get("page_content")
                if page_content is not None and not isinstance(page_content, str):
                    raise ValueError("'page_content' must be a string")
                module.page_content = page_content
            await db.commit()
            await db.refresh(module)
        return {
            "ok": True,
            "module": module_name,
            "page_title": module.page_title,
            "page_content": module.page_content or "",
        }

    return _handler


def _make_custom_action_handler(
    *,
    module_name: str,
    action: ActionDefinition,
    raw_action: dict[str, Any],
    session_factory: "async_sessionmaker[AsyncSession]",
):
    param_keys = [param.key for param in (_params_from_action(raw_action) or [])]
    action_type = _normalized_action_type(raw_action)
    requires_record = action_type in _RECORD_ACTION_TYPES
    code = str(raw_action.get("code") or "").strip()

    async def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        if not code:
            raise ValueError(f"Action '{action.id}' has no executable code")
        module = await _require_module(session_factory, module_name)
        secrets = await _load_secrets(session_factory, module_name)
        _check_required_secrets(module, secrets)
        context: dict[str, Any] = {
            "params": {key: payload.get(key) for key in param_keys},
            "secrets": secrets,
        }
        if requires_record:
            record_id = payload.get("record_id")
            if not isinstance(record_id, str) or not record_id.strip():
                raise ValueError("'record_id' is required")
            async with session_factory() as db:
                result = await db.execute(
                    select(AraiosModuleRecord).where(
                        AraiosModuleRecord.module_name == module_name,
                        AraiosModuleRecord.id == record_id.strip(),
                    )
                )
                record = result.scalars().first()
            if record is None:
                raise ValueError(f"Record '{record_id}' not found")
            context["record"] = _serialize_record(record)
        return await execute_action(code, context)

    return _handler
