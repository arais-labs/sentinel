import json
import sqlite3
from uuid import uuid4

from alembic import command
import pytest

from tests.test_alembic_baselines import migration_config


def test_desktop_migration_preserves_workspaces_and_session_cascades(tmp_path):
    path = tmp_path / "instance.sqlite"
    config = migration_config("instance", path)
    command.upgrade(config, "0007_workspace_name_cache")
    workspace, headless, session, approval = [uuid4().hex for _ in range(4)]
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        for identifier, tools in [(workspace, ["git", "desktop", "python"]), (headless, ["git"])]:
            db.execute(
                "INSERT INTO workspaces(id,name,machine_id,directory,development_tools) VALUES(?,?,?,?,?)",
                (identifier, identifier, uuid4().hex, "/project", json.dumps(tools)),
            )
        db.execute(
            "INSERT INTO sessions(id,user_id,workspace_id) VALUES(?,'test',?)", (session, workspace)
        )
        db.execute(
            "INSERT INTO tool_approvals(id,tool_name,action,session_id,status,expires_at) VALUES(?,'tool','read',?,'pending','2099-01-01')",
            (approval, session),
        )
        db.commit()
        command.upgrade(config, "0008_workspace_desktop")
        expected = [(workspace, "xfce", '["git","python"]'), (headless, "none", '["git"]')]
        for identifier, desktop, tools in expected:
            row = db.execute(
                "SELECT desktop,development_tools FROM workspaces WHERE id=?", (identifier,)
            ).fetchone()
            assert row[0] == desktop
            assert json.loads(row[1]) == json.loads(tools)
        assert db.execute(
            "SELECT workspace_id FROM sessions WHERE id=?", (session,)
        ).fetchone() == (workspace,)
        assert db.execute(
            "SELECT session_id FROM tool_approvals WHERE id=?", (approval,)
        ).fetchone() == (session,)
        db.execute("UPDATE alembic_version SET version_num='0007_workspace_name_cache'")
        db.commit()
        command.upgrade(config, "0008_workspace_desktop")
        assert db.execute("SELECT desktop FROM workspaces WHERE id=?", (workspace,)).fetchone() == (
            "xfce",
        )
        db.execute("UPDATE workspaces SET desktop='weston' WHERE id=?", (workspace,))
        db.commit()
        with pytest.raises(RuntimeError, match="Choose XFCE"):
            command.downgrade(config, "0007_workspace_name_cache")
        db.execute("UPDATE workspaces SET desktop='xfce' WHERE id=?", (workspace,))
        db.commit()
        command.downgrade(config, "0007_workspace_name_cache")
        assert json.loads(
            db.execute(
                "SELECT development_tools FROM workspaces WHERE id=?", (workspace,)
            ).fetchone()[0]
        ) == ["git", "python", "desktop"]
        command.upgrade(config, "0008_workspace_desktop")
    # As in application startup, open the session connection after migrations.
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("DELETE FROM sessions WHERE id=?", (session,))
        assert db.execute("SELECT id FROM tool_approvals WHERE id=?", (approval,)).fetchall() == []
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
