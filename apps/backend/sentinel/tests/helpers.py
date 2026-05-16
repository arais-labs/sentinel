from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from starlette.requests import HTTPConnection

from app import main as app_main
from app.dependencies import get_db, get_manager_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from tests.fake_db import FakeDB


async def _noop_init_db() -> None:
    return None


class FakeSessionContext:
    def __init__(self, db: FakeDB) -> None:
        self._db = db

    async def __aenter__(self) -> FakeDB:
        return self._db

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class FakeSessionFactory:
    def __init__(self, db: FakeDB) -> None:
        self._db = db

    def __call__(self) -> FakeSessionContext:
        return FakeSessionContext(self._db)


def install_fake_db_overrides(
    *,
    app_db: FakeDB,
    manager_db: FakeDB | None = None,
    instance_context: Any | None = None,
    session_factory: Any | None = None,
) -> object:
    manager_db = manager_db or app_db
    session_factory = session_factory or FakeSessionFactory(app_db)
    instance_context = instance_context or make_fake_instance_context(
        app_db=app_db,
        session_factory=session_factory,
    )

    async def _override_get_db(connection: HTTPConnection) -> AsyncGenerator[FakeDB, None]:
        connection.state.instance_name = "main"
        connection.state.instance_database_name = "sentinel_main_test"
        connection.state.db_session_factory = session_factory
        if instance_context is not None:
            connection.state.instance_runtime_context = instance_context
        yield app_db

    async def _override_get_manager_db() -> AsyncGenerator[FakeDB, None]:
        yield manager_db

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_manager_db
    old_init_db = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    return old_init_db


def make_fake_instance_context(
    *,
    app_db: FakeDB,
    agent_runtime_support: Any | None = None,
    session_factory: Any | None = None,
) -> Any:
    from app.config import settings
    from app.services.instance_runtime_context import InstanceRuntimeContext
    from app.services.sub_agents import SubAgentOrchestrator
    from app.services.tools import ToolExecutor, ToolRegistry
    from app.services.triggers.trigger_scheduler import TriggerScheduler

    session_factory = session_factory or FakeSessionFactory(app_db)
    tool_registry = ToolRegistry()
    tool_executor = ToolExecutor(tool_registry)
    return InstanceRuntimeContext(
        name="main",
        database_name="sentinel_main_test",
        instance_settings=settings,
        session_factory=session_factory,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        agent_runtime_support=agent_runtime_support,
        trigger_scheduler=TriggerScheduler(
            agent_runtime_support=agent_runtime_support,
            tool_executor=tool_executor,
            db_factory=None,
        ),
        sub_agent_orchestrator=SubAgentOrchestrator(),
        background_tasks=[],
    )


def restore_test_app(old_init_db: object) -> None:
    app.dependency_overrides.clear()
    app_main.init_db = old_init_db
