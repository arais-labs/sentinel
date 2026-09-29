"""Run a real PAM login session and publish compositor-owned connection details.

greetd invokes ``run REQUEST`` as the workspace user. An XDG autostart entry
invokes ``publish`` inside the resulting desktop. The privileged supervisor
owns greetd and terminates the registered logind session on stop; this process
never substitutes a private seat, display server, or service manager.
"""

import argparse
import json
import os
import platform
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

AUTOSTART = """[Desktop Entry]
Type=Application
Name=Sentinel desktop connection
Exec=/usr/bin/python3 /opt/sentinel/desktop/desktop-native-session.py publish
NoDisplay=true
"""


def private_directory(path, uid):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError(
            f"Native desktop directory must be owned by UID {uid} with mode 0700: {path}"
        )
    return path


def session_directory(environment, uid, create=False):
    runtime = Path(f"/run/user/{uid}")
    if environment.get("XDG_RUNTIME_DIR") != str(runtime):
        raise RuntimeError("PAM did not provide the native user's XDG_RUNTIME_DIR")
    private_directory(runtime, uid)
    if create:
        (runtime / "sentinel-desktop").mkdir(mode=0o700, exist_ok=True)
    return private_directory(runtime / "sentinel-desktop", uid)


def read_json(path, uid):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
            raise RuntimeError(f"Native desktop state must be a private regular user file: {path}")
        with os.fdopen(descriptor, closefd=False) as stream:
            return json.load(stream)
    finally:
        os.close(descriptor)


def write_json(path, value):
    descriptor, name = tempfile.mkstemp(prefix=".session-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.flush()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def process_identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except (OSError, IndexError):
        return None


def socket_ready(path, uid):
    try:
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid:
            return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.2)
            connection.connect(str(path))
        return True
    except OSError:
        return False


def gnome_ready(directory, owner, uid):
    """Require this login's live Shell to finish its input-blocking startup."""
    try:
        marker = read_json(directory / "gnome-ready.json", uid)
    except FileNotFoundError:
        return False
    if not isinstance(marker, dict) or marker.get("schema") != 1:
        return False
    if any(marker.get(key) != owner.get(key) for key in ("token", "session_id", "uid")):
        return False
    shell = marker.get("shell")
    if not isinstance(shell, dict):
        return False
    pid, identity = shell.get("pid"), shell.get("identity")
    if type(pid) is not int or pid <= 0 or not isinstance(identity, str) or not identity:
        return False
    if process_identity(pid) != identity:
        return False
    try:
        return Path(f"/proc/{pid}").stat().st_uid == uid
    except FileNotFoundError:
        return False


def wayland_socket(environment, runtime):
    display = environment.get("WAYLAND_DISPLAY", "")
    if not display:
        raise RuntimeError("The native compositor has not published WAYLAND_DISPLAY")
    path = Path(display)
    if not path.is_absolute():
        path = runtime / path
    if path.parent != runtime or path.name in {".", ".."}:
        raise RuntimeError("Native compositor socket is outside the login runtime directory")
    return path


def session_environment(request, inherited):
    command = request.get("command")
    overrides = request.get("environment", {})
    token = request.get("token")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(arg, str) and arg and "\0" not in arg for arg in command)
    ):
        raise RuntimeError("Native desktop command must be a nonempty argument list")
    if not isinstance(token, str) or len(token) < 32:
        raise RuntimeError("Native desktop launch requires a unique session token")
    if not isinstance(overrides, dict) or not all(
        isinstance(k, str)
        and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", k)
        and isinstance(v, str)
        and "\0" not in v
        for k, v in overrides.items()
    ):
        raise RuntimeError("Native desktop environment must contain string variables")
    # PAM and the compositor own these, never a persisted desktop preference.
    reserved = {
        "HOME",
        "USER",
        "LOGNAME",
        "XDG_RUNTIME_DIR",
        "XDG_SESSION_ID",
        "DBUS_SESSION_BUS_ADDRESS",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
    }
    environment = {**inherited, **{k: v for k, v in overrides.items() if k not in reserved}}
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"):
        environment.pop(key, None)
    if not environment.get("XDG_SESSION_ID"):
        raise RuntimeError("PAM did not register a native logind/elogind session")
    environment["SENTINEL_SESSION_TOKEN"] = token
    return environment


