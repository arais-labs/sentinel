"""Own one Linux desktop session. The GPU belongs to the VM, not this process.

/etc/sentinel/desktop.json is the user-editable session contract. This launcher
starts the selected command and publishes its environment for clipboard/tools.
No package installation, preference reset, or persistent health polling occurs
on connection. Children exit with the session; sound failure is non-fatal.
"""

import ctypes
import fcntl
import grp
import json
import os
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape

from desktop_contract import require_session_contract
from graphics_environment import GPU_ENVIRONMENT
from desktop_modes import DEFAULT_DESKTOP_GEOMETRY, DESKTOP_REFRESH_HZ, DESKTOP_RESOLUTIONS

ROOT = Path("/run/sentinel-desktop")
STATE = ROOT / "state.json"
CONFIG = Path("/etc/sentinel/desktop.json")
LOG = Path("/var/log/sentinel-desktop.log")
RESOLUTIONS = frozenset(DESKTOP_RESOLUTIONS)


def identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except (OSError, IndexError):
        return None


def record(pid):
    return {"pid": pid, "identity": identity(pid)}


def alive(item):
    return bool(item and item.get("identity") and identity(item.get("pid")) == item["identity"])


def atomic_json(path, value):
    temporary = path.with_suffix(".next")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def prepare_x11_directory(path=Path("/tmp/.X11-unix")):
    # /tmp is fresh at VM boot; unlike Xorg, compositor-owned Xwayland listeners
    # expect this standard directory to exist before they bind their sockets.
    path.mkdir(mode=0o1777, exist_ok=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(descriptor).st_uid != os.geteuid():
            raise RuntimeError("X11 socket directory has an unexpected owner")
        os.fchmod(descriptor, 0o1777)
    finally:
        os.close(descriptor)


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def healthy(state):
    return alive(state.get("owner")) and alive(state.get("session"))


def own_descendants():
    # Keep daemonized grandchildren under this session owner when their launcher
    # exits. This changes no other process tree and requires no privileged daemon.
    # Check the packaged kernel contract before launching anything to own.
    if not Path(f"/proc/self/task/{os.getpid()}/children").is_file():
        raise RuntimeError("Desktop lifecycle support is missing from the workspace kernel")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, *([ctypes.c_ulong] * 4)]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(36, 1, 0, 0, 0):  # PR_SET_CHILD_SUBREAPER
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def stop_children(children, grace=4, kill_timeout=2):
    """Reap this owner's children and adopted descendants before reporting exit."""
    child_list = Path(f"/proc/self/task/{os.getpid()}/children")
    processes = {process.pid: process for process in children}
    descriptors = {}
    poller = select.poll()
    signal_number = signal.SIGTERM
    deadline = time.monotonic() + grace

    def send(descriptor):
        try:
            signal.pidfd_send_signal(descriptor, signal_number)
        except ProcessLookupError:
            pass  # An exited child still needs to be reaped below.

    try:
        while True:
            # Read only our own immediate children, never scan arbitrary /proc
            # processes. Exiting launchers atomically reparent descendants here.
            tracked = set(descriptors.values())
            for pid in map(int, child_list.read_text().split()):
                if pid in tracked:
                    continue
                try:
                    descriptor = os.pidfd_open(pid)
                except ProcessLookupError:
                    continue
                descriptors[descriptor] = pid
                poller.register(descriptor, select.POLLIN)
                send(descriptor)
            if not descriptors:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if signal_number == signal.SIGKILL:
                    raise RuntimeError("Desktop descendants did not exit; restart the workspace")
                signal_number = signal.SIGKILL
                deadline = time.monotonic() + kill_timeout
                for descriptor in descriptors:
                    send(descriptor)
                continue
            for descriptor, _events in poller.poll(max(1, int(remaining * 1000))):
                pid = descriptors.pop(descriptor)
                poller.unregister(descriptor)
                try:
                    _, status = os.waitpid(pid, 0)
                    if pid in processes:
                        processes[pid].returncode = os.waitstatus_to_exitcode(status)
                finally:
                    os.close(descriptor)
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def stop(state):
    owner = state.get("owner")
    if alive(owner):
        # pidfd pins this process identity and provides an event-driven exit wait.
        descriptor = None
        try:
            descriptor = os.pidfd_open(owner["pid"])
            if alive(owner):
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                # Native desktops first await their graphical user-service stop
                # jobs, then close PAM and reap owned descendants. Killing the
                # controller earlier can strand KWin in the user manager.
                if not select.select([descriptor], [], [], 20)[0]:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                    select.select([descriptor], [], [], 2)
                    raise RuntimeError("Desktop cleanup timed out; restart the workspace")
        except ProcessLookupError:
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)
    if read_state().get("cleanup_pending"):
        raise RuntimeError("Desktop cleanup did not finish; restart the workspace")
    STATE.unlink(missing_ok=True)
    (ROOT / "session.json").unlink(missing_ok=True)


