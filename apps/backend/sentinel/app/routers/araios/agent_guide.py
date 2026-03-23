"""AraiOS Agent Guide router — async SQLAlchemy.

Returns a comprehensive guide for AI agents interacting with the AraiOS platform.
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

router = APIRouter(tags=["araios-agent-guide"])


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
            "name": "AraiOS",
            "description": (
                "AraiOS is the operating-system layer for ARAIS — it provides modules, "
                "coordination, documents, tasks, and settings to AI agents "
                "and human operators."
            ),
            "settings": system_settings,
        },
        "authentication": {
            "method": "Bearer token in Authorization header",
            "tokenEndpoint": "/api/v1/auth/login",
            "example": "Authorization: Bearer <token>",
            "roles": {
                "admin": "Full access to all endpoints and settings",
                "agent": "Access to the AraiOS application surface",
            },
        },
        "endpoints": {
            "manifest": {
                "GET /api/v1/araios/manifest": "Full system manifest with modules and endpoints",
            },
            "coordination": {
                "GET /api/v1/araios/coordination": "List coordination messages (query: limit)",
                "POST /api/v1/araios/coordination": "Send a coordination message",
            },
            "documents": {
                "GET /api/v1/araios/documents": "List documents (query: tag)",
                "GET /api/v1/araios/documents/{slug}": "Get full document by slug",
                "POST /api/v1/araios/documents": "Create a new document",
                "PUT /api/v1/araios/documents/{slug}": "Update document (If-Match for optimistic locking)",
                "DELETE /api/v1/araios/documents/{slug}": "Delete a document",
            },
            "tasks": {
                "GET /api/v1/araios/tasks": "List tasks (query: client, status, owner)",
                "POST /api/v1/araios/tasks": "Create a new task",
                "PATCH /api/v1/araios/tasks/{task_id}": "Update a task",
                "DELETE /api/v1/araios/tasks/{task_id}": "Delete a task",
            },
            "settings": {
                "GET /api/v1/araios/settings": "List all system settings",
                "PUT /api/v1/araios/settings/{key}": "Set a system setting (admin only)",
            },
        },
        "moduleCreation": {
            "description": (
                "Modules are dynamic data containers registered in the system. "
                "Each module defines fields, list configuration, and optional actions."
            ),
            "steps": [
                "POST /api/v1/araios/modules with name, label, fields, etc.",
                "Records are stored via POST /api/v1/araios/modules/{name}/records",
                "Module metadata can be updated via PUT /api/v1/araios/modules/{name}",
            ],
            "fieldTypes": [
                "string", "text", "number", "boolean", "date", "select", "json",
            ],
        },
        "moduleCatalog": module_catalog,
    }

    return guide
