import plistlib
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.workspaces.desktop import keyboard


def layout(name):
    return {
        "InputSourceKind": "Keyboard Layout",
        "KeyboardLayout ID": 0,
        "KeyboardLayout Name": name,
    }


def defaults(current, enabled):
    return SimpleNamespace(
        stdout=plistlib.dumps(
            {
                "AppleCurrentKeyboardLayoutInputSourceID": current,
                "AppleEnabledInputSources": enabled,
            }
        )
    )


def test_enabled_layouts_current_first(monkeypatch):
    run = Mock(
        return_value=defaults(
            "com.apple.keylayout.French",
            [layout("U.S."), {"InputSourceKind": "Non Keyboard Input Method"}, layout("French")],
        )
    )
    monkeypatch.setattr(keyboard.subprocess, "run", run)
    assert keyboard._read_keyboard() == {
        "layout": "fr,us",
        "variant": "mac,mac",
        "model": "pc105",
        "options": "grp:win_space_toggle",
    }
    assert run.call_args.kwargs["timeout"] == 2


def test_single_layout_has_no_switch_shortcut():
    assert keyboard.keyboard_from_sources(
        "com.apple.keylayout.SwissFrench", [layout("Swiss French")]
    ) == {"layout": "ch", "variant": "fr_mac", "model": "pc105", "options": ""}


def test_unknown_layouts_do_not_guess():
    assert (
        keyboard.keyboard_from_sources("com.apple.keylayout.Unknown", [layout("Unknown")]) is None
    )


def test_detection_failure_preserves_guest_configuration(monkeypatch):
    monkeypatch.setattr(
        keyboard.subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("defaults", 2))
    )
    assert keyboard._read_keyboard() is None


@pytest.mark.asyncio
async def test_other_platform_does_not_execute_mac_command(monkeypatch):
    monkeypatch.setattr(keyboard.sys, "platform", "linux")
    run = Mock()
    monkeypatch.setattr(keyboard.subprocess, "run", run)
    assert await keyboard.host_keyboard() is None
    run.assert_not_called()
