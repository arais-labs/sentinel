from __future__ import annotations

import asyncio
from alembic import command
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.database.engine import create_database_engine
from app.models.tool_approvals import SessionActionGrant, ToolApproval
from tests.test_alembic_baselines import migration_config

import base64
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("DATA_ENCRYPTION_KEY", "test-data-key-with-32-bytes-minimum")

from fastapi.testclient import TestClient

from app.main import app
from app.models.memory import Memory
from app.models.modules import Module, ModuleRecord, ModuleSecret
from app.models.sessions import Message, Session
from app.models.triggers import Trigger, TriggerLog
from app.services.backup import (
    BackupFormatError,
    BackupPassphraseError,
    encrypt_backup,
    export_backup,
    import_backup,
    inspect_backup,
)
from app.services.backup import engine as backup_engine
from app.services.backup.crypto import decrypt_backup
from tests.fake_db import FakeDB
from tests.helpers import install_fake_db_overrides, restore_test_app

BACKUP_API = "/api/v1/instances/main/backup"
_PASS = "correct horse battery staple"


# ── crypto ──
def test_crypto_roundtrip():
    blob = encrypt_backup(b"hello world", _PASS)
    assert decrypt_backup(blob, _PASS) == b"hello world"


def test_crypto_wrong_passphrase():
    blob = encrypt_backup(b"secret", _PASS)
    with pytest.raises(BackupPassphraseError):
        decrypt_backup(blob, "nope")


def test_crypto_rejects_foreign_blob():
    with pytest.raises(BackupFormatError):
        decrypt_backup(b"not a sentinel backup at all", _PASS)


def test_crypto_requires_passphrase():
    with pytest.raises(BackupPassphraseError):
        encrypt_backup(b"x", "")


@pytest.fixture(autouse=True)
def _payload_test_kdf(request, monkeypatch):
    # Keep crypto contract tests at production cost. Payload/import tests still
    # use real authenticated encryption, but need not repeatedly benchmark scrypt.
    if not request.node.name.startswith("test_crypto_"):
        from app.services.backup import crypto

        monkeypatch.setattr(crypto, "_SCRYPT_N", 2**10)


# ── engine fixtures ──
def _seed_source() -> FakeDB:
    db = FakeDB()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    db.add(Module(name="notes", label="Notes", system=False))
    db.add(Module(name="core", label="Core", system=True))
    db.add(ModuleRecord(id="rec-notes", module_name="notes", data={"x": 1}))
    db.add(ModuleRecord(id="rec-core", module_name="core", data={"y": 2}))
    db.add(ModuleSecret(module_name="notes", key="api_key", value="s3cr3t"))

    s1 = Session(id=uuid.uuid4(), user_id="u1", title="Root", created_at=base)
    s2 = Session(
        id=uuid.uuid4(),
        user_id="u1",
        title="Child",
        parent_session_id=s1.id,
        created_at=base + timedelta(minutes=1),
    )
    db.add(s1)
    db.add(s2)
    db.add(Message(id=uuid.uuid4(), session_id=s1.id, role="user", content="hi"))

    m1 = Memory(
        id=uuid.uuid4(), content="parent", category="project", is_system=False, created_at=base
    )
    m2 = Memory(
        id=uuid.uuid4(),
        content="child",
        category="project",
        is_system=False,
        parent_id=m1.id,
        session_id=s1.id,
        created_at=base + timedelta(minutes=1),
    )
    sys_mem = Memory(
        id=uuid.uuid4(),
        content="system",
        category="core",
        is_system=True,
        system_key="agent_identity",
        created_at=base,
    )
    db.add(m1)
    db.add(m2)
    db.add(sys_mem)

    t1 = Trigger(
        id=uuid.uuid4(),
        name="nightly",
        type="cron",
        config={"cron": "0 0 * * *"},
        action_type="agent_message",
        action_config={"text": "go"},
    )
    db.add(t1)
    db.add(TriggerLog(id=uuid.uuid4(), trigger_id=t1.id, status="ok"))
    return db


ALL_ITEMS = ["sessions", "memories", "modules", "triggers"]


@pytest.mark.asyncio
async def test_export_import_roundtrip():
    src = _seed_source()
    blob = await export_backup(src, instance_name="main", items=ALL_ITEMS, passphrase=_PASS)

    dst = FakeDB()
    summary = await import_backup(dst, blob, _PASS)

    # notes module + its record + its secret; core (system) module and its record excluded.
    modules = dst.storage[Module]
    assert {m.name for m in modules} == {"notes"}
    assert {r.id for r in dst.storage[ModuleRecord]} == {"rec-notes"}
    assert {s.key for s in dst.storage[ModuleSecret]} == {"api_key"}

    # two sessions (parent + child), one message.
    assert len(dst.storage[Session]) == 2
    assert len(dst.storage[Message]) == 1

    # non-system memories only (parent + child); system memory excluded.
    mems = dst.storage[Memory]
    assert len(mems) == 2
    assert all(not m.is_system for m in mems)

    assert len(dst.storage[Trigger]) == 1
    assert len(dst.storage[TriggerLog]) == 1

    assert summary.skipped == 0
    assert summary.imported > 0


