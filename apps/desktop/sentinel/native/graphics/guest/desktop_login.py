"""Privileged owner of one distro-native graphical login.

greetd/PAM and logind own authentication, seats, the runtime directory and user
services. Sentinel only requests a session and consumes its published endpoint.
No packages, private session bus, or substitute seat broker are started here.
"""

import json
import os
from pathlib import Path
import re
import runpy
import secrets
from graphics_environment import GPU_ENVIRONMENT
import select
import shlex
import shutil
import signal
import stat
import subprocess
import time

from desktop_outputs import configure as configure_output
from desktop_user import as_user, identity


def private_json(path, uid):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
            raise RuntimeError("Native desktop endpoint must be a private regular user file")
        if info.st_size > 1024 * 1024:
            raise RuntimeError("Native desktop endpoint exceeds the size limit")
        with os.fdopen(descriptor, closefd=False) as stream:
            contents = stream.read(1024 * 1024 + 1)
            if len(contents) > 1024 * 1024:
                raise RuntimeError("Native desktop endpoint exceeds the size limit")
            return json.loads(contents)
    finally:
        os.close(descriptor)


def validate_owner(value, uid, token, alive):
    if not isinstance(value, dict):
        raise RuntimeError("Native desktop endpoint must be an object")
    owner = value.get("owner", {})
    pid = owner.get("pid") if isinstance(owner, dict) else None
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 1
        or value.get("uid") != uid
        or value.get("token") != token
        or not alive(owner)
    ):
        raise RuntimeError("Native desktop endpoint does not belong to this login")
    process_uid(pid, uid)
    session_id = value.get("session_id", "")
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        raise RuntimeError("PAM did not register a native login session")
    return session_id


def validate_ready(value, uid, token, alive):
    session_id = validate_owner(value, uid, token, alive)
    environment = value.get("environment")
    if not isinstance(environment, dict) or not all(
        isinstance(key, str)
        and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key)
        and isinstance(item, str)
        and "\0" not in item
        for key, item in environment.items()
    ):
        raise RuntimeError("Native desktop endpoint contains an invalid environment")
    if environment.get("XDG_RUNTIME_DIR") != f"/run/user/{uid}":
        raise RuntimeError("Native desktop runtime directory does not match its account")
    if "XDG_SESSION_ID" in environment and environment["XDG_SESSION_ID"] != session_id:
        raise RuntimeError("Native desktop environment belongs to a different login")
    return session_id


def process_uid(pid, uid):
    """Check real/effective/saved/filesystem UIDs, not a JSON identity claim."""
    status = Path(f"/proc/{pid}/status").read_text()
    match = re.search(r"^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$", status, re.MULTILINE)
    if not match or any(int(item) != uid for item in match.groups()):
        raise RuntimeError("Native desktop process is not owned by the workspace user")


def is_descendant(pid, ancestor):
    for _ in range(128):
        if pid == ancestor:
            return True
        if pid <= 1:
            return False
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            pid = int(fields[1])
        except (OSError, ValueError, IndexError):
            return False
    return False


