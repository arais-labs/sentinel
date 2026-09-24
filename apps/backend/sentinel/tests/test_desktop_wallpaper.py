import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = (
    Path(__file__).parents[1] / "app/services/runtime/guest_commands/linux/desktop/set_wallpaper.py"
)


@pytest.mark.parametrize(
    "desktop,command",
    [
        ("gnome", "gsettings"),
        ("plasma", "plasma-apply-wallpaperimage"),
        ("lxqt", "pcmanfm-qt"),
        ("xfce", "xfconf-query"),
    ],
)
def test_wallpaper_uses_session_identity_and_native_settings(
    tmp_path, monkeypatch, desktop, command
):
    spec = importlib.util.spec_from_file_location("wallpaper", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = Path.read_text
    session = {
        "user": {"uid": 123, "gid": 456, "groups": [456], "home": str(tmp_path)},
        "environment": {"HOME": str(tmp_path), "DBUS_SESSION_BUS_ADDRESS": "test"},
    }

    def read(path, *args, **kwargs):
        if str(path) == "/run/sentinel-desktop/session.json":
            return json.dumps(session)
        if str(path) == "/etc/sentinel/desktop-choice":
            return desktop
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    identity = []
    for name in ("setgroups", "setgid", "setuid"):
        monkeypatch.setattr(
            module.os, name, lambda value, name=name: identity.append((name, value))
        )
    monkeypatch.setattr(module.os, "environ", {})
    data = b"\x89PNG\r\n\x1a\ntest"
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(data)))
    calls = []

    def execute(args, **kwargs):
        assert identity == [("setgroups", [456]), ("setgid", 456), ("setuid", 123)]
        calls.append(args)
        return (
            "/backdrop/screen0/monitorVirtual-1/workspace0/last-image\n" if args[-1] == "-l" else ""
        )

    monkeypatch.setattr(module.subprocess, "check_output", execute)
    module.apply()
    assert calls and all(args[0] == command for args in calls)
    images = list((tmp_path / ".local/share/backgrounds/sentinel").glob("*.png"))
    assert len(images) == 1
    assert images[0].read_bytes() == data


def test_approved_gallery_is_bundled():
    assets = SOURCE.parent / "wallpapers"
    assert {p.stem for p in assets.glob("*.png")} == {
        "satin",
        "horizon",
        "geometry",
        "spectrum",
        "paper",
        "monochrome",
    }
    assert all(p.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for p in assets.glob("*.png"))
