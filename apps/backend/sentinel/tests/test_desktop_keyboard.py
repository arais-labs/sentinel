import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.runtime import desktop_keyboard as keyboard


def test_input_source_mapping_uses_identifier_not_locale(monkeypatch):
    monkeypatch.setattr(
        keyboard, "SOURCE_LAYOUTS", {"test.source": ("test_layout", "test_variant")}
    )
    run = Mock(return_value=SimpleNamespace(stdout="test.source\n"))
    monkeypatch.setattr(keyboard.subprocess, "run", run)
    assert keyboard._read_keyboard() == {
        "layout": "test_layout",
        "variant": "test_variant",
        "model": "apple",
        "options": "",
    }
    assert run.call_args.kwargs["timeout"] == 2
    assert run.call_args.args[0][-1] == "AppleCurrentKeyboardLayoutInputSourceID"


def test_unknown_source_does_not_guess(monkeypatch):
    monkeypatch.setattr(
        keyboard.subprocess, "run", Mock(return_value=SimpleNamespace(stdout="unknown.source\n"))
    )
    assert keyboard._read_keyboard() is None


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


@pytest.mark.asyncio
async def test_only_explicit_start_transports_host_layout(monkeypatch):
    from app.services.runtime import desktop
    import json

    defaults = {"layout": "test_layout", "variant": "test_variant"}
    detect = AsyncMock(return_value=defaults)
    monkeypatch.setattr(desktop, "host_keyboard", detect)
    # Capture the structured request before shell encoding.
    command = Mock(return_value="guest-command")
    monkeypatch.setattr(desktop, "guest_python_command", command)
    transport = SimpleNamespace(
        run=AsyncMock(
            return_value=SimpleNamespace(
                stdout='{"ok":true}',
                stderr="",
                exit_status=0,
            )
        )
    )
    manager = desktop.RuntimeDesktopManager(SimpleNamespace(ssh=transport), workspace_location=None)
    await manager._command("start", "1280x800")
    assert transport.run.call_args.kwargs["timeout"] == 90
    assert json.loads(command.call_args.args[1][0])["keyboard"] == defaults
    detect.assert_awaited_once()
    detect.reset_mock()
    for action in ("status", "stop"):
        await manager._command(action)
        assert transport.run.call_args.kwargs["timeout"] == 25
        assert json.loads(command.call_args.args[1][0])["keyboard"] is None
    detect.assert_not_awaited()
