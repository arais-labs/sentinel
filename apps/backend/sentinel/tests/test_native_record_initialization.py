import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.database import runtime_db_session_factory
from app.database.initialization import _seed_app_defaults
from app.database.engine import create_database_engine
from app.models import Base
from app.models.modules import Module
from app.services.modules.builtins.documents import handlers as documents
from app.services.modules.builtins.tasks import handlers as tasks


@pytest.mark.asyncio
async def test_fresh_instance_can_store_native_documents_and_tasks(tmp_path, monkeypatch):
    # This regression exercises record initialization, not the unrelated tool catalog.
    from app.services.modules import permissions

    monkeypatch.setattr(permissions, "combined_agent_permissions", lambda: {})
    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path}/instance.sqlite")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _seed_app_defaults(factory)
        with runtime_db_session_factory(factory):
            document = await documents.handle_create(
                {
                    "title": "Audit",
                    "slug": "audit",
                    "author": "Sentinel",
                    "content": "Findings",
                }
            )
            task = await tasks.handle_create({"title": "Review audit"})
            # Initialization is repeatable and must not replace persisted records.
            await _seed_app_defaults(factory)
            assert (await documents.handle_get({"slug": "audit"}))["content"] == "Findings"
            assert [item["id"] for item in (await tasks.handle_list({}))["tasks"]] == [task["id"]]
            updated = await documents.handle_update({"id": document["id"], "content": "Reviewed"})
            assert updated["version"] == 2
        async with factory() as db:
            rows = list(await db.scalars(select(Module)))
            assert {row.name for row in rows} == {"documents", "tasks"}
            assert all(row.system for row in rows)
    finally:
        await engine.dispose()