def login_properties(session_id):
    result = subprocess.run(
        [
            "loginctl",
            "show-session",
            session_id,
            "--no-pager",
            "--property=User",
            "--property=Active",
            "--property=Seat",
            "--property=Type",
            "--property=Leader",
            "--property=VTNr",
        ],
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def require_graphical_login(properties, uid, protocol):
    if properties.get("User") != str(uid) or properties.get("Active") != "yes":
        raise RuntimeError("Native desktop login is not active for the workspace user")
    if (
        properties.get("Seat") != "seat0"
        or properties.get("Type") != protocol
        or properties.get("VTNr") != "7"
    ):
        raise RuntimeError("Native desktop login has no registered graphical seat")


def require_login_owner(properties, owner, manager_pid):
    leader = properties.get("Leader", "")
    if (
        not leader.isdecimal()
        or int(leader) <= 1
        or not is_descendant(owner["pid"], int(leader))
        or not is_descendant(int(leader), manager_pid)
    ):
        raise RuntimeError("Native desktop process does not belong to this greetd login")


def runtime_state_path(uid, name):
    runtime = Path(f"/run/user/{uid}")
    for directory in (runtime, runtime / "sentinel-desktop"):
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != uid
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise RuntimeError("Native desktop runtime must be a private user directory")
    return runtime / "sentinel-desktop" / name


def configure_display(geometry, environment, account):
    desktop = Path("/etc/sentinel/desktop-choice").read_text().strip()
    credentials = {
        "user": account.pw_uid,
        "group": account.pw_gid,
        "extra_groups": os.getgrouplist(account.pw_name, account.pw_gid),
    }
    output = configure_output(desktop, geometry, environment, credentials)
    return output, credentials["extra_groups"]


def owned_starting_session(uid, token, alive, protocol, manager_pid):
    """Recover an owned login even when its desktop failed before autostart."""
    value = private_json(runtime_state_path(uid, "owner.json"), uid)
    session_id = validate_owner(value, uid, token, alive)
    properties = login_properties(session_id)
    require_graphical_login(properties, uid, protocol)
    require_login_owner(properties, value["owner"], manager_pid)
    return session_id


def graphical_shutdown_pending(output, expected):
    """Require a complete state snapshot; a finished target alone is insufficient."""
    states = {}
    for block in output.strip().split("\n\n"):
        properties = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        unit = properties.get("Id")
        if (
            unit not in expected
            or unit in states
            or "ActiveState" not in properties
            or "Job" not in properties
        ):
            raise RuntimeError(
                "Native graphical shutdown returned an incomplete unit-state snapshot"
            )
        states[unit] = properties
    if states.keys() != expected:
        raise RuntimeError("Native graphical shutdown omitted an owned unit")
    return {
        unit: {"state": properties["ActiveState"], "job": properties["Job"]}
        for unit, properties in states.items()
        if properties["ActiveState"] not in {"inactive", "failed"}
        or properties["Job"] not in {"", "0"}
    }


def stop_graphical_session(account, log):
    """Await only this user's native graphical units, not their user manager.

    PAM owns the login scope, but modern desktops also launch compositor units
    in the persistent systemd user manager. PartOf/BindsTo declare exactly which
    units belong to graphical-session.target; unrelated user services survive.
    """
    if not Path("/run/systemd/system").is_dir():
        return  # OpenRC/elogind desktops remain in the owned PAM process tree.
    runtime = Path(f"/run/user/{account.pw_uid}")
    try:
        info = runtime.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != account.pw_uid
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise RuntimeError("Native graphical shutdown runtime is not a private user directory")
        info = (runtime / "systemd").lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid or info.st_mode & 0o022:
            raise RuntimeError("Native graphical shutdown manager directory has an invalid owner")
        info = (runtime / "systemd/private").lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != account.pw_uid:
            raise RuntimeError("Native graphical shutdown manager is not the user's socket")
    except FileNotFoundError:
        return  # The manager already exited, or this failed login never had one.
    credentials = {
        "user": account.pw_uid,
        "group": account.pw_gid,
        "extra_groups": os.getgrouplist(account.pw_name, account.pw_gid),
    }
    environment = {
        "PATH": os.defpath,
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "XDG_RUNTIME_DIR": str(runtime),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
        "LC_ALL": "C",
    }
    # Leave the caller's 20-second cleanup budget room for PAM (3 seconds)
    # and owned-child termination/reaping (at most 6 seconds).
    deadline = time.monotonic() + 10
    inspection_deadline = time.monotonic() + 2
    pending, owned, activation_services = {"graphical-session.target"}, set(), set()
    while pending:
        remaining = inspection_deadline - time.monotonic()
        if remaining <= 0 or len(owned | pending) > 128:
            raise RuntimeError("Native graphical shutdown dependency inspection exceeded its bound")
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                "--property=Id",
                "--property=ConsistsOf",
                "--property=BoundBy",
                "--property=Type",
                "--property=RefuseManualStop",
                "--",
                *sorted(pending),
            ],
            env=environment,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=True,
            timeout=remaining,
            **credentials,
        )
        owned.update(pending)
        pending = set()
        for block in result.stdout.strip().split("\n\n"):
            properties = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            if (
                properties.get("Id") in owned
                and properties.get("Type") == "dbus"
                and properties.get("RefuseManualStop") == "no"
            ):
                activation_services.add(properties["Id"])
        for line in result.stdout.splitlines():
            key, _, value = line.partition("=")
            if key in {"ConsistsOf", "BoundBy"}:
                # systemctl shell-quotes string-array elements, including unit
                # names containing literal systemd \\xNN escapes. Decode the
                # list syntax, not the unit's own escaping.
                for unit in shlex.split(value):
                    if not re.fullmatch(r"[A-Za-z0-9_:@.\\-]{1,255}", unit):
                        raise RuntimeError(
                            f"Native graphical shutdown returned an invalid unit name: {unit!r}"
                        )
                    if unit not in owned:
                        pending.add(unit)
    # Let systemd propagate the stop transaction through PartOf/BindsTo.
    # GNOME's dependency-only units deliberately refuse manual stop requests.
    # Observe those units below rather than issuing separate stop jobs for them.
    subprocess.run(
        ["systemctl", "--user", "--no-block", "stop", "graphical-session.target"],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        check=True,
        timeout=min(2, max(0.01, deadline - time.monotonic())),
        **credentials,
    )
    pending_states = {}
    activation_stopped = not activation_services
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"Native graphical units did not finish shutdown: {pending_states}")
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                "--all",
                "--property=Id",
                "--property=ActiveState",
                "--property=Job",
                "--",
                *sorted(owned),
            ],
            env=environment,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=True,
            timeout=remaining,
            **credentials,
        )
        pending_states = graphical_shutdown_pending(result.stdout, owned)
        if not activation_stopped and not (pending_states.keys() - activation_services):
            # A compositor can activate a portal during its own shutdown, after
            # PartOf already stopped that portal. First await the graphical
            # clients, then drain their D-Bus services in one final transaction.
            # Never manually stop dependency-only units or the shared user bus.
            subprocess.run(
                ["systemctl", "--user", "--no-block", "stop", "--", *sorted(activation_services)],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                check=True,
                timeout=min(2, max(0.01, deadline - time.monotonic())),
                **credentials,
            )
            activation_stopped = True
            continue  # Verify the final transaction, including queued jobs.
        if not pending_states:
            return
        # Condition-based observation, not a delay assumed to mean completion.
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))


