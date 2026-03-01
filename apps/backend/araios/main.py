import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.middleware.auth import get_agent_id, get_role, get_subject
from app.platform_auth import hash_api_key

from app.database.database import init_db
from app.routers import (
    agent_guide as agent_guide_router,
    health,
    approvals,
    permissions,
    coordination,
    documents,
    github_tasks,
)
from app.routers import modules as modules_router
from app.routers import settings as settings_router
from app.routers import manifest as manifest_router
from app.routers import platform_auth as platform_auth_router
from config import (
    PLATFORM_BOOTSTRAP_AGENT_ID,
    PLATFORM_BOOTSTRAP_API_KEY,
    PLATFORM_BOOTSTRAP_LABEL,
    PLATFORM_BOOTSTRAP_ROLE,
    PLATFORM_BOOTSTRAP_SUB,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database.database import SessionLocal
    from app.database.models import Permission, PlatformApiKey, Setting
    from sqlalchemy import text

    init_db()
    db = SessionLocal()
    try:
        # Add description column if missing (idempotent migration)
        try:
            db.execute(text("ALTER TABLE modules ADD COLUMN IF NOT EXISTS description TEXT DEFAULT ''"))
            db.commit()
        except Exception:
            db.rollback()
        # Seed default settings
        if not db.query(Setting).filter(Setting.key == "manifest_base_url").first():
            db.add(Setting(key="manifest_base_url", value="http://localhost:9000"))
            db.commit()
        # Seed permissions
        for action, level in [("manifest.read", "allow"), ("settings.manage", "allow")]:
            if not db.query(Permission).filter(Permission.action == action).first():
                db.add(Permission(action=action, level=level))
        db.commit()
        if not db.query(PlatformApiKey).first():
            if not PLATFORM_BOOTSTRAP_API_KEY:
                raise RuntimeError(
                    "No platform API keys found and PLATFORM_BOOTSTRAP_API_KEY is empty. "
                    "Set PLATFORM_BOOTSTRAP_API_KEY to initialize auth."
                )
            db.add(
                PlatformApiKey(
                    label=PLATFORM_BOOTSTRAP_LABEL,
                    role=PLATFORM_BOOTSTRAP_ROLE,
                    subject=PLATFORM_BOOTSTRAP_SUB,
                    agent_id=PLATFORM_BOOTSTRAP_AGENT_ID,
                    key_hash=hash_api_key(PLATFORM_BOOTSTRAP_API_KEY),
                    is_active=True,
                )
            )
            db.commit()
        # Fix permissions for tool modules: keep only permissions that match current action IDs
        from app.database.models import Module
        from app.routers.modules import _seed_module_permissions
        for mod in db.query(Module).filter(Module.type == "tool").all():
            valid_actions = {a["id"] for a in (mod.actions or [])}
            stale = [
                p for p in db.query(Permission).filter(
                    Permission.action.like(f"{mod.name}.%")
                ).all()
                if p.action.split(".", 1)[-1] not in valid_actions
            ]
            for p in stale:
                db.delete(p)
            db.commit()
            _seed_module_permissions(mod.name, db)
    finally:
        db.close()
    yield


app = FastAPI(title="araiOS", lifespan=lifespan, redirect_slashes=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# System API routers (custom pages — not migrated)
app.include_router(health.router)
app.include_router(approvals.router, prefix="/api/approvals", tags=["approvals"])
app.include_router(permissions.router, prefix="/api/permissions", tags=["permissions"])
app.include_router(coordination.router, prefix="/api/coordination", tags=["coordination"])
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(github_tasks.router, prefix="/api/github-tasks", tags=["github-tasks"])
# Module engine router
app.include_router(modules_router.router, prefix="/api/modules", tags=["modules"])

# Settings, manifest and agent guide routers
app.include_router(settings_router.router,    prefix="/api/settings", tags=["settings"])
app.include_router(manifest_router.router,    prefix="/api/manifest", tags=["manifest"])
app.include_router(agent_guide_router.router, prefix="/api/agent",    tags=["agent"])
app.include_router(platform_auth_router.router, prefix="/platform/auth", tags=["platform-auth"])

@app.get("/api/verify")
async def verify_token(
    role: str = Depends(get_role),
    agent_id: str = Depends(get_agent_id),
    subject: str = Depends(get_subject),
):
    return {"user_id": subject, "sub": subject, "role": role, "agent_id": agent_id}


# Serve built React SPA from static/ directory (if it exists)
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    _assets_dir = os.path.join(_static_dir, "assets")
    if os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        # Serve static file if it exists, otherwise index.html
        file_path = os.path.join(_static_dir, path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(_static_dir, "index.html"))