def ready_line(fd, process, timeout=15):
    if not select.select([fd], [], [], timeout)[0]:
        raise RuntimeError("Desktop startup timed out")
    value = os.read(fd, 8192)
    if not value or process.poll() is not None:
        raise RuntimeError(f"Desktop process exited during startup. See {LOG}")
    return value.decode().strip()


def wait_socket(path, process, timeout=8):
    # Bounded startup only; after readiness the owner blocks in waitpid.
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        try:
            with socket.socket(socket.AF_UNIX) as connection:
                connection.connect(str(path))
            return
        except OSError:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
    raise RuntimeError(f"Desktop socket did not become ready: {path}")


def start_session_bus(spawn, runtime, environment):
    """Own one bus shared by shell launchers and externally launched clients."""
    address = "unix:path=" + str(runtime / "sentinel-bus")
    read_fd, write_fd = os.pipe()
    try:
        bus = spawn(
            [
                "dbus-daemon",
                "--session",
                "--nofork",
                "--nopidfile",
                "--address=" + address,
                "--print-address=" + str(write_fd),
            ],
            pass_fds=(write_fd,),
        )
        os.close(write_fd)
        write_fd = -1
        published = ready_line(read_fd, bus)
        if published.split(",guid=", 1)[0] != address:
            raise RuntimeError("Desktop session bus published an unexpected address")
        environment["DBUS_SESSION_BUS_ADDRESS"] = published
        return bus
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)


def wait_session(session, bus):
    # An exited bus makes the desktop unusable. Observe both essential children
    # through kernel exit notifications, without adding a periodic health job.
    descriptors = []
    try:
        for child in (session, bus):
            descriptors.append(os.pidfd_open(child.pid))
        ready, _, _ = select.select(descriptors, [], [])
        if descriptors[0] not in ready:
            raise RuntimeError("Desktop session bus exited; restart the desktop")
        session.wait()
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def compositor_xdisplay(pid, proc=Path("/proc")):
    """Find this compositor's X11 listener, including lazy Xwayland sockets."""
    owned = set()
    for descriptor in (proc / str(pid) / "fd").iterdir():
        try:
            target = os.readlink(descriptor)
        except FileNotFoundError:
            continue
        match = re.fullmatch(r"socket:\[(\d+)\]", target)
        if match:
            owned.add(match[1])
    displays = set()
    for line in (proc / "net/unix").read_text().splitlines()[1:]:
        fields = line.split(maxsplit=7)
        if len(fields) != 8 or fields[6] not in owned or fields[3:5] != ["00010000", "0001"]:
            continue
        match = re.fullmatch(r"@?/tmp/\.X11-unix/X(\d+)", fields[7])
        if match:
            displays.add(":" + match[1])
    if len(displays) > 1:
        raise RuntimeError("Compositor owns multiple X11 displays; configure DISPLAY explicitly")
    return next(iter(displays), None)


