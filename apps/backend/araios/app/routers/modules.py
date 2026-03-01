"""Generic module registry + record CRUD router."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db
from fastapi import Request
from app.middleware.auth import require_permission, get_role
from app.database.models import Module, ModuleRecord, ModuleSecret, Permission, Approval, gen_id
from app.services.executor import execute_action

router = APIRouter()


# ── Helpers ──


async def _check_action_permission(action: str, role: str, db: Session, body: dict = None):
    """Check permission for a dynamic action string.

    - Admin: always allowed
    - Agent + allow: allowed
    - Agent + approval: creates approval record and raises 202
    - Agent + deny: raises 403
    """
    if role == "admin":
        return

    row = db.query(Permission).filter(Permission.action == action).first()
    perm = row.level if row else "allow"

    if perm == "allow":
        return

    if perm == "deny":
        raise HTTPException(status_code=403, detail=f"Action '{action}' is not allowed for agent role")

    if perm == "approval":
        resource = action.rsplit(".", 1)[0] if "." in action else action
        approval = Approval(
            id=gen_id(),
            status="pending",
            action=action,
            resource=resource,
            description=f"Agent requested: {action}",
            payload=body,
        )
        db.add(approval)
        db.commit()
        db.refresh(approval)
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


def _module_or_404(name: str, db: Session) -> Module:
    mod = db.query(Module).filter(Module.name == name).first()
    if not mod:
        raise HTTPException(status_code=404, detail=f"Module '{name}' not found")
    return mod


def _record_or_404(module_name: str, record_id: str, db: Session) -> ModuleRecord:
    rec = (
        db.query(ModuleRecord)
        .filter(ModuleRecord.module_name == module_name, ModuleRecord.id == record_id)
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail=f"Record '{record_id}' not found")
    return rec


def _serialize_module(m: Module) -> dict:
    return {
        "name": m.name,
        "label": m.label,
        "description": m.description or "",
        "icon": m.icon,
        "type": m.type or "data",
        "fields": m.fields or [],
        "list_config": m.list_config or {},
        "actions": m.actions or [],
        "secrets": m.secrets or [],   # [{key, label, required}] — no values
        "is_system": m.is_system,
        "order": m.order,
    }


def _resolve_secrets(module_name: str, db: Session) -> dict:
    """Return {key: value} for all stored secrets of a module."""
    rows = db.query(ModuleSecret).filter(ModuleSecret.module_name == module_name).all()
    return {r.key: r.value for r in rows}


def _check_required_secrets(mod: Module, secrets: dict):
    """Raise 400 listing any required secrets that have not been configured."""
    missing = [
        s["key"] for s in (mod.secrets or [])
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


def _serialize_record(r: ModuleRecord) -> dict:
    d = dict(r.data or {})
    d["id"] = r.id
    d["module_name"] = r.module_name
    d["created_at"] = r.created_at.isoformat() if r.created_at else None
    d["updated_at"] = r.updated_at.isoformat() if r.updated_at else None
    return d


def _seed_module_permissions(name: str, db: Session):
    """Create default permission entries for a newly registered module."""
    mod = db.query(Module).filter(Module.name == name).first()
    if mod and mod.type == "tool":
        # Tool modules: one permission per action
        defaults = [
            (f"{name}.{a['id']}", "allow")
            for a in (mod.actions or [])
        ]
    else:
        # Data/page modules: standard CRUD set
        defaults = [
            (f"{name}.list",   "allow"),
            (f"{name}.create", "allow"),
            (f"{name}.update", "allow"),
            (f"{name}.delete", "approval"),
        ]
    existing = {
        p.action for p in db.query(Permission)
        .filter(Permission.action.in_([k for k, _ in defaults]))
        .all()
    }
    for action, level in defaults:
        if action not in existing:
            db.add(Permission(action=action, level=level))
    db.commit()


# ── Module CRUD ──

@router.get("/")
async def list_modules(
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("modules.list")),
):
    mods = db.query(Module).order_by(Module.order, Module.name).all()
    return {"modules": [_serialize_module(m) for m in mods]}


@router.post("", status_code=201)
async def create_module(
    body: dict,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("modules.create")),
):
    name = body.get("name", "").strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Module name is required")
    if db.query(Module).filter(Module.name == name).first():
        raise HTTPException(status_code=409, detail=f"Module '{name}' already exists")

    mod = Module(
        name=name,
        label=body.get("label", name.title()),
        description=body.get("description", ""),
        icon=body.get("icon", "box"),
        type=body.get("type", "data"),
        fields=body.get("fields", []),
        list_config=body.get("list_config", {}),
        actions=body.get("actions", []),
        secrets=body.get("secrets", []),
        is_system=body.get("is_system", False),
        order=body.get("order", 100),
    )
    db.add(mod)
    db.commit()
    db.refresh(mod)

    # Auto-seed permissions for the new module
    _seed_module_permissions(name, db)

    return _serialize_module(mod)


@router.get("/{name}")
async def get_module(
    name: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("modules.list")),
):
    return _serialize_module(_module_or_404(name, db))


@router.patch("/{name}")
async def update_module(
    name: str,
    body: dict,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("modules.create")),
):
    mod = _module_or_404(name, db)
    for field in ("label", "icon", "type", "fields", "list_config", "actions", "secrets", "description", "order"):
        if field in body:
            setattr(mod, field, body[field])
    db.commit()
    db.refresh(mod)
    return _serialize_module(mod)


@router.delete("/{name}")
async def delete_module(
    name: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("modules.create")),
):
    mod = _module_or_404(name, db)
    if mod.is_system:
        raise HTTPException(status_code=400, detail="Cannot delete a system module")
    db.delete(mod)
    db.commit()
    return {"ok": True}


# ── Record CRUD ──

@router.get("/{name}/records")
async def list_records(
    name: str,
    filter_field: Optional[str] = Query(None),
    filter_value: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("modules.list")),
):
    _module_or_404(name, db)
    records = (
        db.query(ModuleRecord)
        .filter(ModuleRecord.module_name == name)
        .order_by(ModuleRecord.created_at.desc())
        .all()
    )
    result = [_serialize_record(r) for r in records]
    if filter_field and filter_value and filter_value != "all":
        result = [r for r in result if str(r.get(filter_field, "")) == filter_value]
    return {"records": result}


@router.post("/{name}/records", status_code=201)
async def create_record(
    name: str,
    body: dict,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("modules.list")),
):
    _module_or_404(name, db)
    data = {k: v for k, v in body.items() if k not in ("id", "module_name", "created_at", "updated_at")}
    rec = ModuleRecord(id=gen_id(), module_name=name, data=data)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return _serialize_record(rec)


@router.get("/{name}/records/{record_id}")
async def get_record(
    name: str,
    record_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("modules.list")),
):
    return _serialize_record(_record_or_404(name, record_id, db))


@router.patch("/{name}/records/{record_id}")
async def update_record(
    name: str,
    record_id: str,
    body: dict,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("modules.list")),
):
    rec = _record_or_404(name, record_id, db)
    data = dict(rec.data or {})
    for k, v in body.items():
        if k not in ("id", "module_name", "created_at", "updated_at"):
            data[k] = v
    rec.data = data
    db.commit()
    db.refresh(rec)
    return _serialize_record(rec)


@router.delete("/{name}/records/{record_id}")
async def delete_record(
    name: str,
    record_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("modules.list")),
):
    rec = _record_or_404(name, record_id, db)
    db.delete(rec)
    db.commit()
    return {"ok": True}


# ── Secrets management ──

@router.get("/{name}/secrets-status")
async def secrets_status(
    name: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("modules.list")),
):
    """Return {key: bool} — whether each secret is configured. Never returns values."""
    mod = _module_or_404(name, db)
    stored = {r.key for r in db.query(ModuleSecret).filter(ModuleSecret.module_name == name).all()}
    status = {s["key"]: s["key"] in stored for s in (mod.secrets or [])}
    return {"secrets": status}


@router.put("/{name}/secrets/{key}", status_code=200)
async def set_secret(
    name: str,
    key: str,
    body: dict,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("modules.create")),
):
    """Set (or update) a secret value for a module. Admin only."""
    _module_or_404(name, db)
    value = body.get("value", "")
    if not value:
        raise HTTPException(status_code=400, detail="Value is required")
    row = db.query(ModuleSecret).filter(
        ModuleSecret.module_name == name, ModuleSecret.key == key
    ).first()
    if row:
        row.value = value
    else:
        db.add(ModuleSecret(module_name=name, key=key, value=value))
    db.commit()
    return {"ok": True}


@router.delete("/{name}/secrets/{key}")
async def delete_secret(
    name: str,
    key: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("modules.create")),
):
    """Clear a secret. Admin only."""
    db.query(ModuleSecret).filter(
        ModuleSecret.module_name == name, ModuleSecret.key == key
    ).delete()
    db.commit()
    return {"ok": True}


# ── Action execution ──

@router.post("/{name}/records/{record_id}/action/{action_id}")
async def run_record_action(
    name: str,
    record_id: str,
    action_id: str,
    body: dict = {},
    db: Session = Depends(get_db),
    role: str = Depends(get_role),
):
    """Execute a named action on a specific record (data/page modules)."""
    await _check_action_permission(f"{name}.{action_id}", role, db, body)
    mod = _module_or_404(name, db)
    rec = _record_or_404(name, record_id, db)
    action = next((a for a in (mod.actions or []) if a.get("id") == action_id), None)
    if not action:
        raise HTTPException(status_code=404, detail=f"Action '{action_id}' not found")
    code = action.get("code", "")
    if not code:
        raise HTTPException(status_code=400, detail="Action has no executable code")
    secrets = _resolve_secrets(name, db)
    _check_required_secrets(mod, secrets)
    return await execute_action(code, {"record": _serialize_record(rec), "params": body, "secrets": secrets})


@router.post("/{name}/action/{action_id}")
async def run_module_action(
    name: str,
    action_id: str,
    body: dict = {},
    db: Session = Depends(get_db),
    role: str = Depends(get_role),
):
    """Execute a module-level action (api modules — no record context)."""
    await _check_action_permission(f"{name}.{action_id}", role, db, body)
    mod = _module_or_404(name, db)
    action = next((a for a in (mod.actions or []) if a.get("id") == action_id), None)
    if not action:
        raise HTTPException(status_code=404, detail=f"Action '{action_id}' not found")
    code = action.get("code", "")
    if not code:
        raise HTTPException(status_code=400, detail="Action has no executable code")
    secrets = _resolve_secrets(name, db)
    _check_required_secrets(mod, secrets)
    return await execute_action(code, {"params": body, "secrets": secrets})
