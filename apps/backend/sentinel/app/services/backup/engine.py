from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.config import app_version
from app.models.araios import AraiosModule, AraiosModuleRecord, AraiosModuleSecret
from app.models.memory import Memory, SessionSummary
from app.models.session_bindings import SessionBinding
from app.models.sessions import Message, Session
from app.models.sub_agents import SubAgentTask
from app.models.triggers import Trigger, TriggerLog
from app.services.backup.crypto import decrypt_backup, encrypt_backup
from app.services.backup.errors import BackupCompatibilityError, BackupFormatError
from app.services.secrets import is_invalid_secret
from app.services.secrets.encryption import SecretDecryptionError

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
BACKUP_KIND = "sentinel-instance-backup"

# ── backup compatibility contract ──
# Every backup is stamped with the app version that wrote it
# (`created_by_version`). It restores only when:
#     MIN_RESTORABLE_VERSION <= created_by_version        (full SemVer compare)
#     created_by_version.major <= running app's major     (forward guard)
# MIN_RESTORABLE_VERSION is the single knob: raise it to the current VERSION
# whenever a change makes older backups unrestorable. Until then every newer
# backup keeps restoring for free — compatibility is the default, you opt out.
MIN_RESTORABLE_VERSION = "0.1.0"

# Dead-man's-switch for schema drift. Pinned to the instance migration head this
# build is verified to restore onto. Any new instance migration moves the real
# head, which both reddens test_backup_verified_head_matches_instance_head and
# makes restore refuse at runtime — until a commit re-affirms this head (and, if
# the migration breaks old backups, raises MIN_RESTORABLE_VERSION).
VERIFIED_INSTANCE_ALEMBIC_HEAD = "0000_instance_v1"

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_INSTANCE_SCRIPT_LOCATION = _BACKEND_ROOT / "db" / "alembic" / "instance"
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Ref:
    """A foreign-key edge from a child column to a parent table's primary key."""

    column: str
    parent: type
    parent_pk: str = "id"


@dataclass(frozen=True)
class TableSpec:
    model: type
    required_refs: tuple[Ref, ...] = ()
    nullable_refs: tuple[Ref, ...] = ()
    export_filter: Callable[[Select], Select] | None = None
    order_by: str | None = None


@dataclass(frozen=True)
class Item:
    key: str
    label: str
    tables: tuple[TableSpec, ...]


# Canonical processing order. Parents that other items reference (sessions) come
# first so cross-item nullable refs resolve instead of getting nulled.
ITEMS: dict[str, Item] = {
    "sessions": Item(
        key="sessions",
        label="Sessions",
        tables=(
            TableSpec(
                Session,
                nullable_refs=(Ref("parent_session_id", Session, "id"),),
                order_by="created_at",
            ),
            TableSpec(Message, required_refs=(Ref("session_id", Session, "id"),)),
            TableSpec(SessionSummary, required_refs=(Ref("session_id", Session, "id"),)),
            TableSpec(SubAgentTask, required_refs=(Ref("session_id", Session, "id"),)),
            TableSpec(SessionBinding, required_refs=(Ref("session_id", Session, "id"),)),
        ),
    ),
    "memories": Item(
        key="memories",
        label="Memories",
        tables=(
            TableSpec(
                Memory,
                export_filter=lambda q: q.where(Memory.is_system.is_(False)),
                nullable_refs=(
                    Ref("parent_id", Memory, "id"),
                    Ref("session_id", Session, "id"),
                ),
                order_by="created_at",
            ),
        ),
    ),
    "modules": Item(
        key="modules",
        label="Modules",
        tables=(
            TableSpec(
                AraiosModule,
                export_filter=lambda q: q.where(AraiosModule.system.is_(False)),
            ),
            TableSpec(
                AraiosModuleRecord,
                required_refs=(Ref("module_name", AraiosModule, "name"),),
            ),
            TableSpec(
                AraiosModuleSecret,
                required_refs=(Ref("module_name", AraiosModule, "name"),),
            ),
        ),
    ),
    "triggers": Item(
        key="triggers",
        label="Triggers",
        tables=(
            TableSpec(Trigger),
            TableSpec(TriggerLog, required_refs=(Ref("trigger_id", Trigger, "id"),)),
        ),
    ),
}

