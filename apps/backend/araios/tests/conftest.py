import os
import sys

# Ensure backend/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Override config before anything imports it
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database.database import Base
from app.dependencies import get_db
from app.platform_auth import PlatformIdentity, create_access_token
from main import app


engine = create_engine("sqlite:///./test.db", connect_args={"check_same_thread": False})
TestSession = sessionmaker(bind=engine)


def override_get_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


def _make_headers(sub: str, role: str, agent_id: str | None = None) -> dict:
    token = create_access_token(PlatformIdentity(sub=sub, role=role, agent_id=agent_id))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def setup_db():
    """Create all tables before each test, seed permissions, drop after."""
    from app.database.models import Permission
    from app.permissions import AGENT_PERMISSIONS
    from app.services.auth_settings import ensure_default_auth_settings

    Base.metadata.create_all(bind=engine)

    # Seed default permissions so agent role works correctly in tests
    db = TestSession()
    ensure_default_auth_settings(db)
    for action, level in AGENT_PERMISSIONS.items():
        db.add(Permission(action=action, level=level))
    db.commit()
    db.close()

    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_headers():
    return _make_headers(sub="admin", role="admin", agent_id="admin")


@pytest.fixture
def agent_headers():
    return _make_headers(sub="agent", role="agent", agent_id="agent")


@pytest.fixture
def esprit_headers():
    return _make_headers(sub="esprit", role="agent", agent_id="esprit")


@pytest.fixture
def ronnor_headers():
    return _make_headers(sub="ronnor", role="agent", agent_id="ronnor")
