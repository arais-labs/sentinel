"""Apply an explicitly selected wallpaper through the active desktop settings."""

import json
import os
from pathlib import Path
import subprocess
import sys


def apply():
    session = json.loads(Path("/run/sentinel-desktop/session.json").read_text())
    desktop = Path("/etc/sentinel/desktop-choice").read_text().strip()
    user = session["user"]
    os.setgroups(user["groups"])
    os.setgid(user["gid"])
    os.setuid(user["uid"])
    os.environ.update(session["environment"])
    data = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Invalid wallpaper image")
    # Content-addressed files avoid stale desktop image caches on a new choice.
    import hashlib

    directory = Path(user["home"]) / ".local/share/backgrounds/sentinel"
    directory.mkdir(parents=True, exist_ok=True)
    image = directory / (hashlib.sha256(data).hexdigest() + ".png")
    with image.open("wb") as stream:
        stream.write(data)

    def run(*args):
        return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=10)

    if desktop == "gnome":
        for key in ("picture-uri", "picture-uri-dark"):
            run("gsettings", "set", "org.gnome.desktop.background", key, image.as_uri())
        run("gsettings", "set", "org.gnome.desktop.background", "picture-options", "zoom")
    elif desktop == "plasma":
        run("plasma-apply-wallpaperimage", str(image))
    elif desktop == "lxqt":
        run("pcmanfm-qt", "--set-wallpaper", str(image), "--wallpaper-mode=zoom")
    elif desktop == "xfce":
        properties = run("xfconf-query", "-c", "xfce4-desktop", "-l").splitlines()
        targets = [p for p in properties if p.endswith("/last-image")]
        if not targets:
            raise RuntimeError("XFCE has no active wallpaper output")
        for prop in targets:
            run("xfconf-query", "-c", "xfce4-desktop", "-p", prop, "-s", str(image))
            run(
                "xfconf-query",
                "-c",
                "xfce4-desktop",
                "-p",
                prop.rsplit("/", 1)[0] + "/image-style",
                "-s",
                "5",
            )
    else:
        raise ValueError("Unsupported desktop wallpaper settings")


if __name__ == "__main__":
    try:
        apply()
        print(json.dumps({"ok": True}))
    except Exception as error:
        print(json.dumps({"ok": False, "reason": str(error)}))
        sys.exit(1)