CANONICAL_ORDER = tuple(ITEMS.keys())

# Owner-scoped rows are remapped to the importing user so restored data stays
# visible and manageable when a backup crosses accounts.
_OWNER_COLUMN: dict[type, str] = {
    Session: "user_id",
    SessionBinding: "user_id",
    Trigger: "user_id",
}


@dataclass
class ImportSummary:
    imported: int = 0
    skipped: int = 0
    by_table: dict[str, dict[str, int]] = field(default_factory=dict)
    items: list[str] = field(default_factory=list)

    def _record(self, table: str, *, imported: bool) -> None:
        bucket = self.by_table.setdefault(table, {"imported": 0, "skipped": 0})
        if imported:
            bucket["imported"] += 1
            self.imported += 1
        else:
            bucket["skipped"] += 1
            self.skipped += 1

    def as_dict(self) -> dict[str, Any]:
        return {"imported": self.imported, "skipped": self.skipped, "by_table": self.by_table}


def available_items() -> list[dict[str, str]]:
    return [{"key": i.key, "label": i.label} for i in ITEMS.values()]


# ── version compatibility ──
def _version_tuple(value: str | None) -> tuple[int, int, int] | None:
    match = _VERSION_RE.match((value or "").strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def restorable_reason(created_by_version: str | None) -> str | None:
    """None if a backup stamped `created_by_version` can be restored on this
    build, otherwise a human-readable reason it cannot."""
    made = _version_tuple(created_by_version)
    if made is None:
        return "Backup has no valid version stamp and cannot be restored."
    floor = _version_tuple(MIN_RESTORABLE_VERSION) or (0, 0, 0)
    if made < floor:
        return (
            f"Backup was made by Sentinel {created_by_version}; this version "
            f"only restores backups from {MIN_RESTORABLE_VERSION} or newer."
        )
    current = _version_tuple(app_version()) or (0, 0, 0)
    if made[0] > current[0]:
        return f"Backup was made by a newer Sentinel ({created_by_version}). Update to restore it."
    return None


def _instance_migration_head() -> str | None:
    try:
        config = Config()
        config.set_main_option("script_location", str(_INSTANCE_SCRIPT_LOCATION))
        heads = ScriptDirectory.from_config(config).get_heads()
    except Exception:  # pragma: no cover - migrations missing/unreadable
        return None
    return heads[0] if len(heads) == 1 else None


def _assert_schema_verified() -> None:
    if _instance_migration_head() != VERIFIED_INSTANCE_ALEMBIC_HEAD:
        raise BackupCompatibilityError(
            "Instance database schema has changed and backup restore has not "
            "been re-verified for it. Restore is disabled until the app is "
            "updated."
        )


# ── serialization helpers ──
def _column_attrs(model: type):
    return list(sa_inspect(model).mapper.column_attrs)


def _column_by_key(model: type, key: str):
    return sa_inspect(model).mapper.column_attrs[key].columns[0]


def _pk_attr_keys(model: type) -> list[str]:
    return [a.key for a in _column_attrs(model) if a.columns[0].primary_key]


def _single_pk_key(model: type) -> str | None:
    keys = _pk_attr_keys(model)
    return keys[0] if len(keys) == 1 else None


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "tolist"):  # numpy ndarray (pgvector embedding)
        return value.tolist()
    return value