def stop_login(session_id, children, stop_children, log, account):
    try:
        if session_id:
            try:
                stop_graphical_session(account, log)
            finally:
                subprocess.run(
                    ["loginctl", "terminate-session", session_id],
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    timeout=3,
                )
    finally:
        # A logind timeout must not leave greetd/PAM running.
        stop_children(children)


def prepare_audio(environment, credentials):
    """Use the session's existing Pulse-compatible server, never launch another."""
    environment = dict(environment)
    if not environment.get("PULSE_SERVER"):
        endpoint = Path(environment["XDG_RUNTIME_DIR"]) / "pulse/native"
        deadline = time.monotonic() + 8
        while True:
            try:
                info = endpoint.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != credentials["user"]:
                    raise RuntimeError(
                        "Native sound endpoint is not a socket owned by the session user"
                    )
                break
            except FileNotFoundError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Native PipeWire-Pulse service did not publish its socket")
                time.sleep(0.05)
        # An explicit server prevents libpulse autospawning a competing daemon
        # while the native session's PipeWire services are starting.
        environment["PULSE_SERVER"] = "unix:" + str(endpoint)

    def pactl(*arguments):
        return subprocess.run(
            ["pactl", *arguments],
            env=environment,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=5,
            check=True,
            **credentials,
        ).stdout

    # No private daemon/socket is created when a native server is unavailable.
    pactl("info")

    def list_sinks():
        sinks = json.loads(pactl("--format=json", "list", "sinks"))
        if not isinstance(sinks, list) or not all(isinstance(sink, dict) for sink in sinks):
            raise RuntimeError("Native sound server returned invalid sink information")
        return sinks

    sinks = list_sinks()
    if not sinks:
        # A VM without physical audio still needs one playback output. Both
        # native PulseAudio and PipeWire-Pulse implement this standard module.
        pactl("load-module", "module-null-sink", "sink_name=sentinel", "rate=48000", "channels=2")
        sinks = list_sinks()
    # Module loading and the native session manager's default selection are
    # asynchronous. Observe their result instead of racing policy by forcing a
    # default sink immediately after load-module (notably after GNOME logout).
    deadline = time.monotonic() + 5
    while True:
        default = pactl("get-default-sink").strip()
        if default and any(sink.get("name") == default for sink in sinks):
            return environment
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Native sound server has no ready default playback output")
        time.sleep(min(0.05, remaining))
        sinks = list_sinks()


