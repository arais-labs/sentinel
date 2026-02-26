from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.models import Session
from app.services.agent.context_builder import ContextBuilder
from app.services.skills import (
    SkillDefinition,
    SkillRegistry,
    load_builtin_skills,
    load_skill_from_markdown,
)
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


def _builtin_dir() -> Path:
    return (
        Path(__file__).resolve().parents[1] / "app" / "services" / "skills" / "builtin"
    )


def test_load_skill_from_markdown_parses_frontmatter_and_body(tmp_path: Path):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(
        """---
name: custom-skill
description: Custom skill description
required_tools: [shell_exec, file_read]
required_env: [CUSTOM_KEY]
enabled: true
---

## Custom Skill

Use custom logic here.
""",
        encoding="utf-8",
    )

    skill = load_skill_from_markdown(skill_file)
    assert skill.name == "custom-skill"
    assert skill.description == "Custom skill description"
    assert skill.required_tools == ["shell_exec", "file_read"]
    assert skill.required_env == ["CUSTOM_KEY"]
    assert skill.enabled is True
    assert "## Custom Skill" in skill.system_prompt_injection


def test_load_builtin_skills_loads_three_builtin_skills():
    skills = load_builtin_skills(_builtin_dir())
    names = {skill.name for skill in skills}
    assert names == {"code-assistant", "research", "operator"}
    assert all(skill.builtin is True for skill in skills)


def test_skill_registry_list_active_gates_by_tools_and_env():
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="ops",
            description="Ops",
            system_prompt_injection="ops",
            required_tools=["shell_exec"],
            required_env=[],
        )
    )
    registry.register(
        SkillDefinition(
            name="secret",
            description="Secret",
            system_prompt_injection="secret",
            required_tools=[],
            required_env=["CUSTOM_KEY"],
        )
    )
    registry.register(
        SkillDefinition(
            name="plain",
            description="Plain",
            system_prompt_injection="plain",
            required_tools=[],
            required_env=[],
        )
    )

    active_without = {skill.name for skill in registry.list_active({"file_read"}, {})}
    assert active_without == {"plain"}

    active_with = {
        skill.name
        for skill in registry.list_active(
            {"file_read", "shell_exec"}, {"CUSTOM_KEY": "set"}
        )
    }
    assert active_with == {"ops", "secret", "plain"}


def test_loader_defaults_and_malformed_input(tmp_path: Path):
    minimal = tmp_path / "minimal.md"
    minimal.write_text(
        """---
name: minimal
description: minimal skill
---

Minimal body.
""",
        encoding="utf-8",
    )
    loaded = load_skill_from_markdown(minimal)
    assert loaded.enabled is True
    assert loaded.required_env == []
    assert loaded.required_tools == []

    malformed = tmp_path / "malformed.md"
    malformed.write_text("No frontmatter", encoding="utf-8")
    with pytest.raises(ValueError):
        load_skill_from_markdown(malformed)

    bad_yaml = tmp_path / "bad_yaml.md"
    bad_yaml.write_text(
        """---
name: bad
description: bad
required_tools: [one
---

Body
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_skill_from_markdown(bad_yaml)


def test_context_builder_injects_and_removes_skill_prompts():
    registry = SkillRegistry()
    for skill in load_builtin_skills(_builtin_dir()):
        registry.register(skill)

    db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="skills")
    db.add(session)

    builder = ContextBuilder(
        default_system_prompt="Base system prompt",
        skill_registry=registry,
        available_tools={"shell_exec", "file_read", "file_write", "http_request"},
    )
    context = _run(builder.build(db, session.id))
    system_messages = [
        item.content for item in context if getattr(item, "role", "") == "system"
    ]
    joined = "\n".join(system_messages)
    assert "Active Skill: code-assistant" in joined
    assert "Code Assistant" in joined

    assert registry.disable("code-assistant") is True
    context_after_disable = _run(builder.build(db, session.id))
    system_after_disable = [
        item.content
        for item in context_after_disable
        if getattr(item, "role", "") == "system"
    ]
    assert "Active Skill: code-assistant" not in "\n".join(system_after_disable)


def test_context_builder_excludes_skills_with_unmet_tool_requirements():
    registry = SkillRegistry()
    for skill in load_builtin_skills(_builtin_dir()):
        registry.register(skill)

    db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="skills")
    db.add(session)

    builder = ContextBuilder(
        default_system_prompt="Base system prompt",
        skill_registry=registry,
        available_tools={"shell_exec", "file_read", "http_request"},
    )
    context = _run(builder.build(db, session.id))
    text = "\n".join(
        item.content for item in context if getattr(item, "role", "") == "system"
    )
    assert "Active Skill: code-assistant" not in text
    assert "Active Skill: operator" in text
    assert "Active Skill: research" in text


def test_context_builder_injects_browser_playbook_when_browser_tools_available():
    db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="browser-policy")
    db.add(session)

    builder = ContextBuilder(
        default_system_prompt="Base system prompt",
        available_tools={
            "browser_navigate",
            "browser_snapshot",
            "browser_click",
            "browser_type",
            "browser_select",
            "browser_wait_for",
            "browser_get_value",
            "browser_tabs",
            "browser_tab_focus",
        },
    )
    context = _run(builder.build(db, session.id))
    text = "\n".join(
        item.content for item in context if getattr(item, "role", "") == "system"
    )
    assert "## Browser Automation Playbook" in text
    assert (
        "navigate -> snapshot(interactive_only=true) -> interact -> verify -> continue"
        in text
    )
    assert "prefer browser_fill_form" in text
