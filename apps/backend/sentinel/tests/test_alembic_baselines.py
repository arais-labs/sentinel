from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.models import Base
from app.models.manager import ManagerBase

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def migration_config(kind, database):
    config = Config(str(BACKEND_ROOT / f"alembic.{kind}.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "db/alembic" / kind))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database}")
    return config


@pytest.mark.parametrize(
    "kind,metadata", [("manager", ManagerBase.metadata), ("instance", Base.metadata)]
)
def test_fresh_schema_matches_models(tmp_path, kind, metadata):
    path = tmp_path / f"{kind}.sqlite"
    config = migration_config(kind, path)
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [f"0001_{kind}_initial"]
    assert len(list(scripts.walk_revisions())) == 1
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            expected = metadata
            assert not compare_metadata(
                MigrationContext.configure(
                    connection,
                    opts={
                        "compare_server_default": True,
                        "include_object": lambda obj, name, kind, reflected, compare_to: not (
                            kind == "table" and name.startswith("memories_fts")
                        ),
                    },
                ),
                expected,
            )
            inspector = inspect(connection)
            assert {
                name for name in inspector.get_table_names() if not name.startswith("memories_fts")
            } == set(expected.tables) | {"alembic_version"}
            for table in expected.tables.values():
                assert set(inspector.get_pk_constraint(table.name)["constrained_columns"]) == {
                    c.name for c in table.primary_key
                }
                assert {i["name"] for i in inspector.get_indexes(table.name)} == {
                    i.name for i in table.indexes
                }
            if kind == "instance":
                columns = {c["name"] for c in inspector.get_columns("workspaces")}
                assert {"distribution", "development_tools"} <= columns
                assert "session_action_grants" in inspector.get_table_names()
    finally:
        engine.dispose()
