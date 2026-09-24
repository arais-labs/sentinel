"""Native-login assertions, owned project I/O and reversible user-keyboard probe."""

import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import tarfile
import time
from pathlib import Path


def loaded_klipper(session):
    """Match the running Plasma mapping to bytes in the shipped native package."""
    user = session["user"]
    uid = user["uid"]
    # Compositor readiness precedes asynchronous clipboard applet loading.
    # Observe its real service announcement without activation or fixed sleeps.
    subprocess.run(
        ["gdbus", "wait", "--session", "--timeout", "20", "org.kde.klipper"],
        env=session["environment"],
        user=uid,
        group=user["gid"],
        extra_groups=user["groups"],
        check=True,
        timeout=25,
        stdout=subprocess.DEVNULL,
    )
    distribution = platform.freedesktop_os_release()["ID"]
    name = "plasma-workspace-libs" if distribution == "alpine" else "libklipper6"
    root = Path("/opt/sentinel/desktop/native-packages") / distribution / "packages" / name
    manifest = json.loads((root / "manifest.json").read_text())
    package = root / manifest["apk" if distribution == "alpine" else "deb"]
    with package.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == manifest["sha256"]
    if distribution == "alpine":
        archive = tarfile.open(package, "r:gz", ignore_zeros=True)
    else:
        archive = tarfile.open(
            fileobj=io.BytesIO(
                subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(package)], timeout=15)
            ),
            mode="r:",
        )
    with archive:
        libraries = [
            member
            for member in archive
            if member.isfile() and Path(member.name).name.startswith("libklipper.so.")
        ]
        assert len(libraries) == 1, libraries
        member = libraries[0]
        library = Path("/") / member.name.removeprefix("./")
        with archive.extractfile(member) as stream:
            expected = hashlib.file_digest(stream, "sha256").hexdigest()
    with library.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == expected
    identity = library.stat()
    owners = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if (proc / "comm").read_text().strip() != "plasmashell":
                continue
            status = (proc / "status").read_text().splitlines()
            uids = next(line.split()[1:] for line in status if line.startswith("Uid:"))
            assert list(map(int, uids)) == [uid] * 4 and uid > 0, uids
            mappings = [line.split(maxsplit=5) for line in (proc / "maps").read_text().splitlines()]
            loaded = [row for row in mappings if len(row) == 6 and row[5] == str(library)]
            assert loaded, f"Plasma {proc.name} did not load {library}"
            for row in loaded:
                major, minor = (int(part, 16) for part in row[3].split(":"))
                assert (major, minor, int(row[4])) == (
                    os.major(identity.st_dev),
                    os.minor(identity.st_dev),
                    identity.st_ino,
                )
            owners.append(int(proc.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
    assert owners, "No ordinary-user Plasma process loaded the bundled Klipper library"
    return {
        "package": name,
        "version": manifest["version"],
        "package_sha256": manifest["sha256"],
        "library": str(library),
        "library_sha256": expected,
        "pids": owners,
        "uid": uid,
    }


root = Path("/run/sentinel-desktop")
action = sys.argv[1]
if action == "running":
    session = json.loads((root / "session.json").read_text())
    state = json.loads((root / "state.json").read_text())
    uid = session["user"]["uid"]
    session_id = session["native_session"]
    assert state["native_session"] == session_id
    result = subprocess.run(
        ["loginctl", "show-session", session_id, "--no-pager"],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert properties["User"] == str(uid)
    assert properties["Active"] == "yes"
    assert properties["Type"] == session["protocol"]
    assert properties["Seat"] == "seat0"
    assert int(properties["VTNr"]) > 0
    if "XDG_SESSION_ID" in session["environment"]:
        assert session["environment"]["XDG_SESSION_ID"] == session_id
    assert Path(f"/proc/{state['session']['pid']}").stat().st_uid == uid
    assert session["output"] == state["output"]
    from desktop_modes import DESKTOP_RESOLUTIONS, DESKTOP_REFRESH_HZ
    from desktop_outputs import NativeOutput, select_mode

    desktop = Path("/etc/sentinel/desktop-choice").read_text().strip()
    user = session["user"]
    output = NativeOutput(
        desktop,
        session["environment"],
        {
            "user": user["uid"],
            "group": user["gid"],
            "extra_groups": user["groups"],
        },
    ).read()
    current = next(mode for mode in output.modes if mode.current)
    assert (current.width, current.height) == (
        session["output"]["width"],
        session["output"]["height"],
    ), "Compositor mode changed after session publication"
    assert abs(current.refresh_hz - DESKTOP_REFRESH_HZ) < 0.1, current
    advertised = {}
    for geometry in DESKTOP_RESOLUTIONS:
        mode = select_mode(output, *map(int, geometry.split("x")))
        assert mode is not None, f"Missing advertised resolution: {geometry}"
        assert abs(mode.refresh_hz - DESKTOP_REFRESH_HZ) < 0.1, (geometry, mode)
        advertised[geometry] = mode.refresh_hz
    proxies = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            command = (proc / "cmdline").read_bytes().split(b"\0")[0].decode()
            if Path(command).name == "rvgpu-proxy":
                start = (proc / "stat").read_text().rsplit(")", 1)[1].split()[19]
                proxies.append([int(proc.name), start])
        except (FileNotFoundError, ProcessLookupError):
            continue
    assert len(proxies) == 1, f"Expected one persistent GPU proxy: {proxies}"
    keyboard_schema = None
    keyboard_matching = None
    if Path("/etc/sentinel/desktop-choice").read_text().strip() == "gnome":
        user = session["user"]
        keyboard_schema = subprocess.check_output(
            ["gsettings", "list-keys", "org.gnome.desktop.input-sources"],
            env=session["environment"],
            user=user["uid"],
            group=user["gid"],
            extra_groups=user["groups"],
            text=True,
            timeout=5,
        ).splitlines()
        # Exercise the real per-user adapter against this installed GNOME
        # schema, then restore values including originally unset preferences.
        probe = """
import ast,json,os,subprocess,sys
from gi.repository import Gio
sys.path.insert(0, "/opt/sentinel/desktop")
from desktop_keyboard_native import before_start
settings=Gio.Settings.new("org.gnome.desktop.input-sources")
expected={"sources":[("xkb","us")],"mru-sources":[("xkb","us")],
          "xkb-options":["caps:escape"],"xkb-model":"pc105"}
original={key:settings.get_user_value(key) for key in expected}
try:
    before_start("gnome",dict(rules="evdev",model="pc105",layout="us",variant="",options="caps:escape"),os.environ.copy())
    actual={key:ast.literal_eval(subprocess.check_output(
        ["gsettings","get","org.gnome.desktop.input-sources",key],text=True)) for key in expected}
    assert actual==expected,actual
    print(json.dumps({"adapter":"gnome-gsettings","uid":os.getuid(),"values":actual}))
finally:
    for key,value in original.items():
        settings.reset(key) if value is None else settings.set_value(key,value)
    Gio.Settings.sync()
"""
        keyboard_matching = json.loads(
            subprocess.check_output(
                ["python3", "-c", probe],
                env=session["environment"],
                user=user["uid"],
                group=user["gid"],
                extra_groups=user["groups"],
                text=True,
                timeout=15,
            )
        )
    print(
        json.dumps(
            {
                "session_id": session_id,
                "owner": state["session"],
                "login": {
                    key: properties.get(key)
                    for key in ("User", "Type", "Active", "Seat", "VTNr", "Service")
                },
                "output": session["output"],
                "live_refresh_hz": current.refresh_hz,
                "advertised_modes": advertised,
                "gpu_proxy": proxies[0],
                "keyboard_schema": keyboard_schema,
                "keyboard_matching": keyboard_matching,
                "klipper": loaded_klipper(session) if desktop == "plasma" else None,
            }
        )
    )
elif action == "stopped":
    previous = json.loads(sys.argv[2])
    assert not (root / "session.json").exists(), "Stopped desktop still publishes a client endpoint"
    assert not (root / "state.json").exists(), "Stopped desktop still publishes running state"
    sys.path.insert(0, "/opt/sentinel/desktop")
    # Production PID + start-time check avoids treating PID reuse as survival.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "desktop_session", "/opt/sentinel/desktop/desktop-session.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert not module.alive(previous["owner"]), "Previous desktop login process survived stop"
    deadline = time.monotonic() + 5
    while True:
        result = subprocess.run(
            ["loginctl", "show-session", previous["session_id"], "--property=Active", "--value"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        if result.returncode or result.stdout.strip() != "yes":
            break
        assert time.monotonic() < deadline, "Previous graphical login remains active"
        time.sleep(0.1)
    print(json.dumps({"session_id": previous["session_id"], "stopped": True}))
elif action == "project":
    session = json.loads((root / "session.json").read_text())
    user = session["user"]
    project = Path(sys.argv[2])
    metadata = project.stat()
    details = {
        "project": str(project),
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": oct(metadata.st_mode & 0o777),
        "desktop_uid": user["uid"],
    }
    print(json.dumps(details), flush=True)
    # Drop privileges before every filesystem operation in the mounted project.
    # Never fix permissions here: this probe must expose actual GUI access.
    os.setgroups(user["groups"])
    os.setgid(user["gid"])
    os.setuid(user["uid"])
    source, destination = project / sys.argv[3], project / sys.argv[4]
    expected = sys.argv[5]
    assert source.read_text() == expected, "Normal desktop user cannot read the host project"
    with destination.open("x") as handle:
        handle.write(expected)
    assert destination.read_text() == expected
    print(json.dumps({"project_read_write": True}))
else:
    raise ValueError("Unknown native-session contract action")