def ensure_bus(environment, request_path):
    runtime = Path(environment["XDG_RUNTIME_DIR"])
    canonical = runtime / "bus"
    if socket_ready(canonical, os.getuid()):
        environment["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={canonical}"
        return
    # OpenRC/elogind deliberately has no systemd user manager. One stock
    # dbus-run-session owns this graphical session's bus and cleans it on exit.
    if os.environ.get("SENTINEL_NATIVE_BUS_STARTED") == "1":
        if not environment.get("DBUS_SESSION_BUS_ADDRESS"):
            raise RuntimeError("dbus-run-session did not provide a session bus")
        return
    if platform.freedesktop_os_release().get("ID") != "alpine":
        raise RuntimeError("Native user D-Bus is unavailable; check the systemd user session")
    environment["SENTINEL_NATIVE_BUS_STARTED"] = "1"
    os.execvpe(
        "dbus-run-session",
        [
            "dbus-run-session",
            "--",
            sys.executable,
            str(Path(__file__).resolve()),
            "run",
            str(request_path),
        ],
        environment,
    )


def run(request_path):
    from desktop_user import identity

    account = identity()
    if os.getuid() != account.pw_uid or os.geteuid() != account.pw_uid:
        raise RuntimeError("Native desktop must run as the regular workspace user")
    directory = session_directory(os.environ, account.pw_uid, create=True)
    if (
        request_path.parent.parent != Path("/run/sentinel-login")
        or request_path.name != "launch.json"
    ):
        raise RuntimeError("Native desktop request must be in its isolated login directory")
    parent = request_path.parent.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022:
        raise RuntimeError("Native login directory must be owned and writable only by root")
    private_directory(request_path.parent, account.pw_uid)
    request = read_json(request_path, account.pw_uid)
    if request.get("token") != request_path.parent.name:
        raise RuntimeError("Native desktop request token does not match its login directory")
    environment = session_environment(request, dict(os.environ))
    protocol = request.get("protocol", "wayland")
    if protocol not in {"wayland", "x11"}:
        raise RuntimeError("Native desktop protocol must be wayland or x11")
    ensure_bus(environment, request_path)
    from desktop_keyboard_native import before_start
    from desktop_lock import configure as configure_lock

    configure_lock(environment.get("XDG_SESSION_DESKTOP", ""), environment)
    before_start(environment.get("XDG_SESSION_DESKTOP", ""), request.get("keyboard"), environment)
    environment["SENTINEL_SESSION_TOKEN"] = request["token"]
    # systemd-launched services and XDG autostart must inherit this login's GPU
    # configuration and token too, not a stale user-manager environment.
    names = sorted(
        set(request.get("environment", {}))
        | {
            "SENTINEL_SESSION_TOKEN",
            "XDG_SESSION_ID",
            "XDG_SESSION_TYPE",
            "XDG_CURRENT_DESKTOP",
            "XDG_SESSION_DESKTOP",
            "XDG_RUNTIME_DIR",
        }
    )
    names = [name for name in names if name in environment]
    command = ["dbus-update-activation-environment"]
    if Path("/run/systemd/system").is_dir():
        command.append("--systemd")
    subprocess.run([*command, *names], env=environment, check=True)
    if (
        environment.get("GNOME_SHELL_SESSION_MODE") == "sentinel"
        and Path("/run/systemd/system").is_dir()
    ):
        # Explicit provisioning may install the named session while this user's
        # manager already exists. Discover its units before launching GNOME.
        subprocess.run(["systemctl", "--user", "daemon-reload"], env=environment, check=True)
    owner = {"pid": os.getpid(), "identity": process_identity(os.getpid()), "pgrp": os.getpgrp()}
    if not owner["identity"]:
        raise RuntimeError("Native desktop process identity is unavailable")
    write_json(
        directory / "owner.json",
        {
            "owner": owner,
            "uid": account.pw_uid,
            "token": request["token"],
            "session_id": environment["XDG_SESSION_ID"],
            "protocol": protocol,
            "desktop": environment.get("XDG_SESSION_DESKTOP"),
        },
    )
    child = subprocess.Popen(request["command"], env=environment)

    def terminate(_number, _frame):
        if child.poll() is None:
            child.terminate()

    old_handlers = {
        number: signal.signal(number, terminate) for number in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        return child.wait()
    finally:
        for number, handler in old_handlers.items():
            signal.signal(number, handler)
        # Native service-manager children are stopped by the root owner's
        # loginctl terminate-session, not by killing unrelated user processes.
        for name in ("ready.json", "gnome-ready.json", "owner.json"):
            path = directory / name
            try:
                if read_json(path, account.pw_uid).get("token") == request["token"]:
                    path.unlink()
            except FileNotFoundError:
                pass


def publish(timeout=10):
    token = os.environ.get("SENTINEL_SESSION_TOKEN")
    uid = os.getuid()
    if uid == 0 or uid != os.geteuid():
        raise RuntimeError("Native desktop publication must run as a normal user")
    try:
        directory = session_directory(os.environ, uid)
        owner = read_json(directory / "owner.json", uid)
    except FileNotFoundError:
        return 0  # A normal desktop login not owned by Sentinel.
    # systemd XDG autostart may not forward arbitrary custom variables. The
    # private runtime record is authoritative, bound to a live owner/login.
    if (token and owner.get("token") != token) or owner.get("uid") != uid:
        raise RuntimeError("Native desktop publication belongs to another session")
    process = owner.get("owner", {})
    if not process.get("identity") or process_identity(process.get("pid")) != process["identity"]:
        raise RuntimeError("Native desktop session owner has exited")
    # GNOME intentionally removes PAM session variables from systemd-launched
    # applications. The private live wrapper record owns the registered login
    # identity; an application's environment is not that authority. Preserve
    # the compositor's actual environment instead of injecting those variables.
    session_id = owner.get("session_id")
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        raise RuntimeError("Native desktop owner has no registered login session")
    inherited_session_id = os.environ.get("XDG_SESSION_ID")
    if inherited_session_id is not None and inherited_session_id != session_id:
        raise RuntimeError("Native desktop autostart belongs to another login session")
    runtime = directory.parent
    protocol = owner.get("protocol", "wayland")
    display = wayland_socket(os.environ, runtime) if protocol == "wayland" else None
    if protocol == "x11":
        if not re.fullmatch(r":\d+(?:\.\d+)?", os.environ.get("DISPLAY", "")):
            raise RuntimeError("Native X11 desktop did not publish a local DISPLAY")
        authority = os.environ.get("XAUTHORITY")
        if not authority:
            raise RuntimeError("Native X11 desktop did not publish XAUTHORITY")
        info = Path(authority).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
            raise RuntimeError("Native X11 authority must be a private regular user file")
    elif protocol != "wayland":
        raise RuntimeError("Native desktop published an unsupported protocol")
    deadline = time.monotonic() + timeout
    while True:
        if protocol == "wayland":
            ready = socket_ready(display, uid)
        else:
            # Prove the supplied cookie authenticates to the actual X server;
            # existence of a display socket alone is not sufficient readiness.
            try:
                ready = (
                    subprocess.run(
                        ["xdpyinfo"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2,
                    ).returncode
                    == 0
                )
            except subprocess.TimeoutExpired:
                ready = False
        if ready and owner.get("desktop") == "gnome":
            ready = gnome_ready(directory, owner, uid)
        if ready:
            break
        if time.monotonic() >= deadline:
            if owner.get("desktop") == "gnome":
                raise RuntimeError(
                    "GNOME did not publish input readiness; check the Sentinel session-readiness extension and Shell log"
                )
            raise RuntimeError(
                "Native desktop did not accept connections using its published environment"
            )
        time.sleep(0.05)
    write_json(
        directory / "ready.json",
        {
            **owner,
            "protocol": protocol,
            "environment": dict(os.environ),
            **({"wayland_socket": str(display)} if display is not None else {}),
        },
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("run").add_argument("request", type=Path)
    commands.add_parser("publish")
    arguments = parser.parse_args()
    sys.exit(run(arguments.request) if arguments.action == "run" else publish())
