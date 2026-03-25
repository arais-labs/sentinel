"""Native module: module_manager — manage araiOS modules, records, and actions."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import AsyncSessionLocal
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
from app.services.araios.executor import execute_action
from app.services.araios.runtime_services import get_app_state
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


async def _require_module_exists(
    db: AsyncSession, module_name: str,
) -> AraiosModule:
    """Return the module or raise."""
    result = await db.execute(
        select(AraiosModule).where(AraiosModule.name == module_name)
    )
    mod = result.scalars().first()
    if not mod:
        raise ValueError(f"Module '{module_name}' not found")
    return mod


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
        result = await db.execute(
            select(AraiosModule).where(AraiosModule.name == name)
        )
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


_FIELD_EXAMPLE = '{"key": "email", "label": "Email", "type": "email"}'
_VALID_FIELD_TYPES = {
    "text", "textarea", "email", "url", "number", "date",
    "select", "badge", "tags", "readonly",
}


def _validate_fields(fields: Any) -> list[dict]:
    if fields is None:
        return []
    if not isinstance(fields, list):
        raise ValueError(f"'fields' must be an array. Example item: {_FIELD_EXAMPLE}")
    validated = []
    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            raise ValueError(
                f"Field at index {i} must be an object, got {type(f).__name__!r}. "
                f"Each field requires 'key' and 'label' strings. Example: {_FIELD_EXAMPLE}"
            )
        if not isinstance(f.get("key"), str) or not f["key"].strip():
            raise ValueError(f"Field at index {i} is missing required 'key' (non-empty string)")
        if not isinstance(f.get("label"), str) or not f["label"].strip():
            raise ValueError(f"Field at index {i} (key={f.get('key')!r}) is missing required 'label' (non-empty string)")
        field_type = f.get("type", "text")
        if field_type not in _VALID_FIELD_TYPES:
            raise ValueError(
                f"Field {f['key']!r} has invalid type {field_type!r}. "
                f"Valid types: {sorted(_VALID_FIELD_TYPES)}"
            )
        validated.append(f)
    return validated


async def handle_create_module(payload: dict[str, Any]) -> dict[str, Any]:
    name = (payload.get("name") or "").strip().lower()
    if not name:
        raise ValueError("'name' is required for create_module")
    actions = normalize_dynamic_module_actions(list(payload.get("actions") or []))
    permissions = payload.get("permissions", {})
    if permissions is not None and not isinstance(permissions, dict):
        raise ValueError("'permissions' must be an object")
    app_state = get_app_state()
    if app_state is not None:
        registry = getattr(app_state, "tool_registry", None)
        if registry is not None and registry.get(name) is not None:
            raise ValueError(f"Module '{name}' conflicts with an existing tool")
    async with AsyncSessionLocal() as db:
        existing = await db.execute(
            select(AraiosModule).where(AraiosModule.name == name)
        )
        if existing.scalars().first():
            raise ValueError(f"Module '{name}' already exists")
        fields = _validate_fields(payload.get("fields"))
        mod = AraiosModule(
            name=name,
            label=payload.get("label", name.title()),
            description=payload.get("description", ""),
            icon=payload.get("icon", "box"),
            fields=fields,
            fields_config=payload.get("fields_config", {}),
            actions=actions,
            secrets=payload.get("secrets", []),
            page_title=payload.get("page_title"),
            page_content=payload.get("page_content"),
            system=False,
            order=payload.get("order", 100),
        )
        db.add(mod)
        await db.commit()
        permission_levels = await sync_dynamic_module_permissions(
            db,
            module_name=name,
            actions=actions,
            permissions=permissions,
        )
    if app_state is not None:
        session_factory = getattr(app_state, "db_session_factory", AsyncSessionLocal)
        await rebuild_runtime_registry(app_state=app_state, session_factory=session_factory)
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
        result = await db.execute(
            select(AraiosModule).where(AraiosModule.name == name)
        )
        mod = result.scalars().first()
        if not mod:
            raise ValueError(f"Module '{name}' not found")
        await db.execute(
            delete(AraiosModuleRecord).where(AraiosModuleRecord.module_name == name)
        )
        await db.execute(
            delete(AraiosModuleSecret).where(AraiosModuleSecret.module_name == name)
        )
        await db.delete(mod)
        await db.commit()
        await delete_dynamic_module_permissions(db, module_name=name)
    app_state = get_app_state()
    if app_state is not None:
        session_factory = getattr(app_state, "db_session_factory", AsyncSessionLocal)
        await rebuild_runtime_registry(app_state=app_state, session_factory=session_factory)
    return {"ok": True, "message": f"Module '{name}' deleted"}


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


async def handle_create_record(payload: dict[str, Any]) -> dict[str, Any]:
    module_name = (payload.get("module") or "").strip().lower()
    if not module_name:
        raise ValueError("'module' is required")
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise ValueError("'data' must be an object")
    async with AsyncSessionLocal() as db:
        await _require_module_exists(db, module_name)
        rec = AraiosModuleRecord(
            id=araios_gen_id(), module_name=module_name, data=data,
        )
        db.add(rec)
        await db.commit()
        await db.refresh(rec)
        return _serialize_record(rec)


async def handle_update_record(payload: dict[str, Any]) -> dict[str, Any]:
    module_name = (payload.get("module") or "").strip().lower()
    record_id = payload.get("record_id")
    if not module_name:
        raise ValueError("'module' is required")
    if not record_id:
        raise ValueError("'record_id' is required for update_record")
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise ValueError("'data' must be an object")
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
        merged = dict(rec.data or {})
        merged.update(data)
        rec.data = merged
        await db.commit()
        await db.refresh(rec)
        return _serialize_record(rec)


async def handle_delete_record(payload: dict[str, Any]) -> dict[str, Any]:
    module_name = (payload.get("module") or "").strip().lower()
    record_id = payload.get("record_id")
    if not module_name:
        raise ValueError("'module' is required")
    if not record_id:
        raise ValueError("'record_id' is required for delete_record")
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
        await db.delete(rec)
        await db.commit()
        return {"ok": True, "message": f"Record '{record_id}' deleted"}


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
            (a for a in (mod.actions or []) if a.get("id") == action_id), None,
        )
        if not action_def:
            raise ValueError(f"Action '{action_id}' not found in module '{module_name}'")
        code = action_def.get("code", "")
        if not code:
            raise ValueError("Action has no executable code")

        # Resolve secrets
        sec_result = await db.execute(
            select(AraiosModuleSecret).where(
                AraiosModuleSecret.module_name == module_name
            )
        )
        secrets = {s.key: s.value for s in sec_result.scalars().all()}

        # Check required secrets
        missing = [
            s["key"]
            for s in (mod.secrets or [])
            if s.get("required") and not secrets.get(s["key"])
        ]
        if missing:
            raise ValueError(
                f"Module '{module_name}' is missing required secrets: {missing}"
            )

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
