import os

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("DEV_TOKEN", "sentinel-dev-token")

from app.config import settings
from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models.system import SystemSetting
from app.services.onboarding_defaults import DEFAULT_SYSTEM_PROMPT, build_system_prompt
from tests.fake_db import FakeDB


def _auth_headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_onboarding_prompt_when_user_skips_everything():
    fake_db = FakeDB()
    old_prompt = settings.default_system_prompt

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        headers = _auth_headers(client)

        complete = client.post("/api/v1/onboarding/complete", json={}, headers=headers)
        assert complete.status_code == 200
        assert complete.json()["completed"] is True

        persisted_prompt = next(
            (
                row.value
                for row in fake_db.storage[SystemSetting]
                if row.key == "default_system_prompt"
            ),
            None,
        )
        assert persisted_prompt == DEFAULT_SYSTEM_PROMPT
        assert settings.default_system_prompt == DEFAULT_SYSTEM_PROMPT
        print(f"SKIP_EVERYTHING_PROMPT: {persisted_prompt}")
    finally:
        settings.default_system_prompt = old_prompt
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_onboarding_prompt_when_user_inputs_everything():
    fake_db = FakeDB()
    old_prompt = settings.default_system_prompt

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        headers = _auth_headers(client)

        expected_prompt = build_system_prompt(
            agent_name="Atlas",
            agent_role=(
                "You are a senior product-and-engineering copilot. Drive execution proactively and keep plans "
                "actionable."
            ),
            agent_personality="Direct, pragmatic, and highly solution-oriented.",
        )
        complete = client.post(
            "/api/v1/onboarding/complete",
            json={"system_prompt": expected_prompt},
            headers=headers,
        )
        assert complete.status_code == 200
        assert complete.json()["completed"] is True

        persisted_prompt = next(
            (
                row.value
                for row in fake_db.storage[SystemSetting]
                if row.key == "default_system_prompt"
            ),
            None,
        )
        assert persisted_prompt == expected_prompt
        assert settings.default_system_prompt == expected_prompt
        print(f"FULL_INPUT_PROMPT: {persisted_prompt}")
    finally:
        settings.default_system_prompt = old_prompt
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_onboarding_complete_creates_default_system_memories_and_prompt():
    fake_db = FakeDB()
    old_prompt = settings.default_system_prompt

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        headers = _auth_headers(client)

        complete = client.post("/api/v1/onboarding/complete", json={}, headers=headers)
        assert complete.status_code == 200
        assert complete.json()["completed"] is True

        status = client.get("/api/v1/onboarding/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["completed"] is True

        roots = client.get("/api/v1/memory/roots?category=core", headers=headers)
        assert roots.status_code == 200
        items = roots.json()["items"]

        agent_identity = next((item for item in items if item["title"] == "Agent Identity"), None)
        assert agent_identity is not None
        assert "You are Sentinel." in agent_identity["content"]
        assert agent_identity["pinned"] is True
        assert agent_identity["importance"] == 100

        user_profile = next((item for item in items if item["title"] == "User Profile"), None)
        assert user_profile is not None
        assert "ask the user for context" in user_profile["content"]
        assert user_profile["pinned"] is True
        assert user_profile["importance"] == 90

        assert settings.default_system_prompt.startswith("You are Sentinel")
        persisted_prompt = next(
            (
                row.value
                for row in fake_db.storage[SystemSetting]
                if row.key == "default_system_prompt"
            ),
            None,
        )
        assert persisted_prompt is not None
        assert persisted_prompt.startswith("You are Sentinel")
    finally:
        settings.default_system_prompt = old_prompt
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_onboarding_complete_keeps_existing_agent_identity_memory():
    fake_db = FakeDB()
    old_prompt = settings.default_system_prompt

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        headers = _auth_headers(client)

        custom_agent_content = "You are Atlas. Keep responses terse."
        create_agent = client.post(
            "/api/v1/memory",
            json={
                "content": custom_agent_content,
                "title": "Agent Identity",
                "category": "core",
                "importance": 100,
                "pinned": True,
            },
            headers=headers,
        )
        assert create_agent.status_code == 200

        custom_prompt = "You are Atlas. Be terse."
        complete = client.post(
            "/api/v1/onboarding/complete",
            json={"system_prompt": custom_prompt},
            headers=headers,
        )
        assert complete.status_code == 200

        roots = client.get("/api/v1/memory/roots?category=core", headers=headers)
        assert roots.status_code == 200
        items = roots.json()["items"]
        agent_identity_items = [item for item in items if item["title"] == "Agent Identity"]
        assert len(agent_identity_items) == 1
        assert agent_identity_items[0]["content"] == custom_agent_content

        user_profile = next((item for item in items if item["title"] == "User Profile"), None)
        assert user_profile is not None
        assert "ask the user for context" in user_profile["content"]

        assert settings.default_system_prompt == custom_prompt
    finally:
        settings.default_system_prompt = old_prompt
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_onboarding_araios_integration_configure_and_disable():
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

    try:
        client = TestClient(app)
        headers = _auth_headers(client)

        configure = client.post(
            "/api/v1/onboarding/araios",
            json={
                "enabled": True,
                "base_url": "http://araios-backend:9000",
                "agent_api_key": "sk-arais-agent-test",
            },
            headers=headers,
        )
        assert configure.status_code == 200
        assert configure.json()["configured"] is True

        status = client.get("/api/v1/onboarding/araios", headers=headers)
        assert status.status_code == 200
        assert status.json()["configured"] is True
        assert status.json()["base_url"] == "http://araios-backend:9000"
        assert status.json()["masked_agent_api_key"] is not None

        disable = client.post(
            "/api/v1/onboarding/araios",
            json={"enabled": False},
            headers=headers,
        )
        assert disable.status_code == 200
        assert disable.json()["configured"] is False

        status_after = client.get("/api/v1/onboarding/araios", headers=headers)
        assert status_after.status_code == 200
        assert status_after.json()["configured"] is False
        assert status_after.json()["base_url"] is None
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_onboarding_araios_integration_allows_base_url_update_without_new_key():
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

    try:
        client = TestClient(app)
        headers = _auth_headers(client)

        first = client.post(
            "/api/v1/onboarding/araios",
            json={
                "enabled": True,
                "base_url": "http://araios-backend:9000",
                "agent_api_key": "sk-arais-agent-test",
            },
            headers=headers,
        )
        assert first.status_code == 200

        rotate_url = client.post(
            "/api/v1/onboarding/araios",
            json={
                "enabled": True,
                "base_url": "https://new-araios.example.com",
            },
            headers=headers,
        )
        assert rotate_url.status_code == 200
        assert rotate_url.json()["configured"] is True

        status = client.get("/api/v1/onboarding/araios", headers=headers)
        assert status.status_code == 200
        assert status.json()["configured"] is True
        assert status.json()["base_url"] == "https://new-araios.example.com"
        assert status.json()["masked_agent_api_key"] is not None
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
