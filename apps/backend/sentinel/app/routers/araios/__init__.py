"""AraiOS routers — mounted into sentinel-backend.

Provides all /api/* and /platform/auth/* routes that the AraiOS frontend
and agents consume.
"""
from fastapi import APIRouter

from app.routers.araios import (
    agent_guide,
    coordination,
    documents,
    manifest,
    modules,
    platform_auth,
    settings,
    tasks,
)

# Main AraiOS API router — mounted at /api
api_router = APIRouter()
api_router.include_router(modules.router, prefix="/modules", tags=["araios-modules"])
api_router.include_router(coordination.router, prefix="/coordination", tags=["araios-coordination"])
api_router.include_router(documents.router, prefix="/documents", tags=["araios-documents"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["araios-tasks"])
api_router.include_router(settings.router, prefix="/settings", tags=["araios-settings"])
api_router.include_router(manifest.router, prefix="/manifest", tags=["araios-manifest"])
api_router.include_router(agent_guide.router, prefix="/agent", tags=["araios-agent-guide"])

# Platform auth router — mounted at /platform/auth
platform_auth_router = platform_auth.router
