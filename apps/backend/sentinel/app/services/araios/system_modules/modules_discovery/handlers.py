"""Native module: modules_discovery — manage araiOS modules, records, and actions."""
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
    AraiosPermission,
    araios_gen_id,
)
from app.services.araios.executor import execute_action
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolApprovalEvaluation, ToolApprovalRequirement

logger = logging.getLogger(__name__)
ALLOWED_MODULE_DISCOVERY_COMMANDS = (
    "list_modules",
    "get_module",
    "create_module",
    "delete_module",
    "list_records",
    "get_record",
    "create_record",
    "update_record",
    "delete_record",
    "run_action",
)


# ── Helpers ──


def _serialize_record(r: AraiosModuleRecord) -> dict[str, Any]:
    d = dict(r.data or {})
    d["id"] = r.id
    d["module_name"] = r.module_name
    d["created_at"] = r.created_at.isoformat() if r.created_at else None
    d["updated_at"] = r.updated_at.isoformat() if r.updated_at else None
    return d


async def _seed_module_permissions(db: AsyncSession, name: str) -> None:
    """Create default permission entries for a newly registered module."""
    result = await db.execute(
        select(AraiosModule).where(AraiosModule.name == name)
    )
    mod = result.scalars().first()
    defaults = [
        (f"{name}.list", "allow"),
        (f"{name}.create", "allow"),
        (f"{name}.update", "allow"),
        (f"{name}.delete", "approval"),
    ]
    if mod:
        for a in (mod.actions or []):
            if isinstance(a, dict) and a.get("id"):
                defaults.append((f"{name}.{a['id']}", "allow"))
    existing_result = await db.execute(
        select(AraiosPermission).where(
            AraiosPermission.action.in_([k for k, _ in defaults])
        )
    )
    existing = {p.action for p in existing_result.scalars().all()}
    for action, level in defaults:
        if action not in existing:
            db.add(AraiosPermission(action=action, level=level))
    await db.commit()


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
                    "pinned": m.pinned,
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
            "pinned": mod.pinned,
            "system": mod.system,
        }


async def handle_create_module(payload: dict[str, Any]) -> dict[str, Any]:
    name = (payload.get("name") or "").strip().lower()
    if not name:
        raise ValueError("'name' is required for create_module")
    async with AsyncSessionLocal() as db:
        existing = await db.execute(
            select(AraiosModule).where(AraiosModule.name == name)
        )
        if existing.scalars().first():
            raise ValueError(f"Module '{name}' already exists")
        mod = AraiosModule(
            name=name,
            label=payload.get("label", name.title()),
            description=payload.get("description", ""),
            icon=payload.get("icon", "box"),
            fields=payload.get("fields", []),
            fields_config=payload.get("fields_config", {}),
            actions=payload.get("actions", []),
            secrets=payload.get("secrets", []),
            page_title=payload.get("page_title"),
            order=payload.get("order", 100),
        )
        db.add(mod)
        await db.commit()
        await _seed_module_permissions(db, name)
        return {"ok": True, "module": name, "message": f"Module '{name}' created"}


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


def _delete_module_approval_evaluator(payload: dict[str, Any]) -> ToolApprovalEvaluation:
    module_name = str(payload.get("name") or "").strip().lower()
    if not module_name:
        raise ValueError("'name' is required for delete_module")
    return ToolApprovalEvaluation.require(
        ToolApprovalRequirement(
            action="modules.delete",
            description=f"Delete module '{module_name}' and all of its records and secrets.",
        )
    )


def _run_action_approval_evaluator(payload: dict[str, Any]) -> ToolApprovalEvaluation:
    module_name = str(payload.get("module") or "").strip().lower()
    action_id = str(payload.get("action_id") or "").strip()
    if not module_name or not action_id:
        raise ValueError("'module' and 'action_id' are required for run_action approval")
    record_id = str(payload.get("record_id") or "").strip()
    description = f"Execute module action '{module_name}.{action_id}'"
    if record_id:
        description += f" for record '{record_id}'"
    return ToolApprovalEvaluation.require(
        ToolApprovalRequirement(
            action=f"{module_name}.{action_id}",
            description=description + ".",
        )
    )
def _modules_discovery_command(payload: dict[str, Any]) -> str:
    raw = payload.get("command")
    if not isinstance(raw, str) or not raw.strip():
        raise ToolValidationError("Field 'command' must be a non-empty string")
    normalized = raw.strip().lower()
    if normalized not in ALLOWED_MODULE_DISCOVERY_COMMANDS:
        raise ToolValidationError(
            "Field 'command' must be one of: " + ", ".join(ALLOWED_MODULE_DISCOVERY_COMMANDS)
        )
    return normalized


def _modules_discovery_approval_evaluator(payload: dict[str, Any]) -> ToolApprovalEvaluation:
    command = _modules_discovery_command(payload)
    if command == "delete_module":
        return _delete_module_approval_evaluator(payload)
    if command == "run_action":
        return _run_action_approval_evaluator(payload)
    return ToolApprovalEvaluation.allow()


async def handle_run(payload: dict[str, Any]) -> dict[str, Any]:
    command = _modules_discovery_command(payload)
    if command == "list_modules":
        return await handle_list_modules(payload)
    if command == "get_module":
        return await handle_get_module(payload)
    if command == "create_module":
        return await handle_create_module(payload)
    if command == "delete_module":
        return await handle_delete_module(payload)
    if command == "list_records":
        return await handle_list_records(payload)
    if command == "get_record":
        return await handle_get_record(payload)
    if command == "create_record":
        return await handle_create_record(payload)
    if command == "update_record":
        return await handle_update_record(payload)
    if command == "delete_record":
        return await handle_delete_record(payload)
    return await handle_run_action(payload)
