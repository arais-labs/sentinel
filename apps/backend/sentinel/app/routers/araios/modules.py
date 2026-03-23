"""Generic module registry + record CRUD router (async SQLAlchemy port)."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import AsyncSessionLocal
from app.dependencies import get_db
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
from app.services.tools.runtime_registry import rebuild_runtime_registry

router = APIRouter()


def _native_tool_icon(tool_name: str) -> str:
    lower = tool_name.strip().lower()
    if "browser" in lower:
        return "globe"
    if "memory" in lower:
        return "brain"
    if "runtime" in lower or "python" in lower or "git" in lower:
        return "terminal"
    if "trigger" in lower:
        return "clock-3"
    if "telegram" in lower:
        return "send"
    if "document" in lower:
        return "file-text"
    if "task" in lower:
        return "check-square"
    if "module" in lower:
        return "boxes"
    return "box"


# ── Helpers ──


async def _module_or_404(name: str, db: AsyncSession) -> AraiosModule:
    result = await db.execute(
        select(AraiosModule).where(AraiosModule.name == name)
    )
    mod = result.scalars().first()
    if not mod:
        raise HTTPException(status_code=404, detail=f"Module '{name}' not found")
    return mod


_MODULE_MUTABLE_FIELDS = (
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
    "pinned",
)


def _normalize_module_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _extract_module_updates(body: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for field in _MODULE_MUTABLE_FIELDS:
        if field in body:
            value = body[field]
            if field == "actions":
                value = _validate_action_updates(value)
            updates[field] = value
    if not updates:
        raise HTTPException(
            status_code=400,
            detail=(
                "At least one editable module field is required "
                f"({', '.join(_MODULE_MUTABLE_FIELDS)})"
            ),
        )
    return updates


def _validate_action_updates(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="'actions' must be a list")
    try:
        return normalize_dynamic_module_actions(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _merge_action_updates(
    existing_actions: list[Any], patch_actions: list[dict[str, Any]]
) -> list[Any]:
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


def _apply_module_updates(mod: AraiosModule, updates: dict[str, Any]) -> None:
    resolved_updates = dict(updates)
    if "actions" in resolved_updates:
        resolved_updates["actions"] = _merge_action_updates(
            list(mod.actions or []),
            resolved_updates["actions"],
        )
    for field, value in resolved_updates.items():
        setattr(mod, field, value)


async def _record_or_404(
    module_name: str, record_id: str, db: AsyncSession
) -> AraiosModuleRecord:
    result = await db.execute(
        select(AraiosModuleRecord).where(
            AraiosModuleRecord.module_name == module_name,
            AraiosModuleRecord.id == record_id,
        )
    )
    rec = result.scalars().first()
    if not rec:
        raise HTTPException(
            status_code=404, detail=f"Record '{record_id}' not found"
        )
    return rec


def _serialize_module(m: AraiosModule) -> dict:
    return {
        "name": m.name,
        "label": m.label,
        "description": m.description or "",
        "icon": m.icon,
        "fields": m.fields or [],
        "fields_config": m.fields_config or {},
        "actions": m.actions or [],
        "secrets": m.secrets or [],
        "page_title": m.page_title,
        "page_content": m.page_content,
        "pinned": m.pinned,
        "system": m.system,
        "order": m.order,
    }


async def _resolve_secrets(module_name: str, db: AsyncSession) -> dict:
    result = await db.execute(
        select(AraiosModuleSecret).where(
            AraiosModuleSecret.module_name == module_name
        )
    )
    rows = result.scalars().all()
    return {r.key: r.value for r in rows}


def _check_required_secrets(mod: AraiosModule, secrets: dict) -> None:
    missing = [
        s["key"]
        for s in (mod.secrets or [])
        if s.get("required") and not secrets.get(s["key"])
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Module '{mod.name}' is missing required secrets: {missing}. "
                f"Configure them in the UI under the {mod.label} tool panel."
            ),
        )


def _serialize_record(r: AraiosModuleRecord) -> dict:
    d = dict(r.data or {})
    d["id"] = r.id
    d["module_name"] = r.module_name
    d["created_at"] = r.created_at.isoformat() if r.created_at else None
    d["updated_at"] = r.updated_at.isoformat() if r.updated_at else None
    return d


def _normalize_action_params(body: dict | None) -> dict:
    if not isinstance(body, dict):
        return {}
    nested = body.get("params")
    if isinstance(nested, dict):
        merged = dict(nested)
        for key, value in body.items():
            if key == "params":
                continue
            merged[key] = value
        return merged
    return body


# ── Module CRUD ──


@router.get("")
async def list_modules(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AraiosModule).order_by(AraiosModule.order, AraiosModule.name)
    )
    mods = result.scalars().all()
    user_modules = [_serialize_module(m) for m in mods]

    # Inject native tools from the Sentinel tool registry
    native_modules = []
    registry = getattr(getattr(request, "app", None), "state", None)
    if registry is not None:
        tool_registry = getattr(registry, "tool_registry", None)
        if tool_registry is not None:
            user_module_names = {m["name"] for m in user_modules}
            # Skip araios meta-tools — they're plumbing, not user-facing
            skip = {"module_manager"}
            for tool in tool_registry.list_all():
                if tool.name in user_module_names or tool.name in skip:
                    continue
                native_modules.append({
                    "name": tool.name,
                    "label": tool.name.replace("_", " ").title(),
                    "description": tool.description or "",
                    "icon": _native_tool_icon(tool.name),
                    "fields": [],
                    "fields_config": {},
                    "actions": [],
                    "secrets": [],
                    "page_title": None,
                    "page_content": None,
                    "pinned": True,
                    "order": 10,
                    "native": True,
                })

    return {"modules": native_modules + user_modules}


@router.post("", status_code=201)
async def create_module(
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    name = _normalize_module_name(body.get("name"))
    if not name:
        raise HTTPException(status_code=400, detail="Module name is required")
    permissions = body.get("permissions", {})
    if permissions is not None and not isinstance(permissions, dict):
        raise HTTPException(status_code=400, detail="'permissions' must be an object")
    result = await db.execute(
        select(AraiosModule).where(AraiosModule.name == name)
    )
    if result.scalars().first():
        raise HTTPException(
            status_code=409, detail=f"Module '{name}' already exists"
        )
    registry = getattr(request.app.state, "tool_registry", None)
    if registry is not None and registry.get(name) is not None:
        raise HTTPException(status_code=409, detail=f"Module '{name}' conflicts with an existing tool")
    actions = _validate_action_updates(body.get("actions", []))
    mod = AraiosModule(
        name=name,
        label=body.get("label", name.title()),
        description=body.get("description", ""),
        icon=body.get("icon", "box"),
        fields=body.get("fields", []),
        fields_config=body.get("fields_config", {}),
        actions=actions,
        secrets=body.get("secrets", []),
        page_title=body.get("page_title"),
        page_content=body.get("page_content"),
        system=False,
        order=body.get("order", 100),
    )
    db.add(mod)
    await db.commit()
    await db.refresh(mod)
    await sync_dynamic_module_permissions(
        db,
        module_name=name,
        actions=actions,
        permissions=permissions,
    )
    session_factory = getattr(request.app.state, "db_session_factory", AsyncSessionLocal)
    await rebuild_runtime_registry(app_state=request.app.state, session_factory=session_factory)
    return _serialize_module(mod)


@router.get("/{name}")
async def get_module(
    name: str,
    db: AsyncSession = Depends(get_db),
):
    return _serialize_module(await _module_or_404(name, db))


@router.patch("/{name}")
async def update_module(
    name: str,
    body: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    mod = await _module_or_404(name, db)
    permissions = body.get("permissions")
    if permissions is not None and not isinstance(permissions, dict):
        raise HTTPException(status_code=400, detail="'permissions' must be an object")
    updates = _extract_module_updates(body) if any(field in body for field in _MODULE_MUTABLE_FIELDS) else {}
    if updates:
        _apply_module_updates(mod, updates)
    await db.commit()
    await db.refresh(mod)
    await sync_dynamic_module_permissions(
        db,
        module_name=name,
        actions=normalize_dynamic_module_actions(list(mod.actions or [])),
        permissions=permissions,
    )
    session_factory = getattr(request.app.state, "db_session_factory", AsyncSessionLocal)
    await rebuild_runtime_registry(app_state=request.app.state, session_factory=session_factory)
    return _serialize_module(mod)


@router.delete("/{name}")
async def delete_module(
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    mod = await _module_or_404(name, db)
    # Delete child records and secrets first (FK constraint)
    await db.execute(delete(AraiosModuleRecord).where(AraiosModuleRecord.module_name == name))
    await db.execute(delete(AraiosModuleSecret).where(AraiosModuleSecret.module_name == name))
    await db.delete(mod)
    await db.commit()
    await delete_dynamic_module_permissions(db, module_name=name)
    session_factory = getattr(request.app.state, "db_session_factory", AsyncSessionLocal)
    await rebuild_runtime_registry(app_state=request.app.state, session_factory=session_factory)
    return {"ok": True}


# ── Record CRUD ──


@router.get("/{name}/records")
async def list_records(
    name: str,
    filter_field: Optional[str] = Query(None),
    filter_value: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _module_or_404(name, db)
    result = await db.execute(
        select(AraiosModuleRecord)
        .where(AraiosModuleRecord.module_name == name)
        .order_by(AraiosModuleRecord.created_at.desc())
    )
    records = result.scalars().all()
    serialized = [_serialize_record(r) for r in records]
    if filter_field and filter_value and filter_value != "all":
        serialized = [
            r for r in serialized if str(r.get(filter_field, "")) == filter_value
        ]
    return {"records": serialized}


@router.post("/{name}/records", status_code=201)
async def create_record(
    name: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    await _module_or_404(name, db)
    data = {
        k: v
        for k, v in body.items()
        if k not in ("id", "module_name", "created_at", "updated_at")
    }
    rec = AraiosModuleRecord(id=araios_gen_id(), module_name=name, data=data)
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return _serialize_record(rec)


@router.get("/{name}/records/{record_id}")
async def get_record(
    name: str,
    record_id: str,
    db: AsyncSession = Depends(get_db),
):
    return _serialize_record(await _record_or_404(name, record_id, db))


@router.patch("/{name}/records/{record_id}")
async def update_record(
    name: str,
    record_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    rec = await _record_or_404(name, record_id, db)
    data = dict(rec.data or {})
    for k, v in body.items():
        if k not in ("id", "module_name", "created_at", "updated_at"):
            data[k] = v
    rec.data = data
    await db.commit()
    await db.refresh(rec)
    return _serialize_record(rec)


@router.delete("/{name}/records/{record_id}")
async def delete_record(
    name: str,
    record_id: str,
    db: AsyncSession = Depends(get_db),
):
    rec = await _record_or_404(name, record_id, db)
    await db.delete(rec)
    await db.commit()
    return {"ok": True}


# ── Secrets management ──


@router.get("/{name}/secrets-status")
async def secrets_status(
    name: str,
    db: AsyncSession = Depends(get_db),
):
    mod = await _module_or_404(name, db)
    result = await db.execute(
        select(AraiosModuleSecret).where(
            AraiosModuleSecret.module_name == name
        )
    )
    stored = {r.key for r in result.scalars().all()}
    status = {s["key"]: s["key"] in stored for s in (mod.secrets or [])}
    return {"secrets": status}


@router.put("/{name}/secrets/{key}", status_code=200)
async def set_secret(
    name: str,
    key: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    await _module_or_404(name, db)
    value = body.get("value", "")
    if not value:
        raise HTTPException(status_code=400, detail="Value is required")
    result = await db.execute(
        select(AraiosModuleSecret).where(
            AraiosModuleSecret.module_name == name,
            AraiosModuleSecret.key == key,
        )
    )
    row = result.scalars().first()
    if row:
        row.value = value
    else:
        db.add(AraiosModuleSecret(module_name=name, key=key, value=value))
    await db.commit()
    return {"ok": True}


@router.delete("/{name}/secrets/{key}")
async def delete_secret(
    name: str,
    key: str,
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        delete(AraiosModuleSecret).where(
            AraiosModuleSecret.module_name == name,
            AraiosModuleSecret.key == key,
        )
    )
    await db.commit()
    return {"ok": True}


# ── Action execution ──


@router.post("/{name}/records/{record_id}/action/{action_id}")
async def run_record_action(
    name: str,
    record_id: str,
    action_id: str,
    body: dict | None = None,
    db: AsyncSession = Depends(get_db),
):
    params = _normalize_action_params(body)
    mod = await _module_or_404(name, db)
    rec = await _record_or_404(name, record_id, db)
    action = next(
        (a for a in (mod.actions or []) if a.get("id") == action_id), None
    )
    if not action:
        raise HTTPException(
            status_code=404, detail=f"Action '{action_id}' not found"
        )
    code = action.get("code", "")
    if not code:
        raise HTTPException(
            status_code=400, detail="Action has no executable code"
        )
    secrets = await _resolve_secrets(name, db)
    _check_required_secrets(mod, secrets)
    return await execute_action(
        code,
        {"record": _serialize_record(rec), "params": params, "secrets": secrets},
    )


@router.post("/{name}/action/{action_id}")
async def run_module_action(
    name: str,
    action_id: str,
    body: dict | None = None,
    db: AsyncSession = Depends(get_db),
):
    params = _normalize_action_params(body)
    mod = await _module_or_404(name, db)
    action = next(
        (a for a in (mod.actions or []) if a.get("id") == action_id), None
    )
    if not action:
        raise HTTPException(
            status_code=404, detail=f"Action '{action_id}' not found"
        )
    code = action.get("code", "")
    if not code:
        raise HTTPException(
            status_code=400, detail="Action has no executable code"
        )
    secrets = await _resolve_secrets(name, db)
    _check_required_secrets(mod, secrets)
    return await execute_action(
        code, {"params": params, "secrets": secrets}
    )
