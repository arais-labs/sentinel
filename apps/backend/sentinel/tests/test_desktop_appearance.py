import configparser
import importlib.util
from pathlib import Path
from xml.etree import ElementTree

import pytest

from app.services.runtime.desktop_appearance import WALLPAPER, desktop_default_files


@pytest.fixture
def guest_session(monkeypatch):
    source = (
        Path(__file__).resolve().parents[4]
        / "apps/desktop/sentinel/native/graphics/guest/desktop-session.py"
    )
    # Match Python's script-directory import path inside the installed guest.
    monkeypatch.syspath_prepend(str(source.parent))
    spec = importlib.util.spec_from_file_location("appearance_guest_session", source)
    session = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(session)
    return session


def test_panel_launcher_does_not_override_system_browser_menu_entry():
    files = desktop_default_files()
    assert ".local/share/applications/sentinel-browser.desktop" not in files
    application = files[".config/xfce4/panel/launcher-2/sentinel-browser.desktop"]
    entry = configparser.ConfigParser(interpolation=None)
    entry.read_string(application)
    assert entry["Desktop Entry"]["Exec"] == "sentinel-browser %U"
    assert entry["Desktop Entry"]["Terminal"] == "false"
    assert "--no-sandbox" not in application
    assert "/root" not in application


def test_defaults_use_guest_home_and_preserve_existing_preferences(tmp_path, guest_session):
    session = guest_session
    home = tmp_path / "desktop & user"
    home.mkdir()
    files = desktop_default_files()
    session.seed_preferences(files, home)
    wallpaper_config = home / ".config/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml"
    tree = ElementTree.fromstring(wallpaper_config.read_text())
    wallpaper = tree.find(".//property[@name='last-image']")
    assert wallpaper.attrib["value"] == str(home / WALLPAPER)
    assert "__HOME__" not in wallpaper_config.read_text()
    launcher = home / ".config/xfce4/panel/launcher-2/sentinel-browser.desktop"
    launcher.write_text("user launcher")
    wallpaper_config.write_text("user wallpaper")
    session.seed_preferences(files, home)
    assert launcher.read_text() == "user launcher"
    assert wallpaper_config.read_text() == "user wallpaper"


def test_defaults_cannot_write_arbitrary_application_entries(tmp_path, guest_session):
    session = guest_session
    with pytest.raises(ValueError, match="preference path"):
        session.seed_preferences({".local/share/applications/arbitrary.desktop": "bad"}, tmp_path)
