from __future__ import annotations

import os

# Shared pytest defaults so local test runs do not depend on shell-exported env vars.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("DATA_ENCRYPTION_KEY", "test-data-key-with-32-bytes-minimum")
os.environ.setdefault("SENTINEL_AUTH_USERNAME", "admin")
os.environ.setdefault("SENTINEL_AUTH_PASSWORD", "admin")
os.environ.setdefault("TOOL_FILE_READ_BASE_DIR", "/tmp")

import pytest


@pytest.fixture(autouse=True)
def _fake_runtime_manager_db(monkeypatch):
    # The WS connect path probes runtime config via ssh_runtime's own module-level
    # ManagerSessionLocal, which bypasses dependency overrides and would open a real
    # Postgres socket. Point it at an empty fake so the probe resolves "unconfigured".
    from app.services.runtime import ssh_runtime
    from tests.fake_db import FakeDB
    from tests.helpers import FakeSessionFactory

    monkeypatch.setattr(ssh_runtime, "ManagerSessionLocal", FakeSessionFactory(FakeDB()))
