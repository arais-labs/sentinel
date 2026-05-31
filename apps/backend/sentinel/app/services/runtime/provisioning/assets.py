from __future__ import annotations

from importlib import resources
from pathlib import Path


def ansible_config_path() -> Path:
    return Path(
        str(resources.files("app.services.runtime.provisioning.ansible").joinpath("ansible.cfg"))
    )


def ansible_playbook_path() -> Path:
    return Path(
        str(
            resources.files("app.services.runtime.provisioning.ansible").joinpath(
                "sentinel-runtime.yml"
            )
        )
    )
