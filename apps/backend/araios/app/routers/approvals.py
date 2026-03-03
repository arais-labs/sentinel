from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.dependencies import get_db
from app.middleware.auth import require_permission, get_role
from app.database.models import (
    Approval, gen_id,
    Lead, Competitor, Client, Proposal, Task,
    LaunchPrepTask, Positioning, SecurityFinding, Document,
)
from app.schemas import ApprovalCreate, ApprovalOut, ApprovalListResponse

router = APIRouter()

# Map resource names to model classes and their field maps
_RESOURCE_MODELS = {
    "leads": Lead,
    "competitors": Competitor,
    "clients": Client,
    "proposals": Proposal,
    "tasks": Task,
    "github-tasks": Task,
    "launch-prep": LaunchPrepTask,
    "positioning": Positioning,
    "security-audit": SecurityFinding,
    "documents": Document,
}

# camelCase → snake_case maps per resource (matching router conventions)
_RESOURCE_FIELD_MAPS = {
    "leads": {"linkedinUrl": "linkedin_url", "lastContact": "last_contact", "nextAction": "next_action", "messageDraft": "message_draft", "approvedMessage": "approved_message"},
    "competitors": {"lastUpdated": "last_updated"},
    "clients": {"linkedIn": "linked_in", "engagementType": "engagement_type", "phaseProgress": "phase_progress", "healthStatus": "health_status", "contractValue": "contract_value", "startDate": "start_date"},
    "proposals": {"leadName": "lead_name", "proposalTitle": "proposal_title", "sentAt": "sent_at"},
    "tasks": {
        "prUrl": "pr_url",
        "workPackage": "work_package",
        "detectedAt": "detected_at",
        "readyAt": "ready_at",
        "handedOffAt": "handed_off_at",
        "closedAt": "closed_at",
        "createdBy": "created_by",
        "updatedBy": "updated_by",
        "handoffTo": "handoff_to",
    },
    "github-tasks": {
        "prUrl": "pr_url",
        "workPackage": "work_package",
        "detectedAt": "detected_at",
        "readyAt": "ready_at",
        "handedOffAt": "handed_off_at",
        "closedAt": "closed_at",
        "createdBy": "created_by",
        "updatedBy": "updated_by",
        "handoffTo": "handoff_to",
    },
    "launch-prep": {},
    "positioning": {"valueProps": "value_props"},
    "security-audit": {"fixNotes": "fix_notes"},
    "documents": {"lastEditedBy": "last_edited_by"},
}


def _to_dict(obj: Approval) -> dict:
    d = {}
    for col in Approval.__table__.columns:
        val = getattr(obj, col.name)
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        d[col.name] = val
    return d


def _map_payload(resource: str, payload: dict) -> dict:
    """Map camelCase payload keys to snake_case for the resource model."""
    fmap = _RESOURCE_FIELD_MAPS.get(resource, {})
    out = {}
    for k, v in payload.items():
        col = fmap.get(k, k)
        out[col] = v
    return out


