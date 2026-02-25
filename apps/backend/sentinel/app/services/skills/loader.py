from __future__ import annotations

from pathlib import Path

import yaml

from app.services.skills.types import SkillDefinition


def load_skill_from_markdown(path: Path) -> SkillDefinition:
    text = path.read_text(encoding="utf-8")
    metadata, body = _split_frontmatter(text)

    name = str(metadata.get("name") or "").strip()
    description = str(metadata.get("description") or "").strip()
    if not name:
        raise ValueError(f"Skill file missing 'name': {path}")
    if not description:
        raise ValueError(f"Skill file missing 'description': {path}")

    required_tools = _as_string_list(metadata.get("required_tools", []), field_name="required_tools")
    required_env = _as_string_list(metadata.get("required_env", []), field_name="required_env")
    enabled = bool(metadata.get("enabled", True))

    return SkillDefinition(
        name=name,
        description=description,
        system_prompt_injection=body.strip(),
        required_tools=required_tools,
        required_env=required_env,
        enabled=enabled,
        builtin=False,
    )


def load_builtin_skills(directory: Path) -> list[SkillDefinition]:
    skills: list[SkillDefinition] = []
    for skill_path in sorted(directory.glob("*/SKILL.md")):
        skill = load_skill_from_markdown(skill_path)
        skill.builtin = True
        skills.append(skill)
    return skills


def _split_frontmatter(text: str) -> tuple[dict, str]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter")

    marker = "\n---\n"
    end_idx = normalized.find(marker, 4)
    if end_idx == -1:
        raise ValueError("SKILL.md frontmatter closing '---' not found")

    frontmatter_raw = normalized[4:end_idx]
    body = normalized[end_idx + len(marker) :]
    try:
        metadata = yaml.safe_load(frontmatter_raw) or {}
    except yaml.YAMLError as exc:
        raise ValueError("Invalid YAML frontmatter") from exc

    if not isinstance(metadata, dict):
        raise ValueError("YAML frontmatter must be a mapping")
    return metadata, body


def _as_string_list(value: object, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")

    output: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} must contain only strings")
        trimmed = item.strip()
        if trimmed:
            output.append(trimmed)
    return output
