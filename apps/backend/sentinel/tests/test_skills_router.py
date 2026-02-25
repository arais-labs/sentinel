from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("DEV_TOKEN", "sentinel-dev-token")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.services.skills import SkillRegistry, load_builtin_skills
from tests.fake_db import FakeDB


def _builtin_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "app" / "services" / "skills" / "builtin"


def _build_registry() -> SkillRegistry:
    registry = SkillRegistry()
    for skill in load_builtin_skills(_builtin_dir()):
        registry.register(skill)
    return registry


def test_skills_routes_list_detail_and_toggle():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    old_registry = None
    try:
        client = TestClient(app)
        old_registry = getattr(app.state, "skill_registry", None)
        app.state.skill_registry = _build_registry()

        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        listed = client.get("/api/v1/skills", headers=headers)
        assert listed.status_code == 200
        names = {item["name"] for item in listed.json()["items"]}
        assert names == {"code-assistant", "research", "operator"}

        detail = client.get("/api/v1/skills/code-assistant", headers=headers)
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["name"] == "code-assistant"
        assert "Code Assistant" in payload["system_prompt_injection"]
        assert payload["enabled"] is True

        disabled = client.post("/api/v1/skills/code-assistant/disable", headers=headers)
        assert disabled.status_code == 200
        assert disabled.json() == {"name": "code-assistant", "enabled": False}

        detail_after_disable = client.get("/api/v1/skills/code-assistant", headers=headers)
        assert detail_after_disable.status_code == 200
        assert detail_after_disable.json()["enabled"] is False

        enabled = client.post("/api/v1/skills/code-assistant/enable", headers=headers)
        assert enabled.status_code == 200
        assert enabled.json() == {"name": "code-assistant", "enabled": True}

        detail_after_enable = client.get("/api/v1/skills/code-assistant", headers=headers)
        assert detail_after_enable.status_code == 200
        assert detail_after_enable.json()["enabled"] is True
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
        if old_registry is not None:
            app.state.skill_registry = old_registry