def start_audio(environment, account, groups, children, log, lifecycle):
    """Authorize as root; capture and encode entirely as the real desktop user."""
    credentials = {"user": account.pw_uid, "group": account.pw_gid, "extra_groups": groups}
    process = None
    try:
        environment = prepare_audio(environment, credentials)
        source = Path(__file__).with_name("desktop-audio.py")
        connect_audio = runpy.run_path(str(source))["connect"]
        with connect_audio(str(lifecycle.ROOT / "display.sock")) as connection:
            descriptor = connection.fileno()
            process = subprocess.Popen(
                [
                    "/usr/bin/python3",
                    str(source),
                    "--socket-fd",
                    str(descriptor),
                    "--source",
                    "@DEFAULT_MONITOR@",
                ],
                env=environment,
                cwd=account.pw_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=log,
                pass_fds=(descriptor,),
                **credentials,
            )
            children.append(process)
        try:
            if json.loads(lifecycle.ready_line(process.stdout.fileno(), process, timeout=8)) != {
                "event": "ready"
            }:
                raise RuntimeError("Native desktop audio did not confirm Opus capture readiness")
        finally:
            process.stdout.close()
        return process
    except InterruptedError:
        raise
    except Exception as error:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        log.write(f"Desktop sound unavailable: {error}\n".encode())
        return None