def initialize_pointer(timeout):
    from sentinel_display import Desktop

    deadline = time.monotonic() + timeout
    desktop = Desktop(timeout=timeout)
    try:
        desktop.socket.settimeout(max(0.001, deadline - time.monotonic()))
        desktop.initialize_pointer()
    finally:
        desktop.close()


def wayland_ready(path, discover_x11=True):
    # A display sync alone cannot prove input readiness: Weston deliberately
    # advertises its pointer only after first motion. Bind the actual seats and
    # acknowledge that capability before publishing the session to applications.
    with socket.socket(socket.AF_UNIX) as connection:
        deadline = time.monotonic() + 8
        connection.settimeout(8)
        connection.connect(str(path))
        pid, _, _ = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )

        def send(object_id, opcode, body):
            connection.sendall(struct.pack("=II", object_id, (8 + len(body)) << 16 | opcode) + body)

        send(1, 1, struct.pack("=I", 2))  # get_registry(new id=2)
        send(1, 0, struct.pack("=I", 3))  # sync(new id=3)

        def receive(length):
            result = bytearray()
            while len(result) < length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Wayland input readiness timed out")
                connection.settimeout(remaining)
                data = connection.recv(length - len(result))
                if not data:
                    raise RuntimeError("Wayland disconnected during startup")
                result.extend(data)
            return result

        seats, globals_ = {}, {}
        next_id, sync_id, roundtrips = 4, 3, 0
        while True:
            object_id, header = struct.unpack("=II", receive(8))
            size, opcode = header >> 16, header & 65535
            if size < 8 or size > 4096 or size % 4:
                raise RuntimeError("Malformed Wayland readiness event")
            body = receive(size - 8)
            if object_id == 1 and opcode == 0:
                raise RuntimeError("Wayland rejected input readiness negotiation")
            if object_id == 2 and opcode == 0:
                if len(body) < 12:
                    raise RuntimeError("Malformed Wayland registry global")
                name, length = struct.unpack_from("=II", body)
                padded = (length + 3) & ~3
                if not length or len(body) != 12 + padded or body[8 + length - 1]:
                    raise RuntimeError("Malformed Wayland registry interface")
                if body[8 : 8 + length] == b"wl_seat\0":
                    version = struct.unpack_from("=I", body, 8 + padded)[0]
                    if not version:
                        raise RuntimeError("Wayland input seat has no supported version")
                    seat_id = next_id
                    next_id += 1
                    seats[seat_id], globals_[name] = 0, seat_id
                    send(
                        2,
                        0,
                        struct.pack("=II", name, 8) + b"wl_seat\0" + struct.pack("=II", 1, seat_id),
                    )
            elif object_id == 2 and opcode == 1 and len(body) == 4:
                seat_id = globals_.pop(struct.unpack("=I", body)[0], None)
                seats.pop(seat_id, None)
            elif object_id in seats and opcode == 0 and len(body) == 4:
                seats[object_id] = struct.unpack("=I", body)[0]
            elif object_id == sync_id and opcode == 0 and len(body) == 4:
                roundtrips += 1
                if roundtrips == 1:
                    # The registry callback preceded requests binding its seats.
                    # A second callback orders initial seat capabilities too.
                    sync_id = next_id
                    next_id += 1
                    send(1, 0, struct.pack("=I", sync_id))
                elif not seats:
                    raise RuntimeError("Wayland compositor has no input seat")
                elif not any(capabilities & 1 for capabilities in seats.values()):
                    initialize_pointer(max(0.001, deadline - time.monotonic()))
            if roundtrips >= 2 and any(capabilities & 1 for capabilities in seats.values()):
                break
        return compositor_xdisplay(pid) if discover_x11 else None


def modeline(geometry):
    width, height = map(int, geometry.split("x"))
    # Reduced-blanking 120 Hz timing for our virtual connector (no physical EDID).
    total_width, total_height = width + 160, height + 40
    clock = total_width * total_height * DESKTOP_REFRESH_HZ / 1_000_000
    return f"{clock:.3f} {width} {width + 48} {width + 80} {total_width} {height} {height + 3} {height + 8} {total_height} +hsync -vsync"


