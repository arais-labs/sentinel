"""Supported ARM64 workspace root filesystems and provisioning capabilities."""

from typing import Literal

from app.services.runtime.development_tools import TOOL_IDS

Distribution = Literal["alpine", "ubuntu", "debian"]

APT_TOOLS = TOOL_IDS
DISTRIBUTIONS = [
    {
        "id": "alpine",
        "name": "Alpine Linux",
        "detail": "Default · all tools and accelerated desktop",
        "tools": None,
    },
    {
        "id": "ubuntu",
        "name": "Ubuntu 26.04 LTS",
        "detail": "All stacks and accelerated desktop",
        "tools": sorted(APT_TOOLS),
    },
    {
        "id": "debian",
        "name": "Debian 13",
        "detail": "All stacks and accelerated desktop",
        "tools": sorted(APT_TOOLS),
    },
]


def validate_distribution_tools(distribution: str, tools: list[str]) -> None:
    if distribution not in {item["id"] for item in DISTRIBUTIONS}:
        raise ValueError("Unknown workspace distribution")
    if distribution != "alpine" and (unsupported := set(tools) - APT_TOOLS):
        raise ValueError(f"Tools not supported on {distribution}: {', '.join(sorted(unsupported))}")
