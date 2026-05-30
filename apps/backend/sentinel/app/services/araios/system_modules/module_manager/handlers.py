"""Native module: module_manager — manage dynamic modules, records, and actions."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import AsyncSessionLocal, _current_session_factory
from app.models.araios import (
    AraiosModule,
    AraiosModuleRecord,
    AraiosModuleSecret,
    araios_gen_id,
)
from app.services.araios.dynamic_modules import (
    delete_dynamic_module_permissions,
    normalize_dynamic_module_actions,
    sync_dynamic_module_permissions,
)
from app.services.araios.module_updates import apply_module_updates, fold_ops_into_delta
from app.schemas.modules import EditModuleRequest, ModuleCreateRequest
from app.services.araios.executor import execute_action
from app.services.araios.runtime_services import get_app_state
from app.services.secrets import is_invalid_secret
from app.services.tools.runtime_registry import rebuild_runtime_registry

logger = logging.getLogger(__name__)


# ── Helpers ──


def _serialize_record(r: AraiosModuleRecord) -> dict[str, Any]:
    d = dict(r.data or {})
    d["id"] = r.id
    d["module_name"] = r.module_name
    d["created_at"] = r.created_at.isoformat() if r.created_at else None
    d["updated_at"] = r.updated_at.isoformat() if r.updated_at else None
    return d


def _normalize_records_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("'records' must be a non-empty array of objects")
    normalized: list[dict[str, Any]] = []
    for index, entry in enumerate(records):
        if not isinstance(entry, dict):
            raise ValueError(f"'records[{index}]' must be an object")
        normalized.append(entry)
    return normalized


def _normalize_updates_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
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


def _normalize_record_ids_payload(payload: dict[str, Any]) -> list[str]:
    record_ids = payload.get("record_ids")
    if not isinstance(record_ids, list) or not record_ids:
        raise ValueError("'record_ids' must be a non-empty array of strings")
    normalized: list[str] = []
    for index, record_id in enumerate(record_ids):
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError(f"'record_ids[{index}]' must be a non-empty string")
        normalized.append(record_id.strip())
    return normalized


async def _require_module_exists(
    db: AsyncSession,
    module_name: str,
) -> AraiosModule:
    """Return the module or raise."""
    result = await db.execute(select(AraiosModule).where(AraiosModule.name == module_name))
    mod = result.scalars().first()
    if not mod:
        raise ValueError(f"Module '{module_name}' not found")
    return mod


def _active_session_factory(app_state: Any) -> Any:
    return (
        _current_session_factory.get()
        or getattr(app_state, "db_session_factory", None)
        or AsyncSessionLocal
    )


async def _refresh_current_runtime_after_module_change(app_state: Any | None) -> None:
    if app_state is None:
        return
    session_factory = _active_session_factory(app_state)
    from app.services.instance_runtime_context import instance_runtime_context_registry

    context = instance_runtime_context_registry.find_by_session_factory(session_factory)
    if context is not None:
        await instance_runtime_context_registry.rebuild_context(
            app_state=app_state,
            context=context,
        )
        return

    if getattr(app_state, "tool_registry", None) is not None:
        await rebuild_runtime_registry(app_state=app_state, session_factory=session_factory)


# ---------------------------------------------------------------------------
# Handler functions (module-level)
# ---------------------------------------------------------------------------

# ── Module handlers ──


async def handle_list_modules(payload: dict[str, Any]) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AraiosModule).order_by(AraiosModule.order, AraiosModule.name)
        )
        mods = result.scalars().all()
        return {
            "modules": [
                {
                    "name": m.name,
                    "label": m.label,
                    "description": m.description or "",
                    "icon": m.icon,
                    "fields": m.fields or [],
                    "fields_config": m.fields_config or {},
                    "actions": m.actions or [],
                    "page_title": m.page_title,
                    "system": m.system,
                }
                for m in mods
            ]
        }


async def handle_get_module(payload: dict[str, Any]) -> dict[str, Any]:
    name = (payload.get("name") or "").strip().lower()
    if not name:
        raise ValueError("'name' is required for get_module")
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(AraiosModule).where(AraiosModule.name == name))
        mod = result.scalars().first()
        if not mod:
            raise ValueError(f"Module '{name}' not found")
        return {
            "name": mod.name,
            "label": mod.label,
            "description": mod.description or "",
            "icon": mod.icon,
            "fields": mod.fields or [],
            "fields_config": mod.fields_config or {},
            "actions": mod.actions or [],
            "secrets": [
                {
                    "key": s["key"],
                    "label": s.get("label", s["key"]),
                    "required": s.get("required", False),
                }
                for s in (mod.secrets or [])
            ],
            "page_title": mod.page_title,
            "system": mod.system,
        }


def _validate_module_create_payload(payload: dict[str, Any]) -> ModuleCreateRequest:
    try:
        return ModuleCreateRequest.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


async def handle_create_module(payload: dict[str, Any]) -> dict[str, Any]:
    request = _validate_module_create_payload(payload)
    name = request.name
    module_values = request.module_values()
    actions = normalize_dynamic_module_actions(list(module_values.get("actions") or []))
    permissions = request.permissions
    app_state = get_app_state()
    if app_state is not None:
        registry = getattr(app_state, "tool_registry", None)
        if registry is not None and registry.get(name) is not None:
            raise ValueError(f"Module '{name}' conflicts with an existing tool")
    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(AraiosModule).where(AraiosModule.name == name))
        if existing.scalars().first():
            raise ValueError(f"Module '{name}' already exists")
        mod = AraiosModule(
            name=name,
            label=module_values["label"],
            description=module_values.get("description", ""),
            icon=module_values.get("icon", "box"),
            fields=module_values.get("fields", []),
            fields_config=module_values.get("fields_config", {}),
            actions=actions,
            secrets=module_values.get("secrets", []),
            page_title=module_values.get("page_title"),
            page_content=module_values.get("page_content"),
            system=False,
            order=module_values.get("order", 100),
        )
        db.add(mod)
        await db.commit()
        permission_levels = await sync_dynamic_module_permissions(
            db,
            module_name=name,
            actions=actions,
            permissions=permissions,
        )
    await _refresh_current_runtime_after_module_change(app_state)
    return {
        "ok": True,
        "module": name,
        "permissions": permission_levels,
        "message": f"Module '{name}' created",
    }


async def handle_delete_module(payload: dict[str, Any]) -> dict[str, Any]:
    name = (payload.get("name") or "").strip().lower()
    if not name:
        raise ValueError("'name' is required for delete_module")
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(AraiosModule).where(AraiosModule.name == name))
        mod = result.scalars().first()
        if not mod:
            raise ValueError(f"Module '{name}' not found")
        await db.execute(delete(AraiosModuleRecord).where(AraiosModuleRecord.module_name == name))
        await db.execute(delete(AraiosModuleSecret).where(AraiosModuleSecret.module_name == name))
        await db.delete(mod)
        await db.commit()
        await delete_dynamic_module_permissions(db, module_name=name)
    app_state = get_app_state()
    await _refresh_current_runtime_after_module_change(app_state)
    return {"ok": True, "message": f"Module '{name}' deleted"}


def _validate_module_edit_payload(payload: dict[str, Any]) -> EditModuleRequest:
    try:
        return EditModuleRequest.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


async def _apply_record_data_migration(
    db: AsyncSession,
    *,
    module_name: str,
    renames: list[tuple[str, str]],
    purge_keys: set[str],
) -> None:
    if not renames and not purge_keys:
        return
    result = await db.execute(
        select(AraiosModuleRecord).where(AraiosModuleRecord.module_name == module_name)
    )
    for record in result.scalars().all():
        data = dict(record.data or {})
        changed = False
        for old_key, new_key in renames:
            if old_key in data:
                data[new_key] = data.pop(old_key)
                changed = True
        for key in purge_keys:
            if key in data:
                data.pop(key)
                changed = True
        if changed:
            record.data = data  # reassign so SQLAlchemy flags the JSON column dirty


async def handle_edit_module(payload: dict[str, Any]) -> dict[str, Any]:
    request = _validate_module_edit_payload(payload)
    name = request.name
    app_state = get_app_state()
    async with AsyncSessionLocal() as db:
        mod = await _require_module_exists(db, name)
        if mod.system:
            raise ValueError(f"Module '{name}' is a system module and cannot be edited")
        delta = fold_ops_into_delta(mod, request)
        apply_module_updates(mod, delta.updates)
        if delta.final_actions is not None:
            mod.actions = delta.final_actions
        await _apply_record_data_migration(
            db,
            module_name=name,
            renames=delta.record_renames,
            purge_keys=delta.record_purge_keys,
        )
        for secret_key in delta.secret_purge_keys:
            await db.execute(
                delete(AraiosModuleSecret).where(
                    AraiosModuleSecret.module_name == name,
                    AraiosModuleSecret.key == secret_key,
                )
            )
        await db.commit()
        await db.refresh(mod)
        permission_levels = await sync_dynamic_module_permissions(
            db,
            module_name=name,
            actions=normalize_dynamic_module_actions(list(mod.actions or [])),
            permissions=delta.permissions,
        )
    await _refresh_current_runtime_after_module_change(app_state)
    return {
        "ok": True,
        "module": name,
        "applied_ops": len(request.ops),
        "permissions": permission_levels,
        "message": f"Module '{name}' updated ({len(request.ops)} op(s) applied)",
    }


# ── Record handlers ──


async def handle_list_records(payload: dict[str, Any]) -> dict[str, Any]:
    module_name = (payload.get("module") or "").strip().lower()
    if not module_name:
        raise ValueError("'module' is required")
    async with AsyncSessionLocal() as db:
        await _require_module_exists(db, module_name)
        result = await db.execute(
            select(AraiosModuleRecord)
            .where(AraiosModuleRecord.module_name == module_name)
            .order_by(AraiosModuleRecord.created_at.desc())
        )
        records = result.scalars().all()
        return {
            "records": [_serialize_record(r) for r in records],
            "count": len(records),
        }


async def handle_get_record(payload: dict[str, Any]) -> dict[str, Any]:
    module_name = (payload.get("module") or "").strip().lower()
    record_id = payload.get("record_id")
    if not module_name:
        raise ValueError("'module' is required")
    if not record_id:
        raise ValueError("'record_id' is required for get_record")
    async with AsyncSessionLocal() as db:
        await _require_module_exists(db, module_name)
        result = await db.execute(
            select(AraiosModuleRecord).where(
                AraiosModuleRecord.module_name == module_name,
                AraiosModuleRecord.id == record_id,
            )
        )
        rec = result.scalars().first()
        if not rec:
            raise ValueError(f"Record '{record_id}' not found")
        return _serialize_record(rec)


async def handle_create_records(payload: dict[str, Any]) -> dict[str, Any]:
    module_name = (payload.get("module") or "").strip().lower()
    if not module_name:
        raise ValueError("'module' is required")
    records_data = _normalize_records_payload(payload)
    async with AsyncSessionLocal() as db:
        await _require_module_exists(db, module_name)
        records: list[AraiosModuleRecord] = []
        for data in records_data:
            rec = AraiosModuleRecord(
                id=araios_gen_id(),
                module_name=module_name,
                data=data,
            )
            db.add(rec)
            records.append(rec)
        await db.commit()
        for record in records:
            await db.refresh(record)
        return {
            "records": [_serialize_record(record) for record in records],
            "count": len(records),
        }


async def handle_update_records(payload: dict[str, Any]) -> dict[str, Any]:
    module_name = (payload.get("module") or "").strip().lower()
    if not module_name:
        raise ValueError("'module' is required")
    updates = _normalize_updates_payload(payload)
    update_ids = [entry["record_id"] for entry in updates]
    async with AsyncSessionLocal() as db:
        await _require_module_exists(db, module_name)
        result = await db.execute(
            select(AraiosModuleRecord).where(
                AraiosModuleRecord.module_name == module_name,
                AraiosModuleRecord.id.in_(update_ids),
            )
        )
        records_by_id = {record.id: record for record in result.scalars().all()}
        missing = [record_id for record_id in update_ids if record_id not in records_by_id]
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


async def handle_delete_records(payload: dict[str, Any]) -> dict[str, Any]:
    module_name = (payload.get("module") or "").strip().lower()
    if not module_name:
        raise ValueError("'module' is required")
    record_ids = _normalize_record_ids_payload(payload)
    async with AsyncSessionLocal() as db:
        await _require_module_exists(db, module_name)
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
        return {
            "ok": True,
            "record_ids": record_ids,
            "count": len(record_ids),
            "message": f"Deleted {len(record_ids)} record(s)",
        }


# ── Action handler ──


async def handle_run_action(payload: dict[str, Any]) -> dict[str, Any]:
    module_name = (payload.get("module") or "").strip().lower()
    action_id = (payload.get("action_id") or "").strip()
    record_id = payload.get("record_id")
    params = payload.get("params", {})

    if not module_name:
        raise ValueError("'module' is required")
    if not action_id:
        raise ValueError("'action_id' is required")

    async with AsyncSessionLocal() as db:
        mod = await _require_module_exists(db, module_name)

        action_def = next(
            (a for a in (mod.actions or []) if a.get("id") == action_id),
            None,
        )
        if not action_def:
            raise ValueError(f"Action '{action_id}' not found in module '{module_name}'")
        code = action_def.get("code", "")
        if not code:
            raise ValueError("Action has no executable code")

        # Resolve secrets
        sec_result = await db.execute(
            select(AraiosModuleSecret).where(AraiosModuleSecret.module_name == module_name)
        )
        secret_rows = sec_result.scalars().all()
        secrets: dict[str, str] = {}
        deleted_secret = False
        for secret in secret_rows:
            if is_invalid_secret(secret.value):
                await db.delete(secret)
                deleted_secret = True
                continue
            secrets[secret.key] = secret.value
        if deleted_secret:
            await db.commit()

        # Check required secrets
        missing = [
            s["key"] for s in (mod.secrets or []) if s.get("required") and not secrets.get(s["key"])
        ]
        if missing:
            raise ValueError(f"Module '{module_name}' is missing required secrets: {missing}")

        # Build context
        context: dict[str, Any] = {"params": params, "secrets": secrets}
        if record_id:
            rec_result = await db.execute(
                select(AraiosModuleRecord).where(
                    AraiosModuleRecord.module_name == module_name,
                    AraiosModuleRecord.id == record_id,
                )
            )
            rec = rec_result.scalars().first()
            if not rec:
                raise ValueError(f"Record '{record_id}' not found")
            context["record"] = _serialize_record(rec)

        return await execute_action(code, context)