def seed_preferences(defaults, home):
    home = Path(home)
    for relative, contents in defaults.items():
        target = (home / relative).resolve()
        if not any(
            target.is_relative_to(base)
            for base in (home / ".config", home / ".local/share/backgrounds/sentinel")
        ):
            raise ValueError("Invalid desktop preference path")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("x") as stream:
                stream.write(
                    contents.replace("__MONITOR__", escape("Virtual-1")).replace(
                        "__HOME__", escape(str(home), {'"': "&quot;"})
                    )
                )
        except FileExistsError:
            pass


def configuration():
    choice = CONFIG.with_name("desktop-choice")
    selection = choice.read_text().strip() if choice.is_file() else None
    return require_session_contract(json.loads(CONFIG.read_text()), selection)


def serve(request, notify):
    from desktop_keyboard import (
        resolve_keyboard,
        validate_keyboard,
        weston_keyboard,
        weston_effective_keyboard,
    )
    from desktop_user import identity as desktop_identity, as_user

    config = configuration()
    own_descendants()
    keyboard = resolve_keyboard(config, request)
    keyboard_keys = validate_keyboard(keyboard)
    protocol, command = config.get("protocol"), config.get("command")
    if (
        protocol not in {"x11", "wayland"}
        or not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) for item in command)
    ):
        raise ValueError("desktop.json requires an x11/wayland protocol and a command array")
    additions = config.get("environment", {})
    if not isinstance(additions, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in additions.items()
    ):
        raise ValueError("desktop.json environment must contain strings")
    output_command = config.get("output_command", [])
    if not isinstance(output_command, list) or not all(
        isinstance(arg, str) for arg in output_command
    ):
        raise ValueError("desktop.json output_command must be an argument array")
    geometry = request["geometry"]
    # Both rootless Xorg and compositor-owned Xwayland need the standard
    # root-owned socket directory before the unprivileged login starts.
    prepare_x11_directory()
    if config.get("session_manager") == "native":
        from desktop_login import serve as serve_login

        return serve_login(request, notify, config, keyboard, keyboard_keys, sys.modules[__name__])
    account = desktop_identity()
    groups = os.getgrouplist(account.pw_name, account.pw_gid)
    credentials = {"user": account.pw_uid, "group": account.pw_gid, "extra_groups": groups}
    runtime = Path("/run/user") / str(account.pw_uid)
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(runtime, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(descriptor).st_uid not in {0, account.pw_uid}:
            raise RuntimeError("Desktop runtime directory has an unexpected owner")
        os.fchown(descriptor, account.pw_uid, account.pw_gid)
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)
    environment = {
        **os.environ,
        "XDG_SESSION_TYPE": protocol,
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
        **additions,
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "XDG_RUNTIME_DIR": str(runtime),
        "PULSE_SERVER": "unix:" + str(runtime / "pulse.sock"),
    }
    for key in (
        "SESSION_MANAGER",
        "DBUS_SESSION_BUS_ADDRESS",
        "LIBGL_ALWAYS_SOFTWARE",
        "VTEST_SOCKET_NAME",
    ):
        environment.pop(key, None)
    if Path("/lib/ld-musl-aarch64.so.1").exists():
        environment.pop("LD_LIBRARY_PATH", None)
        environment.pop("LIBGL_DRIVERS_PATH", None)
    if keyboard is not None:
        for field, value in keyboard.items():
            environment.setdefault("XKB_DEFAULT_" + field.upper(), value)
    children = []

    def interrupted(*_):
        raise InterruptedError("Desktop stopped")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    with LOG.open("ab", buffering=0) as log:

        def spawn(arguments, privileged=False, **options):
            process = subprocess.Popen(
                arguments,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd=account.pw_dir,
                **({} if privileged else credentials),
                **options,
            )
            children.append(process)
            return process

        try:
            atomic_json(STATE, {"owner": record(os.getpid()), "cleanup_pending": True})
            if protocol == "wayland":
                if "DISPLAY" not in additions:
                    environment.pop("DISPLAY", None)
                environment.setdefault("WAYLAND_DISPLAY", "wayland-0")
                environment.update(
                    {
                        "LIBSEAT_BACKEND": "seatd",
                        "SEATD_VTBOUND": "0",
                    }
                )
                # seatd has no socket-path CLI option. The managed broker and
                # libseat use their distribution's shared default inside this VM.
                environment.pop("SEATD_SOCK", None)
                read_fd, write_fd = os.pipe()
                try:
                    seat = spawn(
                        ["seatd", "-g", grp.getgrgid(account.pw_gid).gr_name, "-n", str(write_fd)],
                        privileged=True,
                        pass_fds=(write_fd,),
                    )
                    os.close(write_fd)
                    write_fd = -1
                    ready_line(read_fd, seat)
                finally:
                    os.close(read_fd)
                    if write_fd != -1:
                        os.close(write_fd)
                # Seed Weston preferences only once. The agent may replace its
                # compositor/command entirely without changing the GPU contract.
                weston = Path(account.pw_dir) / ".config/weston.ini"
                # Output timing is runtime configuration, separate from saved UI
                # preferences. Weston supports --config; other compositors own it.
                if "weston" in command and not any(arg.startswith("--config") for arg in command):
                    timing = runtime / "weston.ini"
                    with as_user(account):
                        saved = weston_keyboard(weston.read_text(), keyboard)
                        keyboard_keys = validate_keyboard(weston_effective_keyboard(saved))
                        if not re.search(r"^\[output\]", saved, re.MULTILINE):
                            saved += f"\n[output]\nname=Virtual-1\nmode={modeline(geometry)}\n"
                        timing.write_text(saved)
                    command = [*command, "--config=" + str(timing)]

            bus = start_session_bus(spawn, runtime, environment)

            if config.get("audio", True):
                try:
                    pulse_config = runtime / "pulse.pa"
                    with as_user(account):
                        pulse_config.write_text(
                            f"load-module module-native-protocol-unix socket={runtime / 'pulse.sock'}\n"
                            "load-module module-null-sink sink_name=sentinel rate=48000 channels=2\n"
                            "set-default-sink sentinel\n"
                        )
                    pulse = spawn(
                        [
                            "pulseaudio",
                            "--daemonize=no",
                            "--exit-idle-time=-1",
                            "--use-pid-file=no",
                            "-n",
                            "--file=" + str(pulse_config),
                        ]
                    )
                    wait_socket(runtime / "pulse.sock", pulse)
                    spawn(
                        [
                            "python3",
                            str(Path(__file__).with_name("desktop-audio.py")),
                            "--source",
                            "sentinel.monitor",
                        ],
                        privileged=True,
                    )
                except Exception as error:
                    log.write(f"Desktop sound unavailable: {error}\n".encode())

            session = spawn(command)
            if protocol == "wayland":
                wait_socket(runtime / environment["WAYLAND_DISPLAY"], session)
                if output_command:
                    subprocess.run(
                        [arg.replace("{geometry}", geometry) for arg in output_command],
                        env=environment,
                        cwd=account.pw_dir,
                        stdout=log,
                        stderr=log,
                        check=True,
                        timeout=8,
                        **credentials,
                    )
                xdisplay = wayland_ready(
                    runtime / environment["WAYLAND_DISPLAY"], "DISPLAY" not in additions
                )
                if xdisplay:
                    environment.setdefault("DISPLAY", xdisplay)
            # Devices persist across logins; synchronize their absolute state
            # after the new display has opened them, not only when a Wayland
            # compositor initially advertises no pointer capability.
            initialize_pointer(8)
            atomic_json(
                ROOT / "session.json",
                {
                    "protocol": protocol,
                    "environment": environment,
                    "clipboard_backend": config.get("clipboard_backend", protocol),
                    "keyboard_keys": keyboard_keys,
                    "user": {
                        "name": account.pw_name,
                        "uid": account.pw_uid,
                        "gid": account.pw_gid,
                        "groups": groups,
                        "home": account.pw_dir,
                    },
                },
            )
            state = {
                "owner": record(os.getpid()),
                "cleanup_pending": True,
                "session": record(session.pid),
                "geometry": geometry,
                "display": environment["DISPLAY" if protocol == "x11" else "WAYLAND_DISPLAY"],
                "port": 5901,
            }
            if not healthy(state):
                raise RuntimeError("Desktop session exited during startup")
            atomic_json(STATE, state)
            os.write(notify, b"ready\n")
            os.close(notify)
            notify = -1
            wait_session(session, bus)
        finally:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            # The caller must distinguish completed cleanup from an owner that
            # exited exceptionally or was killed while descendants remained.
            atomic_json(STATE, {**read_state(), "cleanup_pending": True})
            stop_children(children)
            STATE.unlink(missing_ok=True)
            (ROOT / "session.json").unlink(missing_ok=True)
            if notify != -1:
                os.close(notify)


