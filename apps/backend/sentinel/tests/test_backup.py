from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("DATA_ENCRYPTION_KEY", "test-data-key-with-32-bytes-minimum")

from fastapi.testclient import TestClient

from app.main import app
from app.models.araios import AraiosModule, AraiosModuleRecord, AraiosModuleSecret
from app.models.memory import Memory
from app.models.sessions import Message, Session
from app.models.triggers import Trigger, TriggerLog
from app.services.backup import (
    BackupCompatibilityError,
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


# ── engine fixtures ──
def _seed_source() -> FakeDB:
    db = FakeDB(seed_auth=False)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    db.add(AraiosModule(name="notes", label="Notes", system=False))
    db.add(AraiosModule(name="core", label="Core", system=True))
    db.add(AraiosModuleRecord(id="rec-notes", module_name="notes", data={"x": 1}))
    db.add(AraiosModuleRecord(id="rec-core", module_name="core", data={"y": 2}))
    db.add(AraiosModuleSecret(module_name="notes", key="api_key", value="s3cr3t"))

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

    dst = FakeDB(seed_auth=False)
    summary = await import_backup(dst, blob, _PASS)

    # notes module + its record + its secret; core (system) module and its record excluded.
    modules = dst.storage[AraiosModule]
    assert {m.name for m in modules} == {"notes"}
    assert {r.id for r in dst.storage[AraiosModuleRecord]} == {"rec-notes"}
    assert {s.key for s in dst.storage[AraiosModuleSecret]} == {"api_key"}

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

    dst = FakeDB(seed_auth=False)
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

    dst = FakeDB(seed_auth=False)
    await import_backup(dst, blob, _PASS, items=["modules"])

    assert len(dst.storage[AraiosModule]) == 1
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

    dst = FakeDB(seed_auth=False)
    await import_backup(dst, blob, _PASS)
    assert len(dst.storage[Trigger]) == 1
    assert len(dst.storage[AraiosModule]) == 0


@pytest.mark.asyncio
async def test_import_remaps_owner_to_importing_user():
    src = _seed_source()
    blob = await export_backup(src, instance_name="main", items=ALL_ITEMS, passphrase=_PASS)

    dst = FakeDB(seed_auth=False)
    summary = await import_backup(dst, blob, _PASS, owner_user_id="u2")

    # Owner-scoped rows are reassigned to the importing user, not the source's.
    assert {s.user_id for s in dst.storage[Session]} == {"u2"}
    assert {t.user_id for t in dst.storage[Trigger]} == {"u2"}
    # Processed item keys are tracked so the router can rebuild the runtime.
    assert set(summary.items) == set(ALL_ITEMS)


# ── version compatibility ──
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
async def test_import_refuses_backup_below_min_version(monkeypatch):
    monkeypatch.setattr(backup_engine, "MIN_RESTORABLE_VERSION", "0.5.0")
    monkeypatch.setattr(backup_engine, "app_version", lambda: "0.5.0")
    with pytest.raises(BackupCompatibilityError):
        await import_backup(FakeDB(seed_auth=False), _stamped_blob("0.4.9"), _PASS)


@pytest.mark.asyncio
async def test_import_refuses_backup_from_newer_major(monkeypatch):
    monkeypatch.setattr(backup_engine, "app_version", lambda: "1.2.0")
    with pytest.raises(BackupCompatibilityError):
        await import_backup(FakeDB(seed_auth=False), _stamped_blob("2.0.0"), _PASS)


@pytest.mark.asyncio
async def test_import_allows_newer_minor_same_major(monkeypatch):
    # Upper bound is major-only: a newer minor/patch still restores.
    monkeypatch.setattr(backup_engine, "app_version", lambda: "1.2.0")
    summary = await import_backup(FakeDB(seed_auth=False), _stamped_blob("1.9.3"), _PASS)
    assert summary.imported == 0  # empty tables, but not refused


@pytest.mark.asyncio
async def test_import_refuses_unstamped_backup():
    with pytest.raises(BackupCompatibilityError):
        await import_backup(FakeDB(seed_auth=False), _stamped_blob(None), _PASS)


@pytest.mark.asyncio
async def test_import_refuses_when_schema_head_unverified(monkeypatch):
    monkeypatch.setattr(backup_engine, "VERIFIED_INSTANCE_ALEMBIC_HEAD", "not-the-real-head")
    blob = await export_backup(
        _seed_source(), instance_name="main", items=["modules"], passphrase=_PASS
    )
    with pytest.raises(BackupCompatibilityError):
        await import_backup(FakeDB(seed_auth=False), blob, _PASS)


def test_inspect_reports_incompatible_backup(monkeypatch):
    monkeypatch.setattr(backup_engine, "app_version", lambda: "1.0.0")
    info = inspect_backup(_stamped_blob("2.0.0"), _PASS)
    assert info["restorable"] is False
    assert info["compatibility"]
    assert info["created_by_version"] == "2.0.0"


# ── router ──
def _auth_headers(client: TestClient) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_list_items_endpoint():
    fake_db = FakeDB()
    old_init = install_fake_db_overrides(app_db=fake_db)
    try:
        client = TestClient(app)
        headers = _auth_headers(client)
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
        client = TestClient(app)
        headers = _auth_headers(client)
        resp = client.post(
            f"{BACKUP_API}/export", json={"items": [], "passphrase": _PASS}, headers=headers
        )
        assert resp.status_code == 400
    finally:
        restore_test_app(old_init)


def test_api_export_then_import_roundtrip():
    fake_db = FakeDB()
    fake_db.add(AraiosModule(name="notes", label="Notes", system=False))

    old_init = install_fake_db_overrides(app_db=fake_db)
    try:
        client = TestClient(app)
        headers = _auth_headers(client)

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
        fake_db.storage[AraiosModule] = []

        imported = client.post(
            f"{BACKUP_API}/import",
            json={"data": blob_b64, "passphrase": _PASS, "items": ["modules"]},
            headers=headers,
        )
        assert imported.status_code == 200
        assert imported.json()["imported"] >= 1
        assert {m.name for m in fake_db.storage[AraiosModule]} == {"notes"}
    finally:
        restore_test_app(old_init)


def test_api_import_wrong_passphrase():
    fake_db = FakeDB()
    fake_db.add(AraiosModule(name="notes", label="Notes", system=False))

    old_init = install_fake_db_overrides(app_db=fake_db)
    try:
        client = TestClient(app)
        headers = _auth_headers(client)
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
