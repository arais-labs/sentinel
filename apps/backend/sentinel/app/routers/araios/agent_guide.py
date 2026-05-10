"""Module agent guide router — async SQLAlchemy.

Returns a comprehensive guide for AI agents interacting with modules.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.models.araios import AraiosModule
from app.models.system import SystemSetting

router = APIRouter(tags=["module-agent-guide"])


@router.get("")
async def get_agent_guide(
    _user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    # Fetch modules for the catalog
    mod_result = await db.execute(select(AraiosModule).order_by(AraiosModule.order))
    modules = mod_result.scalars().all()

    # Fetch system settings
    settings_result = await db.execute(select(SystemSetting))
    system_settings = {s.key: s.value for s in settings_result.scalars().all()}

    module_catalog = [
        {
            "name": m.name,
            "label": m.label,
            "description": m.description,
            "icon": m.icon,
            "has_fields": bool(m.fields),
            "has_actions": bool(m.actions),
            "has_page": bool(m.page_title),
            "field_count": len(m.fields) if m.fields else 0,
            "action_count": len(m.actions) if m.actions else 0,
        }
        for m in modules
    ]

    guide: dict[str, Any] = {
        "system": {
            "name": "Sentinel Modules",
            "description": (
                "Sentinel modules provide data stores, callable actions, coordination, "
                "documents, tasks, and settings to AI agents and human operators."
            ),
            "settings": system_settings,
        },
        "authentication": {
            "method": "Bearer token in Authorization header",
            "tokenEndpoint": "/api/v1/auth/login",
            "example": "Authorization: Bearer <token>",
            "roles": {
                "admin": "Full access to all endpoints and settings",
                "agent": "Access to the module application surface",
            },
        },
        "endpoints": {
            "manifest": {
                "GET /api/manifest": "Full system manifest with modules and endpoints",
            },
            "settings": {
                "GET /api/settings": "List all system settings",
                "PUT /api/settings/{key}": "Set a system setting (admin only)",
            },
        },
        "moduleCreation": {
            "description": (
                "Modules are dynamic data containers registered in the system. "
                "Each module defines fields, list configuration, and optional actions."
            ),
            "steps": [
                "POST /api/modules with name, label, fields, etc.",
                "Records are stored via POST /api/modules/{name}/records",
                "Module metadata can be updated via PATCH /api/modules/{name}",
            ],
            "fieldTypes": [
                "string", "text", "number", "boolean", "date", "select", "json",
            ],
        },
        "moduleCatalog": module_catalog,
    }

    return guide
