from __future__ import annotations

import asyncio

from app.models import Session
from app.services.agent.agent_modes import AgentMode
from app.services.agent.context_builder import ContextBuilder
from app.services.runtime.workspace import WorkspaceLocation
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


def _system_text(messages) -> str:
    return "\n".join(item.content for item in messages if getattr(item, "role", "") == "system")


def test_normal_mode_system_prompt_omits_html_artifact_rules():
    db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="ctx-normal")
    db.add(session)

    builder = ContextBuilder(default_system_prompt="Base")
    context = _run(builder.build(db, session.id, agent_mode=AgentMode.NORMAL))
    text = _system_text(context)

    assert "<!-- sentinel:html -->" not in text
    assert "<!-- sentinel:html-raw -->" not in text
    assert ".sentinel-table" not in text


def test_interactive_output_mode_system_prompt_includes_marker_contract_and_class_reference():
    db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="ctx-interactive")
    db.add(session)

    builder = ContextBuilder(default_system_prompt="Base")
    context = _run(builder.build(db, session.id, agent_mode=AgentMode.INTERACTIVE_OUTPUT))
    text = _system_text(context)

    assert "<!-- sentinel:html -->" in text
    assert "<!-- sentinel:html-raw -->" in text
    assert ".sentinel-table" in text
    # CSS body must NOT be inlined into the prompt — the prompt only references
    # class names. The CSS is injected at message-persist time by
    # post_process_assistant_html.
    assert "--sentinel-bg" not in text
    assert "prefers-color-scheme" not in text


def test_runtime_context_reports_detected_os_and_shared_workspace(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    from app.services.runtime import ssh_runtime
    from app.services.runtime.environment import RuntimeEnvironment

    parent_id = uuid4()
    child_id = uuid4()

    async def environment():
        return RuntimeEnvironment("linux", "container")

    async def manager(**kwargs):
        assert kwargs == {"instance_name": "work", "session_id": parent_id}
        return SimpleNamespace(
            runtime_environment=environment,
            workspace_location=WorkspaceLocation("/Users/test/project", "/var/lib/sentinel"),
        )

    monkeypatch.setattr(ssh_runtime, "get_runtime_terminal_manager", manager)
    builder = ContextBuilder(
        instance_name="work", runtime_session_id=parent_id, available_tools={"runtime"}
    )
    message = _run(builder._runtime_environment_message(child_id))
    assert "Workspace OS: Linux" in message.content
    assert "Attached workspace directory: /Users/test/project" in message.content
    assert str(child_id) not in message.content
    assert "explicit pane IDs" in message.content
    message = _run(builder._runtime_environment_message(child_id))
    assert "Workspace OS: Linux" in message.content
    assert "Attached workspace directory: /Users/test/project\n" in message.content
    assert "HOME is /root" in message.content
    assert "/workspace" not in message.content


def test_runtime_context_does_not_invent_environment_when_unavailable(monkeypatch):
    from uuid import uuid4
    from app.services.runtime import ssh_runtime

    async def unavailable(**kwargs):
        raise ConnectionError("offline")

    monkeypatch.setattr(ssh_runtime, "get_runtime_terminal_manager", unavailable)
    builder = ContextBuilder(instance_name="work", available_tools={"runtime"})
    message = _run(builder._runtime_environment_message(uuid4()))
    assert "Use runtime.workspace to inspect the attachment" in message.content
    assert "Attached workspace directory:" not in message.content


def test_runtime_context_uses_selected_distribution_package_manager(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4
    from app.services.runtime import ssh_runtime
    from app.services.runtime.environment import RuntimeEnvironment

    async def environment():
        return RuntimeEnvironment("linux", "container")

    async def manager(**kwargs):
        return SimpleNamespace(
            runtime_environment=environment,
            workspace_location=WorkspaceLocation(
                "/project", "/var/lib/sentinel", distribution="ubuntu"
            ),
        )

    monkeypatch.setattr(ssh_runtime, "get_runtime_terminal_manager", manager)
    builder = ContextBuilder(instance_name="work", available_tools={"runtime"})
    message = _run(builder._runtime_environment_message(uuid4()))
    assert "Ubuntu" in message.content
    # Distribution selection does not identify the installed guest release.
    assert "26.04" not in message.content
    assert "Use apt-get" in message.content
    assert "Use apk" not in message.content
