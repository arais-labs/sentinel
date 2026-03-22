"""AraiOS Manifest router — async SQLAlchemy.

Returns the full module manifest including registered endpoints and auth info.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.models.araios import AraiosModule, AraiosPermission
from app.models.system import SystemSetting

router = APIRouter(tags=["araios-manifest"])


# ── Helpers ──


def _module_dict(m: AraiosModule) -> dict[str, Any]:
    return {
        "name": m.name,
        "label": m.label,
        "description": m.description,
        "icon": m.icon,
        "type": m.type,
        "fields": m.fields,
        "listConfig": m.list_config,
        "actions": m.actions,
        "secrets": m.secrets,
        "isSystem": m.is_system,
        "order": m.order,
        "createdAt": m.created_at.isoformat() if m.created_at else None,
        "updatedAt": m.updated_at.isoformat() if m.updated_at else None,
    }


# ── Routes ──


@router.get("")
async def get_manifest(
    _user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    # Modules
    mod_result = await db.execute(select(AraiosModule).order_by(AraiosModule.order))
    modules = [_module_dict(m) for m in mod_result.scalars().all()]

    # Permissions
    perm_result = await db.execute(select(AraiosPermission))
    permissions = {p.action: p.level for p in perm_result.scalars().all()}

    # System settings
    settings_result = await db.execute(select(SystemSetting))
    system_settings = {s.key: s.value for s in settings_result.scalars().all()}

    # Core endpoints manifest
    endpoints = [
        {"method": "GET", "path": "/api/v1/araios/manifest", "description": "This manifest"},
        {"method": "GET", "path": "/api/v1/araios/agent-guide", "description": "Full agent guide"},
        {"method": "GET", "path": "/api/v1/araios/permissions", "description": "List permissions"},
        {"method": "PATCH", "path": "/api/v1/araios/permissions/{action}", "description": "Update permission"},
        {"method": "GET", "path": "/api/v1/araios/coordination", "description": "List coordination messages"},
        {"method": "POST", "path": "/api/v1/araios/coordination", "description": "Send coordination message"},
        {"method": "GET", "path": "/api/v1/araios/documents", "description": "List documents"},
        {"method": "GET", "path": "/api/v1/araios/documents/{slug}", "description": "Get document"},
        {"method": "POST", "path": "/api/v1/araios/documents", "description": "Create document"},
        {"method": "PUT", "path": "/api/v1/araios/documents/{slug}", "description": "Update document"},
        {"method": "DELETE", "path": "/api/v1/araios/documents/{slug}", "description": "Delete document"},
        {"method": "GET", "path": "/api/v1/araios/tasks", "description": "List tasks"},
        {"method": "POST", "path": "/api/v1/araios/tasks", "description": "Create task"},
        {"method": "PATCH", "path": "/api/v1/araios/tasks/{task_id}", "description": "Update task"},
        {"method": "DELETE", "path": "/api/v1/araios/tasks/{task_id}", "description": "Delete task"},
        {"method": "GET", "path": "/api/v1/araios/settings", "description": "List system settings"},
        {"method": "PUT", "path": "/api/v1/araios/settings/{key}", "description": "Set system setting"},
    ]

    return {
        "version": "1.0",
        "modules": modules,
        "endpoints": endpoints,
        "permissions": permissions,
        "systemSettings": system_settings,
        "auth": {
            "type": "bearer",
            "header": "Authorization",
            "tokenEndpoint": "/api/v1/auth/login",
        },
    }
