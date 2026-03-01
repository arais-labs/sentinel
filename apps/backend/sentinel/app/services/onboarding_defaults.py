from __future__ import annotations

DEFAULT_AGENT_NAME = "Sentinel"
DEFAULT_AGENT_ROLE = "You are a proactive operator assistant for the user."
DEFAULT_USER_PROFILE_HINT = (
    "When appropriate, ask the user for context about their goals, preferences, constraints, and environment "
    "to fill this memory."
)

_SYSTEM_PROMPT_LINES = (
    "Be concise, factual, and execution-oriented.",
    "Take initiative and complete tasks end-to-end when possible.",
    "Keep the user informed with clear outcomes.",
    "Prefer delegating bounded one-off tasks to sub-agents when continuity is not required; default to permissive sub-agent tool access unless the user requests restrictions, verify delegated results with sub-agent checks before finalizing, and retry with a refined sub-agent objective when delegated output is insufficient.",
)


def _trim(value: str | None) -> str:
    return (value or "").strip()


def resolve_agent_identity(
    *,
    agent_name: str | None,
    agent_role: str | None,
    agent_personality: str | None = None,
) -> tuple[str, str, str]:
    name = _trim(agent_name) or DEFAULT_AGENT_NAME
    role = _trim(agent_role) or DEFAULT_AGENT_ROLE
    personality = _trim(agent_personality)
    return name, role, personality


def build_system_prompt(
    *,
    agent_name: str | None = None,
    agent_role: str | None = None,
    agent_personality: str | None = None,
) -> str:
    name, role, personality = resolve_agent_identity(
        agent_name=agent_name,
        agent_role=agent_role,
        agent_personality=agent_personality,
    )
    parts = [f"You are {name}.", role, *_SYSTEM_PROMPT_LINES]
    if personality:
        parts.append(f"Personality: {personality}")
    return " ".join(parts)


def build_agent_identity_memory(
    *,
    agent_name: str | None = None,
    agent_role: str | None = None,
    agent_personality: str | None = None,
) -> str:
    name, role, personality = resolve_agent_identity(
        agent_name=agent_name,
        agent_role=agent_role,
        agent_personality=agent_personality,
    )
    parts = [f"You are {name}.", f"Role: {role}"]
    behavior = (
        "Behavior: Be concise, factual, and execution-oriented. Take initiative and complete tasks end-to-end "
        "when possible. Keep the user informed with clear outcomes. "
        "Prefer delegating bounded one-off tasks to sub-agents when continuity is not required; default to permissive "
        "sub-agent tool access unless the user requests restrictions, verify delegated results with sub-agent checks "
        "before finalizing, and retry with a refined sub-agent objective when delegated output is insufficient."
    )
    parts.append(behavior)
    if personality:
        parts.append(f"Personality: {personality}")
    return "\n".join(parts)


DEFAULT_SYSTEM_PROMPT = build_system_prompt()
DEFAULT_AGENT_IDENTITY_MEMORY = build_agent_identity_memory()
DEFAULT_USER_PROFILE_MEMORY = (
    "The user's name and detailed profile are not known yet.\n\n"
    f"{DEFAULT_USER_PROFILE_HINT}"
)
