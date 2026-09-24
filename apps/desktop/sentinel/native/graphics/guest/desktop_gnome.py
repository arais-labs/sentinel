"""Install Sentinel's GNOME readiness mode using distro-owned session services.

Session 48 passes GNOME_SHELL_SESSION_MODE to Shell. Session 50 instead selects
the mode through org.gnome.Shell@.service's --mode=%i; use its native named-session
target, not a stock service override. Alpine can pair session 48 with Shell 50.
"""

from copy import deepcopy
from pathlib import Path
import re
import shlex
import subprocess

ASSETS = Path(__file__).with_name("session-assets") / "gnome"
UUID = "session-readiness@sentinel.local"
UNIT_DIRECTORIES = (
    "etc/systemd/user",
    "usr/local/lib/systemd/user",
    "usr/lib/systemd/user",
    "lib/systemd/user",
)


def _major(binary):
    result = subprocess.run(
        [str(binary), "--version"], check=True, text=True, capture_output=True, timeout=10
    )
    match = re.search(r"\b(\d+)\.\d+", result.stdout + result.stderr)
    if not match:
        raise RuntimeError(f"Cannot identify installed GNOME version: {binary}")
    return int(match[1])


def _unit(root, name):
    for directory in UNIT_DIRECTORIES:
        if (root / directory / (name + ".d")).exists():
            raise RuntimeError(f"GNOME readiness needs review of the custom {name} override")
    for directory in UNIT_DIRECTORIES:
        path = root / directory / name
        if path.exists():
            return path.read_text()
    raise RuntimeError(f"GNOME readiness requires the stock native unit {name}")


def _command(contents, key):
    lines = re.findall(rf"^{key}=(.*)$", contents, re.MULTILINE)
    if len(lines) != 1:
        raise RuntimeError(f"GNOME readiness expected one stock launcher {key}")
    return shlex.split(lines[0])


def configure_gnome(config, root=Path("/")):
    """Return an updated profile; never change user preferences or stock units.

    Called during explicit desktop provisioning, not reconnect or session start.
    Validate the installed lifecycle and all assets before publishing anything.
    """
    result = deepcopy(config)
    if result.get("command") not in (
        ["/usr/bin/gnome-session"],
        ["/usr/bin/gnome-session", "--session=gnome"],
        ["/usr/bin/gnome-session", "--session=sentinel"],
    ):
        raise RuntimeError(
            "GNOME readiness cannot replace a custom desktop command; review desktop.json"
        )
    environment = result.setdefault("environment", {})
    if environment.get("GNOME_SHELL_SESSION_MODE") not in (None, "user", "sentinel"):
        raise RuntimeError(
            "GNOME readiness cannot replace a custom Shell mode; review desktop.json"
        )
    session = _major(root / "usr/bin/gnome-session")
    shell = _major(root / "usr/bin/gnome-shell")
    if session not in (48, 50) or shell not in (48, 50):
        raise RuntimeError(f"GNOME readiness has not qualified session {session} / Shell {shell}")
    systemd = (root / "run/systemd/system").is_dir()
    files = {"usr/local/share/gnome-shell/modes/sentinel.json": "sentinel.json"}
    for name in ("extension.js", "metadata.json"):
        files[f"usr/local/share/gnome-shell/extensions/{UUID}/{name}"] = name
    if session == 48:
        if systemd:
            command = _command(_unit(root, "org.gnome.Shell@wayland.service"), "ExecStart")
        else:
            desktop = root / "usr/share/applications/org.gnome.Shell.desktop"
            command = _command(desktop.read_text(), "Exec")
        if command not in (["gnome-shell"], ["/usr/bin/gnome-shell"]):
            raise RuntimeError(
                "GNOME readiness requires the stock environment-selected Shell launcher"
            )
        result["command"] = ["/usr/bin/gnome-session", "--session=gnome"]
    else:
        if not systemd:
            raise RuntimeError("GNOME session 50 readiness requires the native systemd lifecycle")
        command = _command(_unit(root, "org.gnome.Shell@.service"), "ExecStart")
        if command != ["/usr/bin/gnome-shell", "--mode=%i"]:
            raise RuntimeError(
                "GNOME readiness requires the stock instance-selected Shell template"
            )
        for name in (
            "gnome-session@.target",
            "gnome-session-services.target",
            "gnome-session-manager@.service",
        ):
            _unit(root, name)
        files["etc/systemd/user/gnome-session@sentinel.target.d/session.conf"] = "session.conf"
        files["usr/local/share/gnome-session/sessions/sentinel.session"] = "sentinel.session"
        result["command"] = ["/usr/bin/gnome-session", "--session=sentinel"]
    environment["GNOME_SHELL_SESSION_MODE"] = "sentinel"
    contents = {root / target: (ASSETS / source).read_bytes() for target, source in files.items()}
    for path, data in contents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".next")
        temporary.write_bytes(data)
        temporary.chmod(0o644)
        temporary.replace(path)
    return result
