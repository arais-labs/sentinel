import os
import sys

# Ensure backend/ is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Override config before anything imports it
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["AGENT_TOKEN"] = "test-agent-token"
os.environ["ESPRIT_AGENT_TOKEN"] = "test-esprit-token"
os.environ["RONNOR_AGENT_TOKEN"] = "test-ronnor-token"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database.database import Base
from app.dependencies import get_db
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


@pytest.fixture(autouse=True)
def setup_db():
    """Create all tables before each test, seed permissions, drop after."""
    from app.database.models import Permission
    from app.permissions import AGENT_PERMISSIONS

    Base.metadata.create_all(bind=engine)

    # Seed default permissions so agent role works correctly in tests
    db = TestSession()
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
    return {"Authorization": "Bearer test-admin-token"}


@pytest.fixture
def agent_headers():
    return {"Authorization": "Bearer test-agent-token"}


@pytest.fixture
def esprit_headers():
    return {"Authorization": "Bearer test-esprit-token"}


@pytest.fixture
def ronnor_headers():
    return {"Authorization": "Bearer test-ronnor-token"}
