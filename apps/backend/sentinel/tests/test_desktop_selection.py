from types import SimpleNamespace

import pytest

from app.services.runtime.desktop import RuntimeDesktopManager


@pytest.mark.parametrize(
    ("selection", "enabled"),
    [
        ("none", False),
        ("xfce", True),
        ("weston", True),
        ("lxqt", True),
        ("gnome", True),
        ("plasma", True),
        ("unknown", False),
    ],
)
def test_desktop_manager_enables_explicit_supported_choices(selection, enabled):
    manager = RuntimeDesktopManager(
        SimpleNamespace(ssh=None),
        workspace_location=SimpleNamespace(desktop=selection),
    )
    assert manager.enabled is enabled
