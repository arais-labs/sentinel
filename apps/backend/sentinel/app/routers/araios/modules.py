"""Generic module registry + record CRUD router (async SQLAlchemy port)."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.models.araios import (
    AraiosModule,
    AraiosModuleRecord,
    AraiosModuleSecret,
    AraiosPermission,
    AraiosApproval,
    araios_gen_id,
)
from app.services.araios.executor import execute_action

router = APIRouter()


# ── Auth helpers ──


async def get_role(user: TokenPayload = Depends(require_auth)) -> str:
    return user.role


def _require_araios_permission(action: str):
    """Dependency factory – returns an async dependency that enforces AraiOS
    permission logic for *action*.

    * admin  -> always passes
    * allow  -> passes
    * deny   -> 403
    * approval -> creates AraiosApproval, raises 202
    """

    async def _dependency(
        user: TokenPayload = Depends(require_auth),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        if user.role == "admin":
            return

        result = await db.execute(
            select(AraiosPermission).where(AraiosPermission.action == action)
        )
        row = result.scalars().first()
        perm = row.level if row else "allow"

        if perm == "allow":
            return

        if perm == "deny":
            raise HTTPException(
                status_code=403,
                detail=f"Action '{action}' is not allowed for agent role",
            )

        if perm == "approval":
            approval_resource = (
                action.rsplit(".", 1)[0] if "." in action else action
            )
            description = f"Agent requested: {action}"
            approval = AraiosApproval(
                id=araios_gen_id(),
                status="pending",
                action=action,
                resource=approval_resource,
                description=description,
            )
            db.add(approval)
            await db.commit()
            await db.refresh(approval)
            raise HTTPException(
                status_code=202,
                detail={
                    "message": "Action requires approval",
                    "approval": {
                        "id": approval.id,
                        "status": approval.status,
                        "action": approval.action,
                        "description": approval.description,
                    },
                },
            )

    return _dependency


# ── Inline permission check (for action execution) ──


async def _check_action_permission(
    action: str,
    role: str,
    db: AsyncSession,
    body: dict | None = None,
    *,
    resource: str | None = None,
    resource_id: str | None = None,
) -> None:
    if role == "admin":
        return

    result = await db.execute(
        select(AraiosPermission).where(AraiosPermission.action == action)
    )
    row = result.scalars().first()
    perm = row.level if row else "allow"

    if perm == "allow":
        return

    if perm == "deny":
        raise HTTPException(
            status_code=403,
            detail=f"Action '{action}' is not allowed for agent role",
        )

    if perm == "approval":
        approval_resource = resource or (
            action.rsplit(".", 1)[0] if "." in action else action
        )
        description = f"Agent requested: {action}" + (
            f" on {resource_id}" if resource_id else ""
        )
        approval = AraiosApproval(
            id=araios_gen_id(),
            status="pending",
            action=action,
            resource=approval_resource,
            resource_id=resource_id,
            description=description,
            payload=body,
        )
        db.add(approval)
        await db.commit()
        await db.refresh(approval)
        raise HTTPException(
            status_code=202,
            detail={
                "message": "Action requires approval",
                "approval": {
                    "id": approval.id,
                    "status": approval.status,
                    "action": approval.action,
                    "description": approval.description,
                },
            },
        )


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
    validated: list[dict[str, Any]] = []
    for action in value:
        if not isinstance(action, dict):
            raise HTTPException(
                status_code=400,
                detail="Each action in 'actions' must be an object",
            )
        action_id = action.get("id")
        if not isinstance(action_id, str) or not action_id.strip():
            raise HTTPException(
                status_code=400,
                detail="Each action in 'actions' requires a non-empty 'id'",
            )
        validated.append(action)
    return validated


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


async def _seed_module_permissions(name: str, db: AsyncSession) -> None:
    result = await db.execute(
        select(AraiosModule).where(AraiosModule.name == name)
    )
    mod = result.scalars().first()
    # Always seed CRUD permissions + per-action permissions
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
    action_keys = [k for k, _ in defaults]
    result = await db.execute(
        select(AraiosPermission).where(AraiosPermission.action.in_(action_keys))
    )
    existing = {p.action for p in result.scalars().all()}
    for action, level in defaults:
        if action not in existing:
            db.add(AraiosPermission(action=action, level=level))
    await db.commit()


# ── Module CRUD ──


@router.get("")
async def list_modules(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_araios_permission("modules.list")),
):
    result = await db.execute(
        select(AraiosModule).order_by(AraiosModule.order, AraiosModule.name)
    )
    mods = result.scalars().all()
    return {"modules": [_serialize_module(m) for m in mods]}


@router.post("", status_code=201)
async def create_module(
    body: dict,
    db: AsyncSession = Depends(get_db),
    role: str = Depends(get_role),
):
    name = _normalize_module_name(body.get("name"))
    if not name:
        raise HTTPException(status_code=400, detail="Module name is required")
    result = await db.execute(
        select(AraiosModule).where(AraiosModule.name == name)
    )
    if result.scalars().first():
        raise HTTPException(
            status_code=409, detail=f"Module '{name}' already exists"
        )
    await _check_action_permission(
        "modules.create", role, db, body, resource="modules", resource_id=name
    )
    mod = AraiosModule(
        name=name,
        label=body.get("label", name.title()),
        description=body.get("description", ""),
        icon=body.get("icon", "box"),
        fields=body.get("fields", []),
        fields_config=body.get("fields_config", {}),
        actions=body.get("actions", []),
        secrets=body.get("secrets", []),
        page_title=body.get("page_title"),
        order=body.get("order", 100),
    )
    db.add(mod)
    await db.commit()
    await db.refresh(mod)
    await _seed_module_permissions(name, db)
    return _serialize_module(mod)


@router.get("/{name}")
async def get_module(
    name: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_araios_permission("modules.list")),
):
    return _serialize_module(await _module_or_404(name, db))


@router.patch("/{name}")
async def update_module(
    name: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    role: str = Depends(get_role),
):
    mod = await _module_or_404(name, db)
    updates = _extract_module_updates(body)
    await _check_action_permission(
        "modules.update", role, db, body, resource="modules", resource_id=mod.name
    )
    _apply_module_updates(mod, updates)
    await db.commit()
    await db.refresh(mod)
    return _serialize_module(mod)


@router.delete("/{name}")
async def delete_module(
    name: str,
    db: AsyncSession = Depends(get_db),
    role: str = Depends(get_role),
):
    mod = await _module_or_404(name, db)
    await _check_action_permission(
        "modules.delete", role, db, {}, resource="modules", resource_id=mod.name
    )
    # Delete child records and secrets first (FK constraint)
    await db.execute(delete(AraiosModuleRecord).where(AraiosModuleRecord.module_name == name))
    await db.execute(delete(AraiosModuleSecret).where(AraiosModuleSecret.module_name == name))
    await db.delete(mod)
    await db.commit()
    return {"ok": True}


# ── Record CRUD ──


@router.get("/{name}/records")
async def list_records(
    name: str,
    filter_field: Optional[str] = Query(None),
    filter_value: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_araios_permission("modules.list")),
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
    _: None = Depends(_require_araios_permission("modules.list")),
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
    _: None = Depends(_require_araios_permission("modules.list")),
):
    return _serialize_record(await _record_or_404(name, record_id, db))


@router.patch("/{name}/records/{record_id}")
async def update_record(
    name: str,
    record_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_araios_permission("modules.list")),
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
    _: None = Depends(_require_araios_permission("modules.list")),
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
    _: None = Depends(_require_araios_permission("modules.list")),
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
    _: None = Depends(_require_araios_permission("modules.create")),
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
    _: None = Depends(_require_araios_permission("modules.create")),
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
    role: str = Depends(get_role),
):
    params = _normalize_action_params(body)
    await _check_action_permission(f"{name}.{action_id}", role, db, params)
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
    role: str = Depends(get_role),
):
    params = _normalize_action_params(body)
    await _check_action_permission(f"{name}.{action_id}", role, db, params)
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
