from app.services.runtime.development_tools import STACKS, TOOL_IDS
from app.services.runtime.distributions import APT_TOOLS, validate_distribution_tools


def test_all_stacks_available_on_ubuntu_and_debian():
    assert APT_TOOLS == TOOL_IDS
    for distribution in ("ubuntu", "debian"):
        for stack in STACKS:
            validate_distribution_tools(distribution, stack["tools"])
