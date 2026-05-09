import logging

from fastapi.testclient import TestClient

from app.main import app
from app.logging_context import (
    clear_all_runtime_logger_overrides,
    configure_logging,
    get_logging_config_snapshot,
)
from app.middleware.auth import TokenPayload, require_auth


def test_logging_defaults_silence_httpx():
    clear_all_runtime_logger_overrides()
    configure_logging()

    snapshot = get_logging_config_snapshot()

    assert snapshot["default_logger_levels"]["httpx"] == "WARNING"
    assert snapshot["effective_logger_levels"]["httpx"] == "WARNING"


def test_logging_override_round_trip():
    clear_all_runtime_logger_overrides()
    configure_logging()

    from app.logging_context import clear_runtime_logger_override, set_runtime_logger_override

    snapshot = set_runtime_logger_override("app.services.runtime.qemu", "DEBUG")
    assert snapshot["runtime_overrides"]["app.services.runtime.qemu"] == "DEBUG"
    assert logging.getLogger("app.services.runtime.qemu").getEffectiveLevel() == logging.DEBUG

    snapshot = clear_runtime_logger_override("app.services.runtime.qemu")
    assert "app.services.runtime.qemu" not in snapshot["runtime_overrides"]


def test_logging_routes_allow_runtime_override():
    clear_all_runtime_logger_overrides()
    configure_logging()

    from app import main as app_main

    async def _noop_init_db():
        return None

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    try:
        async def _fake_auth() -> TokenPayload:
            return TokenPayload(
                sub="dev-admin",
                role="admin",
                agent_id=None,
                exp=1999999999,
                iat=1771810000,
                jti="test-jti",
                token_type="access",
            )

        app.dependency_overrides[require_auth] = _fake_auth
        client = TestClient(app)
        get_resp = client.get("/api/v1/settings/logging")
        assert get_resp.status_code == 200
        assert get_resp.json()["default_logger_levels"]["httpx"] == "WARNING"

        set_resp = client.post(
            "/api/v1/settings/logging/levels",
            json={"logger": "app.routers.ws", "level": "DEBUG"},
        )
        assert set_resp.status_code == 200
        assert set_resp.json()["runtime_overrides"]["app.routers.ws"] == "DEBUG"

        clear_resp = client.delete(
            "/api/v1/settings/logging/levels",
            params={"logger": "app.routers.ws"},
        )
        assert clear_resp.status_code == 200
        assert "app.routers.ws" not in clear_resp.json()["runtime_overrides"]

        reset_resp = client.post("/api/v1/settings/logging/reset")
        assert reset_resp.status_code == 200
        assert reset_resp.json()["runtime_overrides"] == {}
    finally:
        app_main.init_db = old_init
        app.dependency_overrides.clear()
        clear_all_runtime_logger_overrides()