def _row_to_dict(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for attr in _column_attrs(type(obj)):
        value = getattr(obj, attr.key)
        if is_invalid_secret(value):
            raise SecretDecryptionError("Invalid encrypted value.")
        out[attr.key] = _jsonable(value)
    return out


def _coerce(value: Any, column) -> Any:
    if value is None:
        return None
    try:
        pytype = column.type.python_type
    except NotImplementedError:
        return value  # e.g. pgvector Vector — accepts a plain list
    if pytype is uuid.UUID and isinstance(value, str):
        return uuid.UUID(value)
    if pytype is datetime and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _build(model: type, data: dict[str, Any]) -> Any:
    kwargs: dict[str, Any] = {}
    for attr in _column_attrs(model):
        if attr.key in data:
            kwargs[attr.key] = _coerce(data[attr.key], attr.columns[0])
    return model(**kwargs)


async def _exists(db: AsyncSession, model: type, pk_data: dict[str, Any]) -> bool:
    stmt = select(model)
    for key, value in pk_data.items():
        stmt = stmt.where(getattr(model, key) == value)
    result = await db.execute(stmt)
    return result.scalars().first() is not None


def _pk_token(pk_data: dict[str, Any]) -> tuple:
    return tuple(sorted((k, str(v)) for k, v in pk_data.items()))


async def _load_skipping_undecryptable(db: AsyncSession, spec: TableSpec) -> list[Any]:
    """Re-load a table row-by-row, dropping rows whose encrypted columns can't be
    decrypted, so one corrupt secret can't abort the whole export."""
    pk_cols = [getattr(spec.model, k) for k in _pk_attr_keys(spec.model)]
    pk_stmt = select(*pk_cols)
    if spec.export_filter is not None:
        pk_stmt = spec.export_filter(pk_stmt)
    objs: list[Any] = []
    for pk_row in (await db.execute(pk_stmt)).all():
        stmt = select(spec.model)
        for col, value in zip(pk_cols, pk_row):
            stmt = stmt.where(col == value)
        try:
            obj = (await db.execute(stmt)).scalars().first()
        except SecretDecryptionError:
            logger.warning(
                "Skipping undecryptable %s row pk=%s during backup export",
                spec.model.__tablename__,
                tuple(pk_row),
            )
            continue
        if obj is not None:
            objs.append(obj)
    return objs


async def _ref_present(db: AsyncSession, ref: Ref, value: Any, present: dict[type, set]) -> bool:
    col = _column_by_key(ref.parent, ref.parent_pk)
    coerced = _coerce(value, col)
    token = ((ref.parent_pk, str(coerced)),)
    if token in present.get(ref.parent, set()):
        return True
    return await _exists(db, ref.parent, {ref.parent_pk: coerced})


# ── export ──
async def export_backup(
    db: AsyncSession,
    *,
    instance_name: str | None,
    items: list[str],
    passphrase: str,
) -> bytes:
    selected = [i for i in CANONICAL_ORDER if i in items]
    exported: dict[type, set] = {}
    tables: dict[str, list[dict[str, Any]]] = {}

    for item_key in selected:
        for spec in ITEMS[item_key].tables:
            stmt = select(spec.model)
            if spec.export_filter is not None:
                stmt = spec.export_filter(stmt)
            try:
                objs = list((await db.execute(stmt)).scalars().all())
            except SecretDecryptionError:
                objs = await _load_skipping_undecryptable(db, spec)
            if spec.order_by:
                objs.sort(key=lambda o: getattr(o, spec.order_by) or _EPOCH)

            pk_key = _single_pk_key(spec.model)
            rows: list[dict[str, Any]] = []
            for obj in objs:
                try:
                    row = _row_to_dict(obj)
                except SecretDecryptionError:
                    logger.warning(
                        "Skipping undecryptable %s row during backup export",
                        spec.model.__tablename__,
                    )
                    continue
                drop = False
                for ref in spec.required_refs:
                    v = row.get(ref.column)
                    if v is None or v not in exported.get(ref.parent, set()):
                        drop = True
                        break
                if drop:
                    continue
                for ref in spec.nullable_refs:
                    v = row.get(ref.column)
                    if v is not None and v not in exported.get(ref.parent, set()):
                        row[ref.column] = None
                rows.append(row)
                if pk_key is not None:
                    exported.setdefault(spec.model, set()).add(row[pk_key])
            tables[spec.model.__tablename__] = rows

    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": BACKUP_KIND,
        "created_by_version": app_version(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_instance": instance_name,
        "items": selected,
        "tables": tables,
    }
    return encrypt_backup(json.dumps(payload).encode("utf-8"), passphrase)


# ── import ──
def _decode_payload(blob: bytes, passphrase: str) -> dict[str, Any]:
    raw = decrypt_backup(blob, passphrase)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BackupFormatError("Backup payload is not valid JSON.") from exc
    if not isinstance(payload, dict) or payload.get("kind") != BACKUP_KIND:
        raise BackupFormatError("File is not a Sentinel instance backup.")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise BackupFormatError("Unsupported backup schema version.")
    if not isinstance(payload.get("tables"), dict):
        raise BackupFormatError("Backup is missing its table payload.")
    return payload


def inspect_backup(blob: bytes, passphrase: str) -> dict[str, Any]:
    payload = _decode_payload(blob, passphrase)
    created_by = payload.get("created_by_version")
    reason = restorable_reason(created_by)
    return {
        "source_instance": payload.get("source_instance"),
        "created_at": payload.get("created_at"),
        "created_by_version": created_by,
        "items": [i for i in payload.get("items", []) if i in ITEMS],
        "restorable": reason is None,
        "compatibility": reason,
    }


async def _import_table(
    db: AsyncSession,
    spec: TableSpec,
    rows: list[dict[str, Any]],
    present: dict[type, set],
    summary: ImportSummary,
    owner_user_id: str | None = None,
) -> None:
    model = spec.model
    table = model.__tablename__
    pk_attrs = [(k, _column_by_key(model, k)) for k in _pk_attr_keys(model)]
    owner_column = _OWNER_COLUMN.get(model)
    present.setdefault(model, set())

    if spec.order_by:
        rows = sorted(rows, key=lambda r: r.get(spec.order_by) or "")

    for raw in rows:
        data = dict(raw)
        if owner_column is not None and owner_user_id is not None:
            data[owner_column] = owner_user_id

        skip = False
        for ref in spec.required_refs:
            value = data.get(ref.column)
            if value is None or not await _ref_present(db, ref, value, present):
                skip = True
                break
        if skip:
            summary._record(table, imported=False)
            continue

        for ref in spec.nullable_refs:
            value = data.get(ref.column)
            if value is not None and not await _ref_present(db, ref, value, present):
                data[ref.column] = None

        pk_data = {key: _coerce(data.get(key), col) for key, col in pk_attrs}
        if any(v is None for v in pk_data.values()):
            summary._record(table, imported=False)
            continue

        if await _exists(db, model, pk_data):
            present[model].add(_pk_token(pk_data))
            summary._record(table, imported=False)
            continue

        db.add(_build(model, data))
        await db.flush()
        present[model].add(_pk_token(pk_data))
        summary._record(table, imported=True)


async def import_backup(
    db: AsyncSession,
    blob: bytes,
    passphrase: str,
    items: list[str] | None = None,
    owner_user_id: str | None = None,
) -> ImportSummary:
    payload = _decode_payload(blob, passphrase)
    reason = restorable_reason(payload.get("created_by_version"))
    if reason:
        raise BackupCompatibilityError(reason)
    _assert_schema_verified()
    backup_items = set(payload.get("items", []))
    requested = set(items) if items is not None else None

    selected = [
        i for i in CANONICAL_ORDER if i in backup_items and (requested is None or i in requested)
    ]

    present: dict[type, set] = {}
    summary = ImportSummary(items=list(selected))
    tables = payload["tables"]

    for item_key in selected:
        for spec in ITEMS[item_key].tables:
            rows = tables.get(spec.model.__tablename__, [])
            await _import_table(db, spec, rows, present, summary, owner_user_id)

    await db.commit()
    return summary
