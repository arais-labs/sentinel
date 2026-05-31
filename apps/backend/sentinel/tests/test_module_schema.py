from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.models.araios import AraiosModule
from app.schemas.modules import ModuleCreateRequest, module_create_tool_parameters_schema
from app.services.araios.system_modules.module_manager.handlers import (
    _validate_module_create_payload,
)
from app.services.araios.system_modules.module_manager.module import (
    _create_module_parameters_schema,
)
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


def test_module_create_schema_rejects_string_secret_definitions():
    with pytest.raises(ValidationError):
        ModuleCreateRequest.model_validate(
            {
                "name": "interactive_brokers_accounts",
                "label": "Interactive Brokers Accounts",
                "secrets": ["ibkr_bridge_url", "ibkr_bridge_token"],
            }
        )


def test_module_manager_create_payload_uses_module_schema():
    with pytest.raises(ValueError):
        _validate_module_create_payload(
            {
                "name": "interactive_brokers_accounts",
                "label": "Interactive Brokers Accounts",
                "secrets": ["ibkr_bridge_url", "ibkr_bridge_token"],
            }
        )


def test_module_manager_tool_schema_is_generated_from_module_create_schema():
    schema = _create_module_parameters_schema()

    assert schema == module_create_tool_parameters_schema()
    assert "$defs" not in schema
    assert schema["properties"]["secrets"]["items"]["required"] == ["key", "label"]


def test_module_manager_registers_with_generated_create_schema():
    from app.services.tools.registry_builder import build_default_registry

    registry = build_default_registry()

    assert registry.get("module_manager") is not None


def test_create_dynamic_module_stores_validated_secret_definitions(monkeypatch):
    from app.routers.araios import modules as modules_router

    async def noop_rebuild(_request):
        return None

    monkeypatch.setattr(modules_router, "_rebuild_current_instance_runtime", noop_rebuild)
    db = FakeDB(seed_auth=False)
    body = ModuleCreateRequest.model_validate(
        {
            "name": "Interactive_Brokers_Accounts",
            "label": "Interactive Brokers Accounts",
            "secrets": [
                {
                    "key": "ibkr_bridge_url",
                    "label": "IBKR Bridge URL",
                    "required": True,
                },
                {
                    "key": "ibkr_bridge_token",
                    "label": "IBKR Bridge Token",
                    "required": True,
                },
            ],
        }
    )

    _run(
        modules_router._create_dynamic_module(
            body=body,
            request=object(),
            db=db,
        )
    )

    [module] = db.storage[AraiosModule]
    assert module.name == "interactive_brokers_accounts"
    assert module.secrets == [
        {"key": "ibkr_bridge_url", "label": "IBKR Bridge URL", "required": True},
        {"key": "ibkr_bridge_token", "label": "IBKR Bridge Token", "required": True},
    ]
