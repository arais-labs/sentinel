from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import settings
from app.services.instance_runtime_context import InstanceRuntimeContext
from app.services.runtime.runtime_rebuild import RuntimeRebuildService


@pytest.mark.asyncio
async def test_rebuild_request_runtime_support_rebuilds_instance_context(monkeypatch):
    from app.services import instance_runtime_context as runtime_context_module

    rebuilt: list[InstanceRuntimeContext] = []

    async def _rebuild_context(*, app_state, context):
        rebuilt.append(context)
        return context

    monkeypatch.setattr(
        runtime_context_module.instance_runtime_context_registry,
        "rebuild_context",
        _rebuild_context,
    )
    context = InstanceRuntimeContext(
        name="main",
        database_name="sentinel_main_0d6e4079",
        instance_settings=settings,
        session_factory=object(),
        tool_registry=object(),
        tool_executor=object(),
        agent_runtime_support=object(),
        trigger_scheduler=object(),
        sub_agent_orchestrator=object(),
        background_tasks=[],
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace()),
        state=SimpleNamespace(instance_runtime_context=context),
    )

    await RuntimeRebuildService().rebuild_request_runtime_support(request)

    assert rebuilt == [context]


@pytest.mark.asyncio
async def test_rebuild_request_runtime_support_raises_without_instance_context():
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace()),
        state=SimpleNamespace(),
    )

    with pytest.raises(RuntimeError, match="instance-scoped"):
        await RuntimeRebuildService().rebuild_request_runtime_support(request)