@pytest.mark.asyncio
async def test_reimport_is_noop():
    src = _seed_source()
    blob = await export_backup(src, instance_name="main", items=ALL_ITEMS, passphrase=_PASS)

    dst = FakeDB()
    first = await import_backup(dst, blob, _PASS)
    counts = {model: len(rows) for model, rows in dst.storage.items()}

    second = await import_backup(dst, blob, _PASS)

    # Nothing new inserted; every row skipped on the second pass.
    assert {model: len(rows) for model, rows in dst.storage.items()} == counts
    assert second.imported == 0
    assert second.skipped == first.imported


@pytest.mark.asyncio
async def test_selective_import_only_modules():
    src = _seed_source()
    blob = await export_backup(src, instance_name="main", items=ALL_ITEMS, passphrase=_PASS)

    dst = FakeDB()
    await import_backup(dst, blob, _PASS, items=["modules"])

    assert len(dst.storage[Module]) == 1
    assert len(dst.storage[Session]) == 0
    assert len(dst.storage[Memory]) == 0
    assert len(dst.storage[Trigger]) == 0


@pytest.mark.asyncio
async def test_selective_export_only_triggers():
    src = _seed_source()
    blob = await export_backup(src, instance_name="main", items=["triggers"], passphrase=_PASS)

    info = inspect_backup(blob, _PASS)
    assert info["items"] == ["triggers"]
    assert info["source_instance"] == "main"

    dst = FakeDB()
    await import_backup(dst, blob, _PASS)
    assert len(dst.storage[Trigger]) == 1
    assert len(dst.storage[Module]) == 0


@pytest.mark.asyncio
async def test_import_remaps_owner_to_importing_user():
    src = _seed_source()
    blob = await export_backup(src, instance_name="main", items=ALL_ITEMS, passphrase=_PASS)

    dst = FakeDB()
    summary = await import_backup(dst, blob, _PASS, owner_user_id="u2")

    # Owner-scoped rows are reassigned to the importing user, not the source's.
    assert {s.user_id for s in dst.storage[Session]} == {"u2"}
    assert {t.user_id for t in dst.storage[Trigger]} == {"u2"}
    # Processed item keys are tracked so the router can rebuild the runtime.
    assert set(summary.items) == set(ALL_ITEMS)


# ── backup format contract ──
def _stamped_blob(created_by_version, *, items=("modules",), tables=None) -> bytes:
    payload = {
        "schema_version": backup_engine.SCHEMA_VERSION,
        "kind": backup_engine.BACKUP_KIND,
        "created_by_version": created_by_version,
        "created_at": "2026-01-01T00:00:00+00:00",
        "source_instance": "main",
        "items": list(items),
        "tables": tables or {},
    }
    return encrypt_backup(json.dumps(payload).encode("utf-8"), _PASS)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, "0.0.1", "999.0.0", "development"])
async def test_app_version_is_informational(version):
    blob = _stamped_blob(version)
    assert inspect_backup(blob, _PASS)["created_by_version"] == version
    assert (await import_backup(FakeDB(), blob, _PASS)).imported == 0


# ── router ──
def _desktop_headers(client: TestClient) -> dict[str, str]:
    return {"x-sentinel-desktop-token": "test-desktop-transport-token"}


def test_list_items_endpoint():
    fake_db = FakeDB()
    old_init = install_fake_db_overrides(app_db=fake_db)
    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )
        headers = _desktop_headers(client)
        resp = client.get(f"{BACKUP_API}/items", headers=headers)
        assert resp.status_code == 200
        keys = {i["key"] for i in resp.json()["items"]}
        assert keys == {"sessions", "memories", "modules", "triggers"}
    finally:
        restore_test_app(old_init)


def test_export_requires_item_selection():
    fake_db = FakeDB()
    old_init = install_fake_db_overrides(app_db=fake_db)
    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )
        headers = _desktop_headers(client)
        resp = client.post(
            f"{BACKUP_API}/export", json={"items": [], "passphrase": _PASS}, headers=headers
        )
        assert resp.status_code == 400
    finally:
        restore_test_app(old_init)


