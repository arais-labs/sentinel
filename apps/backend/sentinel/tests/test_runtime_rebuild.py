from __future__ import annotations

from types import SimpleNamespace

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


class _FakeToolAdapter:
    def __init__(self, tool_registry, tool_executor, session_factory):
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.session_factory = session_factory


class _FakeRuntimeSupport:
    def __init__(self, provider, context_builder, tool_adapter):
        self.provider = provider
        self.context_builder = context_builder
        self.tool_adapter = tool_adapter


def test_rebuild_agent_runtime_support_syncs_scheduler(monkeypatch):
    import app.services.runtime.runtime_rebuild as runtime_rebuild_module

    monkeypatch.setattr(
        runtime_rebuild_module,
        "build_tier_provider_from_settings",
        lambda _settings: object(),
    )
    monkeypatch.setattr(runtime_rebuild_module, "ContextBuilder", _FakeContextBuilder)
    monkeypatch.setattr(runtime_rebuild_module, "ToolAdapter", _FakeToolAdapter)
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
