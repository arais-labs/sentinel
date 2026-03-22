"""AraiOS approvals router — async SQLAlchemy port."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.models.araios import (
    AraiosApproval,
    AraiosClient,
    AraiosCompetitor,
    AraiosDocument,
    AraiosLaunchPrepTask,
    AraiosLead,
    AraiosModule,
    AraiosModuleRecord,
    AraiosModuleSecret,
    AraiosPermission,
    AraiosPositioning,
    AraiosProposal,
    AraiosSecurityFinding,
    AraiosTask,
    araios_gen_id,
)
from app.models.system import SystemSetting
from app.schemas.araios import ApprovalCreate, ApprovalListResponse, ApprovalOut
from app.services.araios.executor import execute_action

router = APIRouter()

# ── Resource → Model mapping ──

_RESOURCE_MODELS: dict[str, type] = {
    "leads": AraiosLead,
    "competitors": AraiosCompetitor,
    "clients": AraiosClient,
    "proposals": AraiosProposal,
    "tasks": AraiosTask,
    "github-tasks": AraiosTask,
    "launch-prep": AraiosLaunchPrepTask,
    "positioning": AraiosPositioning,
    "security-audit": AraiosSecurityFinding,
    "documents": AraiosDocument,
}

# camelCase → snake_case maps per resource (matching router conventions)
_RESOURCE_FIELD_MAPS: dict[str, dict[str, str]] = {
    "leads": {
        "linkedinUrl": "linkedin_url",
        "lastContact": "last_contact",
        "nextAction": "next_action",
        "messageDraft": "message_draft",
        "approvedMessage": "approved_message",
    },
    "competitors": {"lastUpdated": "last_updated"},
    "clients": {
        "linkedIn": "linked_in",
        "engagementType": "engagement_type",
        "phaseProgress": "phase_progress",
        "healthStatus": "health_status",
        "contractValue": "contract_value",
        "startDate": "start_date",
    },
    "proposals": {
        "leadName": "lead_name",
        "proposalTitle": "proposal_title",
        "sentAt": "sent_at",
    },
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

_MODULE_MUTABLE_FIELDS = {
    "label", "description", "icon", "fields",
    "fields_config", "actions", "secrets", "order", "page_title",
}

_SLUG_RESOURCES = {"documents"}
_DEEP_MERGE_FIELDS = {"pricing", "icp", "work_package"}


# ── Permission dependency ──


def _require_araios_permission(action: str):
    """Return an async dependency that checks AraiosPermission for *action*.

    * admin role  → always allowed
    * agent role  → look up the permission level:
        - "allow"    → pass through
        - "approval" → raise 403 (caller should create an approval instead)
        - "deny" / missing → raise 403
    """

    async def _check(
        user: TokenPayload = Depends(require_auth),
        db: AsyncSession = Depends(get_db),
    ) -> TokenPayload:
        if user.role == "admin":
            return user

        result = await db.execute(
            select(AraiosPermission).where(AraiosPermission.action == action)
        )
        perm = result.scalars().first()
        level = perm.level if perm else "deny"

        if level == "allow":
            return user
        if level == "approval":
            raise HTTPException(
                status_code=403,
                detail=f"Action '{action}' requires admin approval",
            )
        raise HTTPException(
            status_code=403,
            detail=f"Action '{action}' is denied",
        )

    return _check


# ── Helpers ──


def _to_dict(obj: AraiosApproval) -> dict:
    d: dict[str, Any] = {}
    for col in AraiosApproval.__table__.columns:
        val = getattr(obj, col.name)
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        d[col.name] = val
    return d


def _map_payload(resource: str, payload: dict) -> dict:
    """Map camelCase payload keys to snake_case for the resource model."""
    fmap = _RESOURCE_FIELD_MAPS.get(resource, {})
    out: dict[str, Any] = {}
    for k, v in payload.items():
        col = fmap.get(k, k)
        out[col] = v
    return out


def _normalize_module_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _normalize_action_params(body: dict | None) -> dict:
    """Accept both flat action payloads and legacy {params:{...}} wrappers."""
    if not isinstance(body, dict):
        return {}
    nested = body.get("params")
    if isinstance(nested, dict):
        merged = dict(nested)
        for key, value in body.items():
            if key == "params":
                continue
            merged.setdefault(key, value)
        return merged
    return body


async def _seed_module_permissions(name: str, db: AsyncSession) -> None:
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
    result = await db.execute(
        select(AraiosPermission).where(
            AraiosPermission.action.in_([k for k, _ in defaults])
        )
    )
    existing = {p.action for p in result.scalars().all()}
    for action_key, level in defaults:
        if action_key not in existing:
            db.add(AraiosPermission(action=action_key, level=level))
    await db.commit()


# ── Approval executor ──


async def _execute_approval(db: AsyncSession, approval: AraiosApproval) -> None:
    """Execute the operation stored in an approved approval."""
    resource = approval.resource
    action = approval.action
    payload = approval.payload or {}
    resource_id = approval.resource_id

    # ── Module tool action executors ──
    parts = action.split(".")
    if len(parts) == 2:
        result = await db.execute(
            select(AraiosModule).where(AraiosModule.name == parts[0])
        )
        mod = result.scalars().first()
        if mod:
            action_def = next(
                (a for a in (mod.actions or []) if a["id"] == parts[1]), None
            )
            if action_def and action_def.get("code"):
                secrets: dict[str, str] = {}
                sec_result = await db.execute(
                    select(AraiosModuleSecret).where(
                        AraiosModuleSecret.module_name == mod.name
                    )
                )
                for s in sec_result.scalars().all():
                    secrets[s.key] = s.value
                params = _normalize_action_params(
                    payload if isinstance(payload, dict) else {}
                )
                exec_result = await execute_action(
                    action_def["code"], {"params": params, "secrets": secrets}
                )
                if not exec_result.get("ok", True):
                    raise HTTPException(
                        status_code=502,
                        detail=exec_result.get("error", "Action failed"),
                    )
                return

    # ── Module engine executors ──
    action_verb = action.split(".")[-1] if "." in action else action
    if resource == "modules":
        module_name = _normalize_module_name(
            resource_id or payload.get("name")
        )
        if action_verb == "create":
            if not module_name:
                raise HTTPException(
                    status_code=400, detail="Module name is required"
                )
            result = await db.execute(
                select(AraiosModule).where(AraiosModule.name == module_name)
            )
            if result.scalars().first():
                raise HTTPException(
                    status_code=409,
                    detail=f"Module '{module_name}' already exists",
                )
            mod = AraiosModule(
                name=module_name,
                label=payload.get("label", module_name.title()),
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
            await _seed_module_permissions(module_name, db)
            return
        if action_verb == "update":
            if not module_name:
                raise HTTPException(
                    status_code=400,
                    detail="Module name is required for update",
                )
            result = await db.execute(
                select(AraiosModule).where(AraiosModule.name == module_name)
            )
            mod = result.scalars().first()
            if not mod:
                raise HTTPException(
                    status_code=404,
                    detail=f"Module '{module_name}' not found",
                )
            # Apply mutable field updates
            for field in _MODULE_MUTABLE_FIELDS:
                if field in payload:
                    setattr(mod, field, payload[field])
            await db.commit()
            return
        if action_verb == "delete":
            if not module_name:
                raise HTTPException(
                    status_code=400,
                    detail="Module name is required for delete",
                )
            result = await db.execute(
                select(AraiosModule).where(AraiosModule.name == module_name)
            )
            mod = result.scalars().first()
            if not mod:
                raise HTTPException(
                    status_code=404,
                    detail=f"Module '{module_name}' not found",
                )
            await db.delete(mod)
            await db.commit()
        return

    # ── DB CRUD executors ──
    model = _RESOURCE_MODELS.get(resource)  # type: ignore[arg-type]
    if not model:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot execute action '{action}' — unknown resource: {resource}",
        )

    mapped = _map_payload(resource, payload)

    if action_verb == "create":
        create_kwargs = {"id": payload.get("id", araios_gen_id()), **mapped}
        if resource == "documents":
            create_kwargs.setdefault("author", "agent")
            create_kwargs.setdefault("last_edited_by", "agent")
        item = model(**create_kwargs)
        db.add(item)

    elif action_verb == "update":
        lookup_id = "default" if resource == "positioning" else resource_id
        if resource in _SLUG_RESOURCES:
            result = await db.execute(
                select(model).where(model.slug == lookup_id)
            )
        else:
            result = await db.execute(
                select(model).where(model.id == lookup_id)
            )
        item = result.scalars().first()
        if not item:
            raise HTTPException(
                status_code=404, detail=f"{resource} {lookup_id} not found"
            )
        for col, val in mapped.items():
            if hasattr(item, col):
                if (
                    col in _DEEP_MERGE_FIELDS
                    and isinstance(getattr(item, col), dict)
                    and isinstance(val, dict)
                ):
                    merged = {**(getattr(item, col) or {}), **val}
                    setattr(item, col, merged)
                else:
                    setattr(item, col, val)
        if resource in _SLUG_RESOURCES and hasattr(item, "version"):
            item.version += 1
            item.last_edited_by = "admin"

    elif action_verb == "delete":
        if resource in _SLUG_RESOURCES:
            result = await db.execute(
                delete(model).where(model.slug == resource_id)
            )
        else:
            result = await db.execute(
                delete(model).where(model.id == resource_id)
            )
        if result.rowcount == 0:  # type: ignore[union-attr]
            raise HTTPException(
                status_code=404, detail=f"{resource} {resource_id} not found"
            )

    await db.commit()


# ── Routes ──


@router.get(
    "",
    response_model=ApprovalListResponse,
    summary="List approvals",
    description="Returns all approval records. Optionally filter by status (pending, approved, rejected).",
)
async def list_approvals(
    status: str | None = Query(
        None, description="Filter by status: pending, approved, rejected"
    ),
    db: AsyncSession = Depends(get_db),
    user: TokenPayload = Depends(
        _require_araios_permission("approvals.list")
    ),
):
    stmt = select(AraiosApproval).order_by(AraiosApproval.created_at.desc())
    if status:
        stmt = stmt.where(AraiosApproval.status == status)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return {"approvals": [_to_dict(a) for a in rows]}


@router.post(
    "",
    status_code=201,
    response_model=ApprovalOut,
    summary="Create an approval request",
    description=(
        "Manually create an approval request. The agent should prefer using "
        "the dedicated endpoints which auto-create approvals via the permission "
        "middleware. Use this endpoint only for custom actions."
    ),
)
async def create_approval(
    body: ApprovalCreate,
    db: AsyncSession = Depends(get_db),
    user: TokenPayload = Depends(
        _require_araios_permission("approvals.create")
    ),
):
    approval = AraiosApproval(
        id=araios_gen_id(),
        action=body.action,
        resource=body.resource,
        resource_id=body.resourceId,
        description=body.description or "",
        payload=body.payload,
    )
    db.add(approval)
    await db.commit()
    await db.refresh(approval)
    return _to_dict(approval)


@router.post(
    "/{approval_id}/approve",
    response_model=ApprovalOut,
    summary="Approve a pending request",
    description="Admin-only. Executes the stored action and marks the approval as approved.",
)
async def approve(
    approval_id: str,
    db: AsyncSession = Depends(get_db),
    user: TokenPayload = Depends(require_auth),
):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can approve")

    result = await db.execute(
        select(AraiosApproval).where(AraiosApproval.id == approval_id)
    )
    approval = result.scalars().first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Approval is already {approval.status}",
        )

    await _execute_approval(db, approval)

    approval.status = "approved"
    approval.resolved_at = datetime.now(timezone.utc)
    approval.resolved_by = "admin"
    await db.commit()
    await db.refresh(approval)
    return _to_dict(approval)


@router.post(
    "/{approval_id}/reject",
    response_model=ApprovalOut,
    summary="Reject a pending request",
    description="Admin-only. Marks the approval as rejected without executing the action.",
)
async def reject(
    approval_id: str,
    db: AsyncSession = Depends(get_db),
    user: TokenPayload = Depends(require_auth),
):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can reject")

    result = await db.execute(
        select(AraiosApproval).where(AraiosApproval.id == approval_id)
    )
    approval = result.scalars().first()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Approval is already {approval.status}",
        )

    approval.status = "rejected"
    approval.resolved_at = datetime.now(timezone.utc)
    approval.resolved_by = "admin"
    await db.commit()
    await db.refresh(approval)
    return _to_dict(approval)
