from app.models import Base
from app.models.manager import ManagerBase


def test_manager_metadata_does_not_include_app_tables():
    manager_tables = set(ManagerBase.metadata.tables)
    app_tables = set(Base.metadata.tables)

    assert "instances" in manager_tables
    assert "manager_settings" in manager_tables
    assert "manager_revoked_tokens" in manager_tables
    assert "revoked_tokens" not in app_tables
    assert "sessions" in app_tables
    assert "messages" in app_tables
    assert manager_tables.isdisjoint(app_tables)
