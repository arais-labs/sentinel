from __future__ import annotations

import os

from app.services.skills.types import SkillDefinition


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> SkillDefinition | None:
        return self._skills.get(name)

    def list_all(self) -> list[SkillDefinition]:
        return sorted(self._skills.values(), key=lambda item: item.name)

    def list_active(
        self,
        available_tools: set[str],
        env: dict[str, str] | None = None,
    ) -> list[SkillDefinition]:
        current_env = env if env is not None else dict(os.environ)
        active: list[SkillDefinition] = []
        for skill in self.list_all():
            if not skill.enabled:
                continue
            if any(tool not in available_tools for tool in skill.required_tools):
                continue
            if any(key not in current_env or not str(current_env.get(key) or "").strip() for key in skill.required_env):
                continue
            active.append(skill)
        return active

    def enable(self, name: str) -> bool:
        skill = self.get(name)
        if skill is None:
            return False
        skill.enabled = True
        return True

    def disable(self, name: str) -> bool:
        skill = self.get(name)
        if skill is None:
            return False
        skill.enabled = False
        return True
