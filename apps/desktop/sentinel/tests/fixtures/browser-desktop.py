"""Verify the ordinary user can resolve the selected desktop browser entry."""

import json
import os
from pathlib import Path

session = json.loads(Path("/run/sentinel-desktop/session.json").read_text())
user = session["user"]
os.setgroups(user["groups"])
os.setgid(user["gid"])
os.setuid(user["uid"])
os.environ.clear()
os.environ.update(session["environment"])

import gi  # noqa: E402 — initialize GTK only after entering the desktop user's environment

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, Gtk  # noqa: E402 — requires the GTK version selection above

Gtk.init([])
app = Gio.DesktopAppInfo.new("sentinel-browser.desktop")
assert app is not None, "Selected browser desktop entry is absent"
icon = app.get_icon()
resolved = (
    Gtk.IconTheme.get_default().lookup_by_gicon(icon, 48, Gtk.IconLookupFlags.FORCE_SIZE)
    if icon
    else None
)
defaults = {}
for scheme in ("http", "https"):
    default = Gio.AppInfo.get_default_for_uri_scheme(scheme)
    defaults[scheme] = default.get_id() if default else None
default = Gio.AppInfo.get_default_for_type("text/html", False)
defaults["text/html"] = default.get_id() if default else None
result = {
    "uid": os.getuid(),
    "selection": Path("/etc/sentinel/browser-selection").read_text().strip(),
    "name": app.get_name(),
    "visible": app.should_show(),
    "executable": app.get_executable(),
    "commandline": app.get_commandline(),
    "icon": icon.to_string() if icon else None,
    "resolved_icon": resolved.get_filename() if resolved else None,
    "defaults": defaults,
}
print(json.dumps(result, sort_keys=True))
assert result["uid"] > 0
assert result["visible"], result
assert result["executable"] == "sentinel-browser", result
assert resolved is not None, result
assert all(value == app.get_id() for value in defaults.values()), result
