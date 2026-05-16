from __future__ import annotations

import asyncio

from app.models import Session
from app.services.agent.agent_modes import AgentMode
from app.services.agent.context_builder import ContextBuilder
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


def _system_text(messages) -> str:
    return "\n".join(
        item.content for item in messages if getattr(item, "role", "") == "system"
    )


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