def wait_login(owner_pid, manager, audio, log):
    descriptors = {}
    try:
        for pid, role in ((owner_pid, "session"), (manager.pid, "manager")):
            descriptors[os.pidfd_open(pid)] = role
        if audio is not None:
            descriptors[os.pidfd_open(audio.pid)] = "audio"
        while True:
            ready, _, _ = select.select(list(descriptors), [], [])
            if any(descriptors[descriptor] != "audio" for descriptor in ready):
                return
            for descriptor in ready:
                # Sound failure does not end an otherwise healthy desktop, nor
                # start a retry loop that could duplicate audio servers/streams.
                code = audio.wait()
                log.write(
                    f"Desktop sound stopped (exit {code}); desktop remains available\n".encode()
                )
                del descriptors[descriptor]
                os.close(descriptor)
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def append_session_log(path, uid, log):
    """Retain a bounded, validated child log before removing its private launch directory."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
                raise RuntimeError("Native desktop log is not a private regular user file")
            os.lseek(descriptor, max(0, info.st_size - 65536), os.SEEK_SET)
            log.write(b"\nNative desktop user session log (last 64 KiB):\n")
            log.write(os.read(descriptor, 65536))
            log.write(b"\n")
        finally:
            os.close(descriptor)
    except FileNotFoundError:
        log.write(b"Native desktop user command did not create its session log\n")
    except (OSError, RuntimeError) as error:
        log.write(f"Native desktop log unavailable: {error}\n".encode())


def append_user_journal(uid, log):
    if not Path("/run/systemd/system").is_dir() or not shutil.which("journalctl"):
        return
    try:
        result = subprocess.run(
            ["journalctl", "-b", "--no-pager", "-n", "100", "-o", "short-monotonic", f"_UID={uid}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=3,
            check=False,
        )
        log.write(b"\nNative desktop user journal before startup cleanup (last 64 KiB):\n")
        log.write(result.stdout[-65536:])
        log.write(b"\n")
    except (OSError, subprocess.SubprocessError) as error:
        log.write(f"Native desktop journal unavailable: {error}\n".encode())


def greetd_config(command, user, runfile, protocol, session_log):
    # JSON strings are also TOML basic strings. User and paths never become shell
    # fragments; greetd's command field receives individually quoted arguments.
    if protocol not in {"x11", "wayland"}:
        raise ValueError("Unknown native desktop protocol")
    quote = json.dumps
    # greetd sends the login's standard streams to its VT. Keep errors from the
    # user runner and desktop in the user's private log, not an invisible VT.
    login_command = shlex.join(
        [
            "/usr/bin/python3",
            str(Path(__file__).with_name("desktop-session-log.py")),
            str(session_log),
            *command,
        ]
    )
    return (
        "[terminal]\nvt = 7\nswitch = true\n"
        f'[general]\nservice = "sentinel-{protocol}"\n'
        "source_profile = false\n"
        f"runfile = {quote(str(runfile))}\n"
        "[initial_session]\n"
        f"command = {quote(login_command)}\nuser = {quote(user)}\n"
        "[default_session]\n"
        f'command = "/usr/bin/sleep infinity"\nuser = {quote(user)}\n'
    )


def serve(request, notify, config, keyboard, keyboard_keys, lifecycle):
    account = identity()
    uid = account.pw_uid
    protocol = config["protocol"]
    token = secrets.token_hex(24)
    parent = Path("/run/sentinel-login")
    parent.mkdir(mode=0o711, exist_ok=True)
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("Native login configuration directory is not root-owned")
    launch_dir = parent / token
    launch_dir.mkdir(mode=0o700)
    os.chown(launch_dir, uid, account.pw_gid)
    launch = launch_dir / "launch.json"
    session_log = launch_dir / "session.log"
    environment = {
        "PATH": os.defpath + ":/usr/local/bin:/usr/sbin:/sbin",
        **(
            {}
            if Path("/lib/ld-musl-aarch64.so.1").exists()
            else {
                "LD_LIBRARY_PATH": "/opt/sentinel/graphics/lib",
                "LIBGL_DRIVERS_PATH": "/opt/sentinel/graphics/lib/dri",
            }
        ),
        "GALLIUM_DRIVER": "virgl",
        **GPU_ENVIRONMENT,
        "LC_ALL": "C.UTF-8",
        **config.get("environment", {}),
        "XDG_SESSION_TYPE": protocol,
    }
    if keyboard is not None:
        environment.update(
            {"XKB_DEFAULT_" + field.upper(): value for field, value in keyboard.items()}
        )
    with as_user(account):
        with launch.open("x") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(
                {
                    "token": token,
                    "protocol": protocol,
                    "command": config["command"],
                    "keyboard": keyboard,
                    "environment": environment,
                },
                stream,
            )
        lifecycle.seed_preferences(request.get("defaults", {}), account.pw_dir)
    greetd_path = lifecycle.ROOT / "greetd.toml"
    runfile = lifecycle.ROOT / (token + ".run")
    manager_socket = lifecycle.ROOT / (token + ".sock")
    greetd_path.write_text(
        greetd_config(
            [
                "/usr/bin/python3",
                "/opt/sentinel/desktop/desktop-native-session.py",
                "run",
                str(launch),
            ],
            account.pw_name,
            runfile,
            protocol,
            session_log,
        )
    )
    children = []
    session_id = None

    def interrupted(*_):
        raise InterruptedError("Desktop stopped")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    with lifecycle.LOG.open("ab", buffering=0) as log:
        try:
            lifecycle.atomic_json(
                lifecycle.STATE, {"owner": lifecycle.record(os.getpid()), "cleanup_pending": True}
            )
            # A working system bus/login manager is a base-image boot requirement.
            subprocess.run(
                ["loginctl", "list-seats", "--no-pager"],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                timeout=5,
            )
            manager = subprocess.Popen(
                ["greetd", "--config", str(greetd_path), "--socket-path", str(manager_socket)],
                # User desktop preferences (notably LD_PRELOAD/LD_LIBRARY_PATH)
                # must never become the privileged login manager's environment.
                env={"PATH": os.defpath + ":/usr/sbin:/sbin", "LANG": "C.UTF-8"},
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            children.append(manager)
            deadline = time.monotonic() + 50
            while manager.poll() is None and time.monotonic() < deadline:
                if not Path("/dev/dri/card0").exists():
                    raise RuntimeError(
                        "Desktop GPU disconnected during startup. See the host renderer log."
                    )
                try:
                    value = private_json(runtime_state_path(uid, "ready.json"), uid)
                except FileNotFoundError:
                    time.sleep(0.05)
                    continue
                # A stale endpoint is not readiness for the new login.
                if not isinstance(value, dict):
                    raise RuntimeError("Native desktop endpoint must be an object")
                if value.get("token") != token:
                    time.sleep(0.05)
                    continue
                candidate = validate_ready(value, uid, token, lifecycle.alive)
                properties = login_properties(candidate)
                require_graphical_login(properties, uid, protocol)
                require_login_owner(properties, value["owner"], manager.pid)
                if value.get("protocol") != protocol or not lifecycle.alive(value["owner"]):
                    raise RuntimeError("Native desktop endpoint changed during login validation")
                session_id = candidate  # Only a validated owned login may be terminated.
                break
            else:
                raise RuntimeError(
                    f"Native desktop login did not become ready. See {lifecycle.LOG}"
                )
            environment = value["environment"]
            output, groups = configure_display(request["geometry"], environment, account)
            lifecycle.initialize_pointer(8)
            from desktop_keyboard_native import after_start

            after_start(
                environment.get("XDG_SESSION_DESKTOP", ""),
                keyboard,
                environment,
                {"user": uid, "group": account.pw_gid, "extra_groups": groups},
            )
            audio = (
                start_audio(environment, account, groups, children, log, lifecycle)
                if config.get("audio", True)
                else None
            )
            lifecycle.atomic_json(
                lifecycle.ROOT / "session.json",
                {
                    "protocol": protocol,
                    "environment": environment,
                    "keyboard_keys": keyboard_keys,
                    "clipboard_backend": config["clipboard_backend"],
                    "native_session": session_id,
                    "output": output,
                    "user": {
                        "name": account.pw_name,
                        "uid": uid,
                        "gid": account.pw_gid,
                        "groups": groups,
                        "home": account.pw_dir,
                    },
                },
            )
            state = {
                "owner": lifecycle.record(os.getpid()),
                "cleanup_pending": True,
                "session": value["owner"],
                "geometry": f"{output['width']}x{output['height']}",
                "output": output,
                "display": environment["DISPLAY" if protocol == "x11" else "WAYLAND_DISPLAY"],
                "port": 5901,
                "native_session": session_id,
            }
            lifecycle.atomic_json(lifecycle.STATE, state)
            os.write(notify, b"ready\n")
            os.close(notify)
            notify = -1
            wait_login(value["owner"]["pid"], manager, audio, log)
        finally:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            if notify != -1:
                try:
                    append_user_journal(uid, log)
                except OSError:
                    pass  # Diagnostics must never prevent terminating the login.
            # Every published state already has cleanup_pending=True. Rewriting
            # it here could fail on a full /run and skip the actual cleanup.
            try:
                try:
                    if session_id is None and children:
                        try:
                            session_id = owned_starting_session(
                                uid, token, lifecycle.alive, protocol, children[0].pid
                            )
                        except (
                            OSError,
                            RuntimeError,
                            ValueError,
                            subprocess.SubprocessError,
                        ) as error:
                            # PAM also closes its login when greetd is reaped.
                            # Never terminate an unverified session as recovery.
                            log.write(f"Native login cleanup: {error}\n".encode())
                finally:
                    stop_login(session_id, children, lifecycle.stop_children, log, account)
                lifecycle.STATE.unlink(missing_ok=True)
                (lifecycle.ROOT / "session.json").unlink(missing_ok=True)
            finally:
                if notify != -1:
                    os.close(notify)
                try:
                    append_session_log(session_log, uid, log)
                except OSError:
                    pass
                session_log.unlink(missing_ok=True)
                launch.unlink(missing_ok=True)
                launch_dir.rmdir()
                runfile.unlink(missing_ok=True)
                manager_socket.unlink(missing_ok=True)
                greetd_path.unlink(missing_ok=True)
