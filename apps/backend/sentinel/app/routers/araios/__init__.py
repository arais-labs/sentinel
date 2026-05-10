"""Dynamic module/control-plane routers mounted into sentinel-backend.

Provides the /api/* module/control-plane routes consumed by the Sentinel
modules surface and agents.
"""
from fastapi import APIRouter

from app.routers.araios import (
    agent_guide,
    manifest,
    modules,
    permissions,
    settings,
)

# Module API router mounted at /api.
api_router = APIRouter()
api_router.include_router(modules.router, prefix="/modules", tags=["modules"])
api_router.include_router(permissions.router, prefix="/permissions", tags=["module-permissions"])
api_router.include_router(settings.router, prefix="/settings", tags=["module-settings"])
api_router.include_router(manifest.router, prefix="/manifest", tags=["module-manifest"])
api_router.include_router(agent_guide.router, prefix="/agent", tags=["module-agent-guide"])
