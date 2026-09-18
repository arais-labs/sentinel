import sqlite3
from uuid import uuid4

import pytest
from alembic import command

from tests.test_alembic_baselines import migration_config


def snapshot(db):
    return {
        name: db.execute(f'SELECT * FROM "{name}" ORDER BY 1').fetchall()
        for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
        )
    }


def test_upgrade_preserves_all_rows_session_bindings_and_indexes(tmp_path):
    path = tmp_path / "instance.sqlite"
    config = migration_config("instance", path)
    command.upgrade(config, "0006_approval_session_cascade")
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        workspace, session, approval, machine = [uuid4().hex for _ in range(4)]
        db.execute(
            "INSERT INTO workspaces(id,name,machine_id,directory) VALUES(?,?,?,?)",
            (workspace, "Project", machine, "/project"),
        )
        db.execute(
            "INSERT INTO sessions(id,user_id,workspace_id) VALUES(?,'test',?)", (session, workspace)
        )
        db.execute(
            "INSERT INTO messages(id,session_id,role,content) VALUES(?,?,'user','keep')",
            (uuid4().hex, session),
        )
        db.execute(
            "INSERT INTO tool_approvals(id,tool_name,action,session_id,status,expires_at) VALUES(?,'tool','read',?,'pending','2099-01-01')",
            (approval, session),
        )
        db.commit()
        before = snapshot(db)
        indexes = db.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='workspaces' AND sql IS NOT NULL"
        ).fetchall()
        command.upgrade(config, "head")
        assert snapshot(db) == before
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert (
            db.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='workspaces' AND sql IS NOT NULL"
            ).fetchall()
            == indexes
        )
        # Re-run with the old revision marker, simulating a lost completion write.
        db.execute("UPDATE alembic_version SET version_num='0006_approval_session_cascade'")
        db.commit()
        command.upgrade(config, "head")
        assert snapshot(db) == before
        command.downgrade(config, "0006_approval_session_cascade")
        assert snapshot(db) == before
        command.upgrade(config, "head")
        db.execute(
            "INSERT INTO workspaces(id,name,machine_id,directory) VALUES(?,'Project',?,'/other')",
            (uuid4().hex, machine),
        )
        db.commit()
        duplicate_names = snapshot(db)
        with pytest.raises(Exception, match="UNIQUE"):
            command.downgrade(config, "0006_approval_session_cascade")
        assert snapshot(db) == duplicate_names
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
