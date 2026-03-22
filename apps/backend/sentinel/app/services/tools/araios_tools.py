"""In-process AraiOS tools — direct DB access, Sentinel approval gate.

Replaces the old araios_api HTTP tool. Each operation is a proper Sentinel
tool with ToolApprovalGate for permission-gated actions.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.araios import (
    AraiosModule,
    AraiosModuleRecord,
    AraiosModuleSecret,
    AraiosPermission,
    araios_gen_id,
)
from app.services.araios.executor import execute_action
from app.services.tools.approval_waiters import build_tool_db_approval_waiter
from app.services.tools.executor import ToolExecutionError, ToolValidationError
from app.services.tools.registry import (
    ToolApprovalDecision,
    ToolApprovalEvaluation,
    ToolApprovalGate,
    ToolApprovalMode,
    ToolApprovalRequirement,
    ToolDefinition,
)

logger = logging.getLogger(__name__)

_APPROVAL_TIMEOUT = 600


# ── Permission evaluator ──


def _make_araios_evaluator(
    default_action: str,
    session_factory: async_sessionmaker[AsyncSession],
):
    """Return an async evaluator that checks the AraiOS permissions table."""

    async def _evaluator(payload: dict[str, Any]) -> ToolApprovalEvaluation:
        # Derive the specific AraiOS permission action from the payload
        operation = payload.get("operation", "list")
        name = (payload.get("name") or payload.get("module") or "").strip().lower()
        action_id = payload.get("action_id", "")
        if default_action == "modules":
            action = f"modules.{operation}"
        elif default_action == "action" and name and action_id:
            action = f"{name}.{action_id}"
        else:
            action = payload.get("_araios_action", default_action)

        async with session_factory() as db:
            perm_level = await _check_araios_permission(db, action)
        if perm_level == "allow":
            return ToolApprovalEvaluation.allow()
        if perm_level == "deny":
            return ToolApprovalEvaluation(
                decision=ToolApprovalDecision.DENY,
                reason=f"AraiOS permission denied for {action}",
            )
        # approval
        return ToolApprovalEvaluation.require(
            ToolApprovalRequirement(
                action=f"araios:{action}",
                description=f"AraiOS action requires approval: {action}",
                timeout_seconds=_APPROVAL_TIMEOUT,
                metadata={"araios_action": action},
            )
        )

    return _evaluator


async def _check_araios_permission(
    db: AsyncSession,
    action: str,
) -> str:
    """Return 'allow', 'approval', or 'deny' for an AraiOS action."""
    result = await db.execute(
        select(AraiosPermission).where(AraiosPermission.action == action)
    )
    row = result.scalars().first()
    return row.level if row else "allow"


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


# ── Tool: araios_modules ──


def araios_modules_tool(
    *, session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    """CRUD for AraiOS modules — list, get, create, update, delete."""

    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        operation = payload.get("operation", "list")
        name = payload.get("name", "").strip().lower() if payload.get("name") else None

        async with session_factory() as db:
            if operation == "list":
                result = await db.execute(
                    select(AraiosModule).order_by(AraiosModule.order, AraiosModule.name)
                )
                mods = result.scalars().all()
                return {
                    "modules": [
                        {
                            "name": m.name, "label": m.label,
                            "description": m.description or "",
                            "icon": m.icon,
                            "fields": m.fields or [],
                            "fields_config": m.fields_config or {},
                            "actions": m.actions or [],
                            "page_title": m.page_title,
                        }
                        for m in mods
                    ]
                }

            if operation == "get":
                if not name:
                    raise ToolValidationError("'name' is required for get operation")
                result = await db.execute(
                    select(AraiosModule).where(AraiosModule.name == name)
                )
                mod = result.scalars().first()
                if not mod:
                    raise ToolExecutionError(f"Module '{name}' not found")
                return {
                    "name": mod.name, "label": mod.label,
                    "description": mod.description or "",
                    "icon": mod.icon,
                    "fields": mod.fields or [],
                    "fields_config": mod.fields_config or {},
                    "actions": mod.actions or [],
                    "secrets": [{"key": s["key"], "label": s.get("label", s["key"]), "required": s.get("required", False)} for s in (mod.secrets or [])],
                    "page_title": mod.page_title,
                }

            if operation == "create":
                if not name:
                    raise ToolValidationError("'name' is required for create operation")
                existing = await db.execute(
                    select(AraiosModule).where(AraiosModule.name == name)
                )
                if existing.scalars().first():
                    raise ToolExecutionError(f"Module '{name}' already exists")
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

            if operation == "delete":
                if not name:
                    raise ToolValidationError("'name' is required for delete operation")
                result = await db.execute(
                    select(AraiosModule).where(AraiosModule.name == name)
                )
                mod = result.scalars().first()
                if not mod:
                    raise ToolExecutionError(f"Module '{name}' not found")
                await db.execute(delete(AraiosModuleRecord).where(AraiosModuleRecord.module_name == name))
                await db.execute(delete(AraiosModuleSecret).where(AraiosModuleSecret.module_name == name))
                await db.delete(mod)
                await db.commit()
                return {"ok": True, "message": f"Module '{name}' deleted"}

        raise ToolValidationError(f"Unknown operation '{operation}'. Use: list, get, create, delete")

    return ToolDefinition(
        name="araios_modules",
        description=(
            "Manage araiOS modules. Operations: list, get, create, delete.\n\n"
            "A module is a unified container that can have any combination of:\n"
            "- fields → the module stores records (CRUD via araios_records)\n"
            "- actions → the module has executable Python code (run via araios_action)\n"
            "- page_title → the module has a markdown page tab\n\n"
            "Fields schema (for 'fields' array):\n"
            "Each: {key, label, type ('text'|'textarea'|'email'|'url'|'number'|'date'|'select'|'badge'|'tags'|'readonly'), required, options}.\n\n"
            "Fields config (for 'fields_config' object):\n"
            "Controls how records display: {titleField, subtitleField, badgeField, filterField, metaField}.\n"
            "Without fields_config, records show raw IDs in the UI.\n\n"
            "Action schema (for 'actions' array):\n"
            "Each: {id, label, description, type ('standalone'|'record'), "
            "params: [{key, label, type ('text'|'textarea'|'number'), required}], "
            "code: 'Python code string'}.\n"
            "Available in code: params, secrets, record (detail only), "
            "http (httpx.AsyncClient), json, re, math, base64, datetime, os.\n"
            "Set 'result' dict to return output.\n\n"
            "Secrets schema: [{key, label, required, hint}]. Configured by admin.\n\n"
            "Example — module with records + actions:\n"
            "{name: 'tasks', label: 'Tasks', fields: [{key: 'title', label: 'Title', type: 'text', required: true}, "
            "{key: 'status', label: 'Status', type: 'select', options: ['todo','done']}], "
            "fields_config: {titleField: 'title', badgeField: 'status'}}\n\n"
            "Example — module with actions only:\n"
            "{name: 'weather', label: 'Weather', actions: [{id: 'check', label: 'Check Weather', "
            "type: 'standalone', params: [{key: 'city', label: 'City', type: 'text', required: true}], "
            "code: \"r = await http.get(f'https://wttr.in/{params[\\\"city\\\"]}?format=j1')\\nresult = r.json()\"}]}"
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["operation"],
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["list", "get", "create", "delete"],
                },
                "name": {"type": "string", "description": "Module slug, lowercase (required for get/create/delete)"},
                "label": {"type": "string", "description": "Display name"},
                "description": {"type": "string"},
                "icon": {"type": "string", "description": "Lucide icon name (e.g. 'wrench', 'zap', 'file-text')"},
                "fields": {
                    "type": "array",
                    "description": "Record schema. Each: {key, label, type, required, options}. If provided, module stores records.",
                },
                "fields_config": {
                    "type": "object",
                    "description": "UI display config: {titleField, subtitleField, badgeField, filterField, metaField}",
                },
                "actions": {
                    "type": "array",
                    "description": "Executable actions. Each: {id, label, description, type ('standalone'|'record'), params, code}.",
                },
                "secrets": {
                    "type": "array",
                    "description": "Runtime secrets. Each: {key, label, required, hint}.",
                },
                "page_title": {"type": "string", "description": "If set, module has a markdown page tab with this title."},
                "session_id": {"type": "string"},
            },
        },
        execute=_execute,
        approval_gate=ToolApprovalGate(
            mode=ToolApprovalMode.CONDITIONAL,
            evaluator=_make_araios_evaluator("modules", session_factory),
            waiter=build_tool_db_approval_waiter(session_factory=session_factory),
        ),
    )


# ── Tool: araios_records ──


def araios_records_tool(
    *, session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    """CRUD for records within an AraiOS module."""

    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        operation = payload.get("operation", "list")
        module_name = (payload.get("module") or "").strip().lower()
        record_id = payload.get("record_id")

        if not module_name:
            raise ToolValidationError("'module' is required")

        async with session_factory() as db:
            # Verify module exists
            mod_result = await db.execute(
                select(AraiosModule).where(AraiosModule.name == module_name)
            )
            mod = mod_result.scalars().first()
            if not mod:
                raise ToolExecutionError(f"Module '{module_name}' not found")

            if operation == "list":
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

            if operation == "get":
                if not record_id:
                    raise ToolValidationError("'record_id' is required for get")
                result = await db.execute(
                    select(AraiosModuleRecord).where(
                        AraiosModuleRecord.module_name == module_name,
                        AraiosModuleRecord.id == record_id,
                    )
                )
                rec = result.scalars().first()
                if not rec:
                    raise ToolExecutionError(f"Record '{record_id}' not found")
                return _serialize_record(rec)

            if operation == "create":
                data = payload.get("data", {})
                if not isinstance(data, dict):
                    raise ToolValidationError("'data' must be an object")
                rec = AraiosModuleRecord(
                    id=araios_gen_id(), module_name=module_name, data=data
                )
                db.add(rec)
                await db.commit()
                await db.refresh(rec)
                return _serialize_record(rec)

            if operation == "update":
                if not record_id:
                    raise ToolValidationError("'record_id' is required for update")
                data = payload.get("data", {})
                if not isinstance(data, dict):
                    raise ToolValidationError("'data' must be an object")
                result = await db.execute(
                    select(AraiosModuleRecord).where(
                        AraiosModuleRecord.module_name == module_name,
                        AraiosModuleRecord.id == record_id,
                    )
                )
                rec = result.scalars().first()
                if not rec:
                    raise ToolExecutionError(f"Record '{record_id}' not found")
                merged = dict(rec.data or {})
                merged.update(data)
                rec.data = merged
                await db.commit()
                await db.refresh(rec)
                return _serialize_record(rec)

            if operation == "delete":
                if not record_id:
                    raise ToolValidationError("'record_id' is required for delete")
                result = await db.execute(
                    select(AraiosModuleRecord).where(
                        AraiosModuleRecord.module_name == module_name,
                        AraiosModuleRecord.id == record_id,
                    )
                )
                rec = result.scalars().first()
                if not rec:
                    raise ToolExecutionError(f"Record '{record_id}' not found")
                await db.delete(rec)
                await db.commit()
                return {"ok": True, "message": f"Record '{record_id}' deleted"}

        raise ToolValidationError(f"Unknown operation '{operation}'. Use: list, get, create, update, delete")

    return ToolDefinition(
        name="araios_records",
        description=(
            "CRUD operations on records within an araiOS module. "
            "Operations: list, get, create, update, delete. "
            "Always specify 'module' (the module slug). "
            "For get/update/delete, also specify 'record_id'. "
            "For create/update, pass the fields in 'data'."
        ),
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["module", "operation"],
            "properties": {
                "module": {"type": "string", "description": "Module slug"},
                "operation": {
                    "type": "string",
                    "enum": ["list", "get", "create", "update", "delete"],
                },
                "record_id": {"type": "string"},
                "data": {"type": "object", "description": "Record field values"},
                "session_id": {"type": "string"},
            },
        },
        execute=_execute,
    )


# ── Tool: araios_action ──


def araios_action_tool(
    *, session_factory: async_sessionmaker[AsyncSession],
) -> ToolDefinition:
    """Execute a module action (tool or record-scoped)."""

    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        module_name = (payload.get("module") or "").strip().lower()
        action_id = (payload.get("action_id") or "").strip()
        record_id = payload.get("record_id")
        params = payload.get("params", {})

        if not module_name:
            raise ToolValidationError("'module' is required")
        if not action_id:
            raise ToolValidationError("'action_id' is required")

        async with session_factory() as db:
            mod_result = await db.execute(
                select(AraiosModule).where(AraiosModule.name == module_name)
            )
            mod = mod_result.scalars().first()
            if not mod:
                raise ToolExecutionError(f"Module '{module_name}' not found")

            action_def = next(
                (a for a in (mod.actions or []) if a.get("id") == action_id), None
            )
            if not action_def:
                raise ToolExecutionError(f"Action '{action_id}' not found in module '{module_name}'")
            code = action_def.get("code", "")
            if not code:
                raise ToolExecutionError("Action has no executable code")

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
                raise ToolExecutionError(
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
                    raise ToolExecutionError(f"Record '{record_id}' not found")
                context["record"] = _serialize_record(rec)

            return await execute_action(code, context)

    return ToolDefinition(
        name="araios_action",
        description=(
            "Execute an araiOS module action. For tool modules, specify module + action_id + params. "
            "For record-scoped actions, also specify record_id. "
            "May require approval depending on permission settings."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["module", "action_id"],
            "properties": {
                "module": {"type": "string", "description": "Module slug"},
                "action_id": {"type": "string", "description": "Action ID to execute"},
                "record_id": {"type": "string", "description": "Record ID (for record-scoped actions)"},
                "params": {"type": "object", "description": "Action parameters"},
                "session_id": {"type": "string"},
            },
        },
        execute=_execute,
        approval_gate=ToolApprovalGate(
            mode=ToolApprovalMode.CONDITIONAL,
            evaluator=_make_araios_evaluator("action", session_factory),
            waiter=build_tool_db_approval_waiter(session_factory=session_factory),
        ),
    )


def _serialize_record(r: AraiosModuleRecord) -> dict[str, Any]:
    d = dict(r.data or {})
    d["id"] = r.id
    d["module_name"] = r.module_name
    d["created_at"] = r.created_at.isoformat() if r.created_at else None
    d["updated_at"] = r.updated_at.isoformat() if r.updated_at else None
    return d