def test_api_export_then_import_roundtrip():
    fake_db = FakeDB()
    fake_db.add(Module(name="notes", label="Notes", system=False))

    old_init = install_fake_db_overrides(app_db=fake_db)
    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )
        headers = _desktop_headers(client)

        exported = client.post(
            f"{BACKUP_API}/export",
            json={"items": ["modules"], "passphrase": _PASS},
            headers=headers,
        )
        assert exported.status_code == 200
        blob_b64 = base64.b64encode(exported.content).decode()

        inspected = client.post(
            f"{BACKUP_API}/inspect",
            json={"data": blob_b64, "passphrase": _PASS},
            headers=headers,
        )
        assert inspected.status_code == 200
        assert inspected.json()["items"] == ["modules"]

        # Drop the module to simulate restoring onto an instance that lacks it.
        fake_db.storage[Module] = []

        imported = client.post(
            f"{BACKUP_API}/import",
            json={"data": blob_b64, "passphrase": _PASS, "items": ["modules"]},
            headers=headers,
        )
        assert imported.status_code == 200
        assert imported.json()["imported"] >= 1
        assert {m.name for m in fake_db.storage[Module]} == {"notes"}
    finally:
        restore_test_app(old_init)


def test_api_import_wrong_passphrase():
    fake_db = FakeDB()
    fake_db.add(Module(name="notes", label="Notes", system=False))

    old_init = install_fake_db_overrides(app_db=fake_db)
    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )
        headers = _desktop_headers(client)
        exported = client.post(
            f"{BACKUP_API}/export",
            json={"items": ["modules"], "passphrase": _PASS},
            headers=headers,
        )
        blob_b64 = base64.b64encode(exported.content).decode()

        bad = client.post(
            f"{BACKUP_API}/import",
            json={"data": blob_b64, "passphrase": "wrong", "items": ["modules"]},
            headers=headers,
        )
        assert bad.status_code == 400
    finally:
        restore_test_app(old_init)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 999),
        ("schema_version", "3"),
        ("items", "sessions"),
        ("items", ["unknown"]),
        ("tables", {"modules": "bad"}),
        ("tables", {"modules": [1]}),
        ("tables", {"session_action_grants": []}),
    ],
)
def test_malformed_or_unsupported_payload_rejected(field, value):
    payload = json.loads(decrypt_backup(_stamped_blob("development"), _PASS))
    payload[field] = value
    blob = encrypt_backup(json.dumps(payload).encode(), _PASS)
    with pytest.raises(BackupFormatError):
        inspect_backup(blob, _PASS)


def test_backup_roundtrip_on_fresh_migrations(tmp_path):

    source_path, target_path = tmp_path / "source.sqlite", tmp_path / "target.sqlite"
    for path in (source_path, target_path):
        command.upgrade(migration_config("instance", path), "head")

    async def scenario():
        source = create_database_engine(f"sqlite+aiosqlite:///{source_path}")
        target = create_database_engine(f"sqlite+aiosqlite:///{target_path}")
        try:
            async with async_sessionmaker(source, expire_on_commit=False)() as db:
                seeded = _seed_source()
                # Insert parent tables before dependent rows with SQLite FK enforcement.
                for model in (
                    Module,
                    Session,
                    Message,
                    Memory,
                    Trigger,
                    TriggerLog,
                    ModuleRecord,
                    ModuleSecret,
                ):
                    for row in seeded.storage[model]:
                        db.add(row)
                        await db.flush()
                session_id = seeded.storage[Session][0].id
                db.add(
                    SessionActionGrant(
                        session_id=session_id, action="notes.write", approved_by="u1"
                    )
                )
                db.add(
                    ToolApproval(
                        session_id=session_id,
                        tool_name="notes",
                        action="notes.write",
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    )
                )
                await db.commit()
                blob = await export_backup(
                    db, instance_name="source", items=ALL_ITEMS, passphrase=_PASS
                )
            payload = json.loads(decrypt_backup(blob, _PASS))
            assert "session_action_grants" not in payload["tables"]
            assert "tool_approvals" not in payload["tables"]
            async with async_sessionmaker(target, expire_on_commit=False)() as db:
                summary = await import_backup(db, blob, _PASS, owner_user_id="restorer")
                assert summary.imported > 0 and summary.skipped == 0
                assert await db.scalar(select(func.count()).select_from(Session)) == 2
                assert await db.scalar(select(ModuleSecret.value)) == "s3cr3t"
                assert await db.scalar(select(func.count()).select_from(SessionActionGrant)) == 0
                assert await db.scalar(select(func.count()).select_from(ToolApproval)) == 0
                assert set((await db.scalars(select(Session.user_id))).all()) == {"restorer"}
                assert (await import_backup(db, blob, _PASS)).imported == 0
        finally:
            await source.dispose()
            await target.dispose()

    asyncio.run(scenario())