def main(request):
    action = request.get("action", "status")
    if action not in {"status", "start", "stop"}:
        raise ValueError("Unknown desktop operation")
    geometry = request.get("geometry") or DEFAULT_DESKTOP_GEOMETRY
    if geometry not in RESOLUTIONS:
        raise ValueError("Unsupported desktop resolution")
    if action != "stop":
        if not CONFIG.is_file():
            return {"state": "not_installed", "reason": "Choose a desktop in workspace settings."}
        # Reject old profiles before creating lock/state files, stopping an
        # existing desktop, seeding preferences or spawning any processes.
        configuration()
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (ROOT / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_state()
        if action == "stop":
            stop(state)
            return {"state": "stopped"}
        if not CONFIG.is_file():
            return {
                "state": "not_installed",
                "reason": "Choose a desktop in workspace settings.",
            }
        if state.get("cleanup_pending") and not healthy(state):
            raise RuntimeError("Desktop cleanup did not finish; restart the workspace")
        if action == "status" or (healthy(state) and state.get("geometry") == geometry):
            return {
                "state": "running" if healthy(state) else "stopped",
                "geometry": state.get("geometry", geometry),
                "display": state.get("display", ""),
                "port": 5901,
            }
        stop(state)
        if not Path("/dev/dri/card0").exists():
            raise RuntimeError("Virtual GPU is unavailable. Restart the workspace.")
        read_fd, write_fd = os.pipe()
        try:
            with LOG.open("ab", buffering=0) as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        __file__,
                        "serve",
                        str(write_fd),
                        json.dumps({**request, "geometry": geometry}),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    pass_fds=(write_fd,),
                    start_new_session=True,
                )
            os.close(write_fd)
            write_fd = -1
            ready_line(read_fd, process, 60)
            state = read_state()
            if not healthy(state):
                raise RuntimeError(f"Desktop did not start. See {LOG}")
            return {
                "state": "running",
                "geometry": geometry,
                "display": state["display"],
                "port": 5901,
            }
        except BaseException:
            if "process" in locals():
                stop({"owner": record(process.pid)})
            raise
        finally:
            os.close(read_fd)
            if write_fd != -1:
                os.close(write_fd)


if __name__ == "__main__":
    if sys.argv[1] == "serve":
        try:
            serve(json.loads(sys.argv[3]), int(sys.argv[2]))
        except InterruptedError:
            pass
    else:
        try:
            print(json.dumps({"ok": True, **main(json.loads(sys.argv[1]))}))
        except Exception as error:
            print(json.dumps({"ok": False, "reason": str(error)}))
            sys.exit(1)
