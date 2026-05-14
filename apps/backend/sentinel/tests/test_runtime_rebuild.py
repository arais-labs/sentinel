from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.instance_runtime_context import InstanceRuntimeContext
from app.services.runtime.runtime_rebuild import RuntimeRebuildService


class _FakeRegistry:
    def list_all(self):
        return []


class _FakeScheduler:
    def __init__(self) -> None:
        self.agent_runtime_support = object()

    def set_agent_runtime_support(self, agent_runtime_support):
        self.agent_runtime_support = agent_runtime_support


class _FakeContextBuilder:
    def __init__(self, default_system_prompt, available_tools, memory_search_service):
        self.default_system_prompt = default_system_prompt
        self.available_tools = available_tools
        self.memory_search_service = memory_search_service


class _FakeRuntimeSupport:
    def __init__(self, provider, context_builder, tool_registry, tool_executor):
        self.provider = provider
        self.context_builder = context_builder
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor


def test_rebuild_agent_runtime_support_syncs_scheduler(monkeypatch):
    import app.services.runtime.runtime_rebuild as runtime_rebuild_module

    monkeypatch.setattr(
        runtime_rebuild_module,
        "build_tier_provider_from_settings",
        lambda _settings: object(),
    )
    monkeypatch.setattr(runtime_rebuild_module, "ContextBuilder", _FakeContextBuilder)
    monkeypatch.setattr(runtime_rebuild_module, "SentinelRuntimeSupport", _FakeRuntimeSupport)

    scheduler = _FakeScheduler()
    app_state = SimpleNamespace(
        llm_provider=None,
        agent_runtime_support=None,
        tool_registry=_FakeRegistry(),
        tool_executor=object(),
        memory_search_service=None,
        trigger_scheduler=scheduler,
        telegram_bridge=None,
    )

    RuntimeRebuildService().rebuild_agent_runtime_support(app_state)

    assert app_state.agent_runtime_support is not None
    assert scheduler.agent_runtime_support is app_state.agent_runtime_support


def test_rebuild_agent_runtime_support_with_no_provider_clears_scheduler(monkeypatch):
    import app.services.runtime.runtime_rebuild as runtime_rebuild_module

    monkeypatch.setattr(
        runtime_rebuild_module,
        "build_tier_provider_from_settings",
        lambda _settings: None,
    )

    scheduler = _FakeScheduler()
    app_state = SimpleNamespace(
        llm_provider=object(),
        agent_runtime_support=object(),
        trigger_scheduler=scheduler,
    )

    RuntimeRebuildService().rebuild_agent_runtime_support(app_state)

    assert app_state.agent_runtime_support is None
    assert scheduler.agent_runtime_support is None


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
