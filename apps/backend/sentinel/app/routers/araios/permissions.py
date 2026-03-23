from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_admin, require_auth
from app.models.araios import AraiosModule, AraiosPermission
from app.schemas.araios import PermissionListResponse, PermissionOut, PermissionUpdate
from app.services.araios.dynamic_modules import (
    VALID_PERMISSION_LEVELS,
    build_dynamic_module_permission_levels,
    normalize_dynamic_module_actions,
)
from app.services.araios.module_types import ActionDefinition
from app.services.araios.permissions import AGENT_PERMISSIONS
from app.services.araios.system_modules import get_system_modules

logger = logging.getLogger(__name__)

router = APIRouter(tags=["araios-permissions"])


@router.get("", response_model=PermissionListResponse)
async def list_permissions(
    _user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    permissions = await _resolved_permissions(db)
    return PermissionListResponse(
        permissions=[
            PermissionOut(action=action, level=level)
            for action, level in sorted(permissions.items(), key=_permission_sort_key)
        ]
    )


@router.patch("/{action:path}", response_model=PermissionOut)
async def update_permission(
    action: str,
    body: PermissionUpdate,
    _admin: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    level = body.level.strip().lower()
    if level not in VALID_PERMISSION_LEVELS:
        raise HTTPException(status_code=400, detail="level must be allow, approval, or deny")

    known_permissions = await _resolved_permissions(db)
    if action not in known_permissions:
        raise HTTPException(status_code=404, detail=f"Unknown permission action '{action}'")

    result = await db.execute(select(AraiosPermission).where(AraiosPermission.action == action))
    perm = result.scalars().first()
    if perm is None:
        perm = AraiosPermission(action=action, level=level)
        db.add(perm)
    else:
        perm.level = level

    await db.commit()
    await db.refresh(perm)
    return PermissionOut(action=perm.action, level=perm.level)


async def _resolved_permissions(db: AsyncSession) -> dict[str, str]:
    result = await db.execute(select(AraiosPermission))
    rows = result.scalars().all()
    stored_levels = {
        str(row.action): str(row.level).strip().lower()
        for row in rows
        if isinstance(getattr(row, "action", None), str) and isinstance(getattr(row, "level", None), str)
    }

    resolved = dict(_system_permission_defaults())

    modules_result = await db.execute(
        select(AraiosModule)
        .where(AraiosModule.system.is_(False))
        .order_by(AraiosModule.order, AraiosModule.name)
    )
    for module in modules_result.scalars().all():
        module_prefix = f"{module.name}."
        module_existing = {
            action[len(module_prefix):]: level
            for action, level in stored_levels.items()
            if action.startswith(module_prefix)
        }
        try:
            levels = build_dynamic_module_permission_levels(
                module_name=module.name,
                actions=normalize_dynamic_module_actions(list(module.actions or [])),
                existing=module_existing,
            )
            for command, level in levels.items():
                resolved[f"{module.name}.{command}"] = level
        except Exception:
            logger.exception("permissions_skip_invalid_dynamic_module module=%s", module.name)
            for action, level in module_existing.items():
                if level in VALID_PERMISSION_LEVELS:
                    resolved[f"{module.name}.{action}"] = level

    for action, level in stored_levels.items():
        if level in VALID_PERMISSION_LEVELS:
            resolved[action] = level

    return resolved


def _system_permission_defaults() -> dict[str, str]:
    permissions = dict(AGENT_PERMISSIONS)
    for module in get_system_modules():
        for action in module.actions or []:
            if not action.handler:
                continue
            permissions.setdefault(
                f"{module.name}.{action.id}",
                _default_permission_level(action),
            )
    return permissions


def _default_permission_level(action: ActionDefinition) -> str:
    if action.permission_default in VALID_PERMISSION_LEVELS:
        return action.permission_default
    return "approval" if action.approval else "allow"


def _permission_sort_key(item: tuple[str, str]) -> tuple[str, str]:
    action, _level = item
    namespace, sep, suffix = action.partition(".")
    return (namespace, suffix if sep else action)
