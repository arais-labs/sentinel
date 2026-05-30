from __future__ import annotations

import asyncio
import types

import pytest
from pydantic import ValidationError

from app.models.araios import AraiosModule, AraiosModuleRecord, AraiosModuleSecret
from app.schemas.modules import EditModuleRequest
from app.services.araios.module_updates import fold_ops_into_delta
from app.services.araios.system_modules.module_manager import handlers
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


def _fake_module(**overrides):
    base = dict(
        fields=[
            {"key": "company", "label": "Company", "type": "text"},
            {"key": "stale", "label": "Stale", "type": "text"},
        ],
        actions=[
            {"id": "sync", "label": "Sync", "code": "OLD", "type": "standalone"},
            {"id": "other", "label": "Other", "code": "KEEP", "type": "standalone"},
        ],
        fields_config={"titleField": "company"},
        secrets=[],
        system=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


# ── Tool registration (THE collision guard) ──


def test_edit_module_registered_in_grouped_schema_without_collision():
    from app.services.araios.system_modules.module_manager.module import MODULE

    # to_tool_definitions() raises at module_types._build_grouped_parameters_schema if any two
    # commands declare a top-level property with a different shape — this is the guard that
    # adding edit_module did not brick the entire module_manager tool.
    defs = MODULE.to_tool_definitions()
    assert len(defs) == 1
    schema = defs[0].parameters_schema
    assert "edit_module" in schema["properties"]["command"]["enum"]
    assert {"name", "ops"} <= set(schema["properties"])


def test_module_manager_still_registers_with_edit_module():
    from app.services.tools.registry_builder import build_default_registry

    registry = build_default_registry()
    assert registry.get("module_manager") is not None


# ── Request validation ──


def test_edit_request_normalizes_name_and_parses_ops():
    request = EditModuleRequest.model_validate(
        {
            "name": "Contacts",
            "ops": [{"op": "patch_action", "id": "sync", "set": {"code": "NEW"}}],
        }
    )
    assert request.name == "contacts"
    assert request.ops[0].set == {"code": "NEW"}


def test_edit_request_rejects_unknown_op():
    with pytest.raises(ValidationError):
        EditModuleRequest.model_validate({"name": "x", "ops": [{"op": "nope"}]})


def test_edit_request_rejects_empty_ops():
    with pytest.raises(ValidationError):
        EditModuleRequest.model_validate({"name": "x", "ops": []})


# ── fold_ops_into_delta (pure logic) ──


def _fold(ops):
    request = EditModuleRequest.model_validate({"name": "m", "ops": ops})
    return fold_ops_into_delta(_fake_module(), request)


def test_patch_action_preserves_other_actions_and_metadata():
    delta = _fold([{"op": "patch_action", "id": "sync", "set": {"code": "NEW"}}])
    by_id = {a["id"]: a for a in delta.final_actions}
    assert by_id["sync"]["code"] == "NEW"
    assert by_id["sync"]["label"] == "Sync"  # untouched key preserved
    assert by_id["other"] == {"id": "other", "label": "Other", "code": "KEEP", "type": "standalone"}


def test_add_field_appends_without_dropping_existing():
    delta = _fold([{"op": "add_field", "field": {"key": "phone", "label": "Phone"}}])
    assert [f["key"] for f in delta.updates["fields"]] == ["company", "stale", "phone"]


def test_rename_field_migrates_refs_and_records_by_default():
    delta = _fold([{"op": "rename_field", "from_key": "company", "to_key": "org"}])
    assert [f["key"] for f in delta.updates["fields"]] == ["org", "stale"]
    assert delta.updates["fields_config"]["titleField"] == "org"
    assert delta.record_renames == [("company", "org")]


def test_rename_field_can_skip_record_migration():
    delta = _fold(
        [
            {
                "op": "rename_field",
                "from_key": "company",
                "to_key": "org",
                "migrate_record_data": False,
            }
        ]
    )
    assert delta.record_renames == []


def test_remove_field_keeps_data_by_default_and_purges_on_flag():
    keep = _fold([{"op": "remove_field", "key": "stale"}])
    assert keep.record_purge_keys == set()
    purge = _fold([{"op": "remove_field", "key": "stale", "purge_record_data": True}])
    assert purge.record_purge_keys == {"stale"}


def test_remove_action_drops_it_from_final_actions():
    delta = _fold([{"op": "remove_action", "id": "other"}])
    assert [a["id"] for a in delta.final_actions] == ["sync"]


def test_set_fields_config_rejects_dangling_reference():
    with pytest.raises(ValueError, match="references unknown field"):
        _fold([{"op": "set_fields_config", "fields_config": {"badgeField": "ghost"}}])


def test_reserved_action_id_rejected():
    with pytest.raises(ValueError, match="reserved"):
        _fold([{"op": "set_action", "action": {"id": "list_records", "label": "x", "code": "y"}}])


def test_patch_missing_action_is_index_qualified_error():
    with pytest.raises(ValueError, match=r"op #1 \(patch_action\)"):
        _fold([{"op": "patch_action", "id": "missing", "set": {"code": "z"}}])


def test_update_field_cannot_change_key():
    with pytest.raises(ValueError, match="cannot change 'key'"):
        _fold([{"op": "update_field", "key": "company", "changes": {"key": "renamed"}}])


def test_fold_is_pure_and_all_or_nothing():
    mod = _fake_module()
    original_actions = mod.actions
    request = EditModuleRequest.model_validate(
        {
            "name": "m",
            "ops": [
                {"op": "set_meta", "label": "Changed"},
                {"op": "patch_action", "id": "missing", "set": {"code": "z"}},
            ],
        }
    )
    with pytest.raises(ValueError):
        fold_ops_into_delta(mod, request)
    # The working copies are discarded on error — the source module is untouched.
    assert mod.actions is original_actions
    assert mod.actions[0]["code"] == "OLD"


def test_fold_rejects_no_op_ops():
    with pytest.raises(ValueError, match="no changes"):
        _fold([{"op": "set_permissions", "permissions": {}}])


# ── handle_edit_module (integration with FakeDB) ──


class _FakeSession:
    def __init__(self, db: FakeDB) -> None:
        self._db = db

    async def __aenter__(self) -> FakeDB:
        return self._db

    async def __aexit__(self, *_a) -> bool:
        return False


def _patch_handler_session(monkeypatch, db: FakeDB) -> None:
    async def _noop_sync(_db, **_kwargs):
        return {}

    monkeypatch.setattr(handlers, "AsyncSessionLocal", lambda: _FakeSession(db))
    monkeypatch.setattr(handlers, "get_app_state", lambda: None)
    monkeypatch.setattr(handlers, "sync_dynamic_module_permissions", _noop_sync)


def _seed_module(db: FakeDB) -> AraiosModule:
    mod = AraiosModule(
        name="contacts",
        label="Contacts",
        description="",
        icon="box",
        fields=[{"key": "company", "label": "Company", "type": "text"}],
        fields_config={"titleField": "company"},
        actions=[{"id": "sync", "label": "Sync", "code": "OLD", "type": "standalone"}],
        secrets=[],
        page_title=None,
        page_content=None,
        system=False,
        order=100,
    )
    db.add(mod)
    return mod


def test_handle_edit_module_migrates_record_data_and_preserves_records(monkeypatch):
    db = FakeDB(seed_auth=False)
    mod = _seed_module(db)
    record = AraiosModuleRecord(id="r1", module_name="contacts", data={"company": "Acme"})
    db.add(record)
    db.add(AraiosModuleSecret(module_name="contacts", key="token", value="v"))
    _patch_handler_session(monkeypatch, db)

    result = _run(
        handlers.handle_edit_module(
            {
                "name": "Contacts",
                "ops": [{"op": "rename_field", "from_key": "company", "to_key": "org"}],
            }
        )
    )

    assert result["ok"] is True
    assert result["applied_ops"] == 1
    assert mod.fields[0]["key"] == "org"
    assert mod.fields_config["titleField"] == "org"
    # Record data migrated to the new key; the secret value row is untouched.
    assert record.data == {"org": "Acme"}
    assert db.storage[AraiosModuleSecret][0].value == "v"


def test_handle_edit_module_patches_one_action_without_touching_records(monkeypatch):
    db = FakeDB(seed_auth=False)
    mod = _seed_module(db)
    record = AraiosModuleRecord(id="r1", module_name="contacts", data={"company": "Acme"})
    db.add(record)
    _patch_handler_session(monkeypatch, db)

    _run(
        handlers.handle_edit_module(
            {
                "name": "contacts",
                "ops": [{"op": "patch_action", "id": "sync", "set": {"code": "NEW"}}],
            }
        )
    )

    assert mod.actions[0]["code"] == "NEW"
    assert mod.actions[0]["label"] == "Sync"
    assert record.data == {"company": "Acme"}


def test_handle_edit_module_rejects_system_module(monkeypatch):
    db = FakeDB(seed_auth=False)
    mod = _seed_module(db)
    mod.system = True
    _patch_handler_session(monkeypatch, db)

    with pytest.raises(ValueError, match="system module"):
        _run(
            handlers.handle_edit_module(
                {"name": "contacts", "ops": [{"op": "set_meta", "label": "X"}]}
            )
        )
