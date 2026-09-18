import sqlite3
from uuid import uuid4

import pytest
from alembic import command

from tests.test_alembic_baselines import migration_config


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "instance.sqlite"
    config = migration_config("instance", path)
    command.upgrade(config, "0005_mcp_servers")
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=ON")
    yield config, db
    db.close()


def session(db):
    sid = uuid4().hex
    db.execute("INSERT INTO sessions(id, user_id) VALUES (?, 'test')", (sid,))
    return sid


def approval(db, sid, status="pending"):
    aid = uuid4().hex
    db.execute(
        "INSERT INTO tool_approvals "
        "(id, tool_name, action, session_id, status, expires_at, payload_json, result_json) "
        "VALUES (?, 'test', 'test.read', ?, ?, '2099-01-01', '{\"input\":1}', '{\"output\":2}')",
        (aid, sid, status),
    )
    return aid


def rows(db):
    return dict((row[0], row) for row in db.execute("SELECT * FROM tool_approvals ORDER BY id"))


def test_upgrade_removes_only_orphans_and_preserves_valid_rows_and_indexes(database):
    config, db = database
    sid = session(db)
    kept = []
    removed = []
    for status in ("pending", "approved", "rejected", "cancelled", "timed_out"):
        kept.extend([approval(db, sid, status), approval(db, None, status)])
        removed.append(approval(db, uuid4().hex, status))
    db.commit()
    before = rows(db)
    index_query = (
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='index' AND tbl_name='tool_approvals' ORDER BY name"
    )
    indexes = db.execute(index_query).fetchall()

    command.upgrade(config, "head")

    assert rows(db) == {aid: before[aid] for aid in kept}
    assert not set(removed) & rows(db).keys()
    assert db.execute(index_query).fetchall() == indexes
    fk = db.execute("PRAGMA foreign_key_list(tool_approvals)").fetchall()
    assert len(fk) == 1
    assert fk[0][2:5] == ("sessions", "session_id", "id")
    assert fk[0][6] == "CASCADE"
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "cancelled", "timed_out"])
def test_session_delete_cascades_approvals_and_existing_session_children(database, status):
    config, db = database
    command.upgrade(config, "head")
    sid, other = session(db), session(db)
    deleted = approval(db, sid, status)
    kept = [approval(db, other, status), approval(db, None, status)]
    db.execute(
        "INSERT INTO messages(id, session_id, role, content) VALUES (?, ?, 'user', 'hello')",
        (uuid4().hex, sid),
    )
    db.execute(
        "INSERT INTO session_summaries(id, session_id, summary) VALUES (?, ?, '{}')",
        (uuid4().hex, sid),
    )
    db.execute(
        "INSERT INTO session_action_grants(id, session_id, action, approved_by) "
        "VALUES (?, ?, 'test.read', 'local')",
        (uuid4().hex, sid),
    )
    db.execute("DELETE FROM sessions WHERE id=?", (sid,))
    db.commit()

    assert deleted not in rows(db)
    assert set(rows(db)) == set(kept)
    for table in ("messages", "session_summaries", "session_action_grants"):
        assert (
            db.execute(f"SELECT count(*) FROM {table} WHERE session_id=?", (sid,)).fetchone()[0]
            == 0
        )
    assert db.execute("SELECT id FROM sessions WHERE id=?", (other,)).fetchone()
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_constraint_rejects_new_orphan_references(database):
    config, db = database
    command.upgrade(config, "head")
    sid = session(db)
    aid = approval(db, sid)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        approval(db, uuid4().hex)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.execute("UPDATE tool_approvals SET session_id=? WHERE id=?", (uuid4().hex, aid))
    assert db.execute("SELECT session_id FROM tool_approvals WHERE id=?", (aid,)).fetchone() == (
        sid,
    )


def test_upgrade_is_repeatable_and_downgrade_preserves_surviving_records(database):
    config, db = database
    sid = session(db)
    approval(db, sid)
    approval(db, None)
    db.commit()
    before = rows(db)
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    assert rows(db) == before
    command.downgrade(config, "0005_mcp_servers")
    assert rows(db) == before
    assert db.execute("PRAGMA foreign_key_list(tool_approvals)").fetchall() == []
    command.upgrade(config, "head")
    assert rows(db) == before
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