async def _execute_approval(db: Session, approval: Approval):
    """Execute the operation stored in an approved approval."""
    resource = approval.resource
    action = approval.action
    payload = approval.payload or {}
    resource_id = approval.resource_id

    # ── Module tool action executors ──
    parts = action.split(".")
    if len(parts) == 2:
        from app.database.models import Module, ModuleSecret
        from app.routers.modules import _normalize_action_params
        from app.services.executor import execute_action
        mod = db.query(Module).filter(Module.name == parts[0]).first()
        if mod and mod.type == "tool":
            action_def = next((a for a in (mod.actions or []) if a["id"] == parts[1]), None)
            if action_def and action_def.get("code"):
                secrets = {}
                for s in db.query(ModuleSecret).filter(ModuleSecret.module_name == mod.name).all():
                    secrets[s.key] = s.value
                params = _normalize_action_params(payload if isinstance(payload, dict) else {})
                result = await execute_action(action_def["code"], {"params": params, "secrets": secrets})
                if not result.get("ok", True):
                    raise HTTPException(status_code=502, detail=result.get("error", "Action failed"))
                return

    # ── Module engine executors ──
    action_verb = action.split(".")[-1] if "." in action else action
    if resource == "modules":
        from app.database.models import Module
        from app.routers.modules import (
            _apply_module_updates,
            _extract_module_updates,
            _normalize_module_name,
            _seed_module_permissions,
        )
        module_name = _normalize_module_name(resource_id or payload.get("name"))
        if action_verb == "create":
            if not module_name:
                raise HTTPException(status_code=400, detail="Module name is required")
            if db.query(Module).filter(Module.name == module_name).first():
                raise HTTPException(status_code=409, detail=f"Module '{module_name}' already exists")
            mod = Module(
                name=module_name,
                label=payload.get("label", module_name.title()),
                description=payload.get("description", ""),
                icon=payload.get("icon", "box"),
                type=payload.get("type", "data"),
                fields=payload.get("fields", []),
                list_config=payload.get("list_config", {}),
                actions=payload.get("actions", []),
                secrets=payload.get("secrets", []),
                is_system=False,
                order=payload.get("order", 100),
            )
            db.add(mod)
            db.commit()
            _seed_module_permissions(module_name, db)
            return
        if action_verb == "update":
            if not module_name:
                raise HTTPException(status_code=400, detail="Module name is required for update")
            mod = db.query(Module).filter(Module.name == module_name).first()
            if not mod:
                raise HTTPException(status_code=404, detail=f"Module '{module_name}' not found")
            updates = _extract_module_updates(payload)
            _apply_module_updates(mod, updates)
            db.commit()
            return
        if action_verb == "delete":
            if not module_name:
                raise HTTPException(status_code=400, detail="Module name is required for delete")
            mod = db.query(Module).filter(Module.name == module_name).first()
            if not mod:
                raise HTTPException(status_code=404, detail=f"Module '{module_name}' not found")
            if mod.is_system:
                raise HTTPException(status_code=400, detail="Cannot delete a system module")
            db.delete(mod)
            db.commit()
        return

    # ── DB CRUD executors ──
    model = _RESOURCE_MODELS.get(resource)
    if not model:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot execute action '{action}' — unknown resource: {resource}",
        )

    mapped = _map_payload(resource, payload)

    # Documents use slug-based lookup instead of id
    _SLUG_RESOURCES = {"documents"}

    if action_verb == "create":
        create_kwargs = {"id": payload.get("id", gen_id()), **mapped}
        # Documents need author/last_edited_by which are normally set from token
        if resource == "documents":
            create_kwargs.setdefault("author", "agent")
            create_kwargs.setdefault("last_edited_by", "agent")
        item = model(**create_kwargs)
        db.add(item)

    elif action_verb == "update":
        # Positioning is a single-row model — always use "default" as ID
        lookup_id = "default" if resource == "positioning" else resource_id
        if resource in _SLUG_RESOURCES:
            item = db.query(model).filter(model.slug == lookup_id).first()
        else:
            item = db.query(model).filter(model.id == lookup_id).first()
        if not item:
            raise HTTPException(status_code=404, detail=f"{resource} {lookup_id} not found")
        # Deep-merge JSON dict fields instead of overwriting
        _DEEP_MERGE_FIELDS = {"pricing", "icp", "work_package"}
        for col, val in mapped.items():
            if hasattr(item, col):
                if col in _DEEP_MERGE_FIELDS and isinstance(getattr(item, col), dict) and isinstance(val, dict):
                    merged = {**(getattr(item, col) or {}), **val}
                    setattr(item, col, merged)
                else:
                    setattr(item, col, val)
        # Documents: increment version and set last_edited_by
        if resource in _SLUG_RESOURCES and hasattr(item, "version"):
            item.version += 1
            item.last_edited_by = "admin"

    elif action_verb == "delete":
        if resource in _SLUG_RESOURCES:
            count = db.query(model).filter(model.slug == resource_id).delete()
        else:
            count = db.query(model).filter(model.id == resource_id).delete()
        if not count:
            raise HTTPException(status_code=404, detail=f"{resource} {resource_id} not found")

    db.commit()


@router.get(
    "",
    response_model=ApprovalListResponse,
    summary="List approvals",
    description="Returns all approval records. Optionally filter by status (pending, approved, rejected).",
)
async def list_approvals(
    status: Optional[str] = Query(None, description="Filter by status: pending, approved, rejected"),
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("approvals.list")),
):
    q = db.query(Approval).order_by(Approval.created_at.desc())
    if status:
        q = q.filter(Approval.status == status)
    return {"approvals": [_to_dict(a) for a in q.all()]}


@router.post(
    "",
    status_code=201,
    response_model=ApprovalOut,
    summary="Create an approval request",
    description="Manually create an approval request. The agent should prefer using "
    "the dedicated endpoints (e.g. POST /api/slack/send) which auto-create approvals "
    "via the permission middleware. Use this endpoint only for custom actions.",
)
async def create_approval(
    body: ApprovalCreate,
    db: Session = Depends(get_db),
    _: None = Depends(require_permission("approvals.create")),
):
    approval = Approval(
        id=gen_id(),
        action=body.action,
        resource=body.resource,
        resource_id=body.resourceId,
        description=body.description or "",
        payload=body.payload,
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return _to_dict(approval)


@router.post(
    "/{approval_id}/approve",
    response_model=ApprovalOut,
    summary="Approve a pending request",
    description="Admin-only. Executes the stored action and marks the approval as approved.",
)
async def approve(
    approval_id: str,
    db: Session = Depends(get_db),
    role: str = Depends(get_role),
):
    if role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can approve")

    approval = db.query(Approval).filter(Approval.id == approval_id).first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=400, detail=f"Approval is already {approval.status}")

    await _execute_approval(db, approval)

    approval.status = "approved"
    approval.resolved_at = datetime.now(timezone.utc)
    approval.resolved_by = "admin"
    db.commit()
    db.refresh(approval)
    return _to_dict(approval)


@router.post(
    "/{approval_id}/reject",
    response_model=ApprovalOut,
    summary="Reject a pending request",
    description="Admin-only. Marks the approval as rejected without executing the action.",
)
async def reject(
    approval_id: str,
    db: Session = Depends(get_db),
    role: str = Depends(get_role),
):
    if role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can reject")

    approval = db.query(Approval).filter(Approval.id == approval_id).first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=400, detail=f"Approval is already {approval.status}")

    approval.status = "rejected"
    approval.resolved_at = datetime.now(timezone.utc)
    approval.resolved_by = "admin"
    db.commit()
    db.refresh(approval)
    return _to_dict(approval)
