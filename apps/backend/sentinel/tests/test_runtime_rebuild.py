from __future__ import annotations

from types import SimpleNamespace

from app.services.runtime_rebuild import RuntimeRebuildService


class _FakeRegistry:
    def list_all(self):
        return []


class _FakeScheduler:
    def __init__(self) -> None:
        self.agent_loop = object()

    def set_agent_loop(self, agent_loop):
        self.agent_loop = agent_loop


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


class _FakeAgentLoop:
    def __init__(self, provider, context_builder, tool_adapter):
        self.provider = provider
        self.context_builder = context_builder
        self.tool_adapter = tool_adapter


def test_rebuild_agent_loop_syncs_scheduler_loop(monkeypatch):
    import app.services.runtime_rebuild as runtime_rebuild_module

    monkeypatch.setattr(
        runtime_rebuild_module,
        "build_tier_provider_from_settings",
        lambda _settings: object(),
    )
    monkeypatch.setattr(runtime_rebuild_module, "ContextBuilder", _FakeContextBuilder)
    monkeypatch.setattr(runtime_rebuild_module, "ToolAdapter", _FakeToolAdapter)
    monkeypatch.setattr(runtime_rebuild_module, "AgentLoop", _FakeAgentLoop)

    scheduler = _FakeScheduler()
    app_state = SimpleNamespace(
        llm_provider=None,
        agent_loop=None,
        tool_registry=_FakeRegistry(),
        tool_executor=object(),
        memory_search_service=None,
        trigger_scheduler=scheduler,
        telegram_bridge=None,
    )

    RuntimeRebuildService().rebuild_agent_loop(app_state)

    assert app_state.agent_loop is not None
    assert scheduler.agent_loop is app_state.agent_loop


def test_rebuild_agent_loop_with_no_provider_clears_scheduler_loop(monkeypatch):
    import app.services.runtime_rebuild as runtime_rebuild_module

    monkeypatch.setattr(
        runtime_rebuild_module,
        "build_tier_provider_from_settings",
        lambda _settings: None,
    )

    scheduler = _FakeScheduler()
    app_state = SimpleNamespace(
        llm_provider=object(),
        agent_loop=object(),
        trigger_scheduler=scheduler,
    )

    RuntimeRebuildService().rebuild_agent_loop(app_state)

    assert app_state.agent_loop is None
    assert scheduler.agent_loop is None
