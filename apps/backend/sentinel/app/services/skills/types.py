from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SkillDefinition:
    name: str
    description: str
    system_prompt_injection: str
    required_tools: list[str] = field(default_factory=list)
    required_env: list[str] = field(default_factory=list)
    enabled: bool = True
    builtin: bool = False
