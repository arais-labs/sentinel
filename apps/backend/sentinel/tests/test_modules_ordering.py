from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.models.araios import AraiosModule, AraiosModuleRecord, AraiosModuleSecret
from app.routers.araios.modules import list_modules
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


def _dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def test_modules_are_ordered_by_recent_activity_with_native_modules_last():
    db = FakeDB(seed_auth=False)
    alpha = AraiosModule(
        name="alpha",
        label="Alpha",
        order=1,
        created_at=_dt(1),
        updated_at=_dt(1),
    )
    beta = AraiosModule(
        name="beta",
        label="Beta",
        order=1,
        created_at=_dt(1),
        updated_at=_dt(1),
    )
    gamma = AraiosModule(
        name="gamma",
        label="Gamma",
        order=1,
        created_at=_dt(1),
        updated_at=_dt(1),
    )
    db.add(alpha)
    db.add(beta)
    db.add(gamma)
    db.add(
        AraiosModuleRecord(
            id="beta-record",
            module_name="beta",
            data={},
            created_at=_dt(2),
            updated_at=_dt(4),
        )
    )
    db.add(
        AraiosModuleSecret(
            module_name="gamma",
            key="token",
            value="redacted",
            updated_at=_dt(5),
        )
    )

    response = _run(list_modules(db=db))
    modules = response["modules"]

    assert [module["name"] for module in modules[:3]] == ["gamma", "beta", "alpha"]
    assert modules[0]["last_changed_at"] == _dt(5).isoformat()
    assert modules[1]["last_changed_at"] == _dt(4).isoformat()
    assert modules[2]["last_changed_at"] == _dt(1).isoformat()
    assert all(not module.get("native") for module in modules[:3])
    assert all(module.get("native") for module in modules[3:])
