from __future__ import annotations

import json
import hashlib
import os
import pwd
import select
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def prepare_profile(
    browser_dir, account, *, control_root=Path("/var/lib/sentinel/control"), root_device=None
):
    """Transfer only private browser state, never a project, symlink or mount."""
    browser_dir = Path(browser_dir)
    if browser_dir.parent.parent != control_root or browser_dir.name != "browser":
        raise ValueError("Browser profile must be in Sentinel's private session state")
    device = Path("/").stat().st_dev if root_device is None else root_device
    ancestors = list(reversed(browser_dir.parents)) + [browser_dir]
    for path in ancestors:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_dev != device:
            raise ValueError("Browser state cannot use symlinks or mounted/shared directories")

    def transfer(fd):
        info = os.fstat(fd)
        if info.st_dev != device:
            raise ValueError("Browser profile contains a mounted/shared directory")
        for name in os.listdir(fd):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                continue  # Chromium owns symlinks too; never follow their targets.
            if info.st_dev != device or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                raise ValueError("Browser profile contains mounted or hard-linked files")
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    transfer(child)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                try:
                    current = os.fstat(child)
                    if (
                        current.st_dev != device
                        or current.st_nlink != 1
                        or not stat.S_ISREG(current.st_mode)
                    ):
                        raise ValueError("Browser profile changed during ownership migration")
                    os.fchown(child, account.pw_uid, account.pw_gid)
                finally:
                    os.close(child)
            else:
                raise ValueError("Browser profile contains an unexpected special file")
        os.fchown(fd, account.pw_uid, account.pw_gid)

    profile = browser_dir / "chromium"
    profile_info = profile.lstat()
    if not stat.S_ISDIR(profile_info.st_mode) or profile_info.st_dev != device:
        raise ValueError("Chromium profile cannot be a symlink or mounted/shared directory")
    descriptor = os.open(browser_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # Upgrade legacy profiles once, not a full profile walk on every launch.
        if os.fstat(descriptor).st_uid != account.pw_uid or profile_info.st_uid != account.pw_uid:
            transfer(descriptor)
    finally:
        os.close(descriptor)
    # Existing preparation creates these private ancestors with mode 0700.
    # Grant only the desktop group traversal, not directory listing or writes.
    for path in (control_root.parent, control_root, browser_dir.parent):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if info.st_dev != device:
                raise ValueError("Browser state changed during ownership migration")
            os.fchown(descriptor, info.st_uid, account.pw_gid)
            os.fchmod(descriptor, stat.S_IMODE(info.st_mode) | stat.S_IXGRP)
        finally:
            os.close(descriptor)


def browser_environment(account, display, session_path=Path("/run/sentinel-desktop/session.json")):
    env = os.environ.copy()
    # Do not inherit a root shell's display/session or obsolete GPU overrides.
    for key in (
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "DBUS_SESSION_BUS_ADDRESS",
        "LIBGL_ALWAYS_SOFTWARE",
        "GALLIUM_DRIVER",
        "VTEST_SOCKET_NAME",
        "LD_LIBRARY_PATH",
        "LIBGL_DRIVERS_PATH",
        "VK_ICD_FILENAMES",
    ):
        env.pop(key, None)
    env.update(
        HOME=account.pw_dir,
        USER=account.pw_name,
        LOGNAME=account.pw_name,
        XDG_RUNTIME_DIR=f"/run/user/{account.pw_uid}",
    )
    protocol = "headless"
    if display:
        session = json.loads(session_path.read_text())
        if session.get("user", {}).get("uid") != account.pw_uid:
            raise ValueError(
                "Desktop session does not belong to the workspace user; restart the desktop"
            )
        protocol = session["protocol"]
        if protocol not in ("x11", "wayland"):
            raise ValueError("Unsupported desktop session protocol")
        env.update(session["environment"])
    return env, protocol


def browser_command(binary, port, profile_dir, window_size, protocol):
    command = [
        binary,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--password-store=basic",
        f"--window-size={window_size}",
        "--start-maximized",
        "about:blank",
    ]
    if protocol == "headless":
        command.insert(1, "--headless=new")
    else:
        command.insert(1, f"--ozone-platform={protocol}")
    return command


def snap_profile(browser_dir, home):
    """Keep automation separate and inside Chromium's confined persistent data."""
    identifier = hashlib.sha256(str(browser_dir).encode()).hexdigest()
    return Path(home) / "snap/chromium/common/sentinel-automation" / identifier


def browser_credentials(account):
    if account.pw_uid == 0 or account.pw_gid == 0:
        raise ValueError("Browser requires a regular workspace user; run workspace setup")
    if os.geteuid() == account.pw_uid:
        return {}
    if os.geteuid() != 0:
        raise ValueError("Browser must be launched by the workspace user or root supervisor")
    return {
        "user": account.pw_uid,
        "group": account.pw_gid,
        "extra_groups": os.getgrouplist(account.pw_name, account.pw_gid),
    }


def process_identity(pid):
    """Identify an incarnation of a process, independent of its mutable title."""
    process = Path(f"/proc/{pid}")
    fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
    return {
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "start_ticks": fields[19],
        "uid": process.stat().st_uid,
    }


def prepare_headless_runtime(browser_dir, account):
    """Headless sessions need no compositor and own their private runtime dir."""
    runtime = Path(browser_dir) / "runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    descriptor = os.open(runtime, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if info.st_dev != Path(browser_dir).stat().st_dev:
            raise ValueError("Browser runtime cannot be a mounted/shared directory")
        if info.st_uid != account.pw_uid:
            os.fchown(descriptor, account.pw_uid, account.pw_gid)
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)
    return str(runtime)


def native_user_runtime(account):
    """Inspect the native runtime without creating replacement login state."""
    runtime = Path(f"/run/user/{account.pw_uid}")
    for path, kind, forbidden in (
        (runtime, stat.S_ISDIR, 0o077),
        (runtime / "systemd", stat.S_ISDIR, 0o022),
        (runtime / "systemd/private", stat.S_ISSOCK, 0o000),
        (runtime / "bus", stat.S_ISSOCK, 0o000),
    ):
        info = path.lstat()
        if not kind(info.st_mode) or info.st_uid != account.pw_uid or info.st_mode & forbidden:
            raise ValueError(
                f"Native browser user runtime has an invalid owner or permissions: {path}"
            )
    return runtime


def snap_launch_command(command, account, environment, *, headless):
    """Enter a native user scope while preserving the browser's supervised PID."""
    browser_credentials(account)
    if not Path("/run/systemd/system").is_dir():
        raise ValueError("Snap Chromium requires the workspace user's native systemd manager")
    # Headless automation must survive the last graphical login ending.
    if headless and os.geteuid() == 0:
        if not Path(f"/var/lib/systemd/linger/{account.pw_name}").exists():
            subprocess.run(
                ["loginctl", "enable-linger", account.pw_name],
                check=True,
                capture_output=True,
                timeout=10,
            )
    try:
        runtime = native_user_runtime(account)
    except FileNotFoundError:
        if not headless or os.geteuid() != 0:
            raise ValueError(
                "Native browser user manager is unavailable; run workspace setup or restart the desktop"
            )
        subprocess.run(
            ["systemctl", "start", f"user@{account.pw_uid}.service"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        runtime = native_user_runtime(account)
    environment.update(
        XDG_RUNTIME_DIR=str(runtime), DBUS_SESSION_BUS_ADDRESS=f"unix:path={runtime}/bus"
    )
    # Scope mode execs the browser; a runuser wrapper would obscure its PID.
    return ["systemd-run", "--user", "--scope", "--quiet", "--", *command]


def main():
    request = json.loads(sys.argv[1])
    browser_dir = Path(request["browser"])
    profile_dir = browser_dir / "chromium"
    runtime_dir = Path(request["runtime"])
    logs_dir = Path(request["logs"])
    metadata_path = runtime_dir / "browser.json"

    def emit(payload):
        print(json.dumps(payload, separators=(",", ":")))

    def fail(detail):
        emit({"ok": False, "detail": detail})
        sys.exit(0)

    def pid_alive(pid):
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def kill_pid(pid, expected_identity):
        try:
            descriptor = os.pidfd_open(pid)
        except ProcessLookupError:
            return
        try:
            try:
                if process_identity(pid) != expected_identity:
                    fail("Browser process identity changed; refusing to signal it")
                poller = select.poll()
                poller.register(descriptor, select.POLLIN)
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                if not poller.poll(5000):
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                    if not poller.poll(5000):
                        fail("Browser did not exit after SIGKILL")
            except (FileNotFoundError, ProcessLookupError):
                return
        finally:
            os.close(descriptor)

    def cdp_ready(port):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1
            ) as response:
                return response.status == 200
        except Exception:
            return False

    def read_json(path):
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    browser_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    display = str(request.get("display") or "")
    account = None
    if Path("/opt/sentinel/desktop/desktop_user.py").is_file():
        sys.path.insert(0, "/opt/sentinel/desktop")
        from desktop_user import identity

        try:
            account = identity()
        except (KeyError, ValueError, RuntimeError) as error:
            fail(str(error))
    elif display:
        fail("Desktop user support is unavailable. Update the workspace desktop package.")
    else:
        try:
            account = pwd.getpwnam("sentinel")
        except KeyError:
            fail(
                "Browser user is unavailable. Run workspace setup to provision the regular workspace user."
            )
        if account.pw_dir != "/home/sentinel":
            fail(
                "Browser requires the sentinel account with home /home/sentinel; run workspace setup"
            )
    try:
        credentials = browser_credentials(account)
    except ValueError as error:
        fail(str(error))
    expected_uid = account.pw_uid
    selection = Path("/etc/sentinel/browser-selection")
    snap_chromium = Path("/snap/bin/chromium").exists() and (
        not selection.exists() or selection.read_text().strip() != "chrome"
    )
    if snap_chromium:
        profile_dir = snap_profile(browser_dir, account.pw_dir)
        # Create as the browser user, never root-follow user-controlled paths.
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import sys; Path(sys.argv[1]).mkdir(parents=True, exist_ok=True, mode=0o700)",
                str(profile_dir),
            ],
            check=True,
            **credentials,
        )

    existing = read_json(metadata_path)
    pid = existing.get("pid")
    port = existing.get("port")
    if isinstance(pid, int) and pid_alive(pid):
        try:
            identity_matches = existing.get("process_identity") == process_identity(pid)
        except (FileNotFoundError, ProcessLookupError):
            identity_matches = False
        if not identity_matches:
            fail("Stored browser process identity does not match; refusing to reuse or signal it")
    if (
        isinstance(pid, int)
        and isinstance(port, int)
        and pid_alive(pid)
        and cdp_ready(port)
        and existing.get("display", "") == str(request.get("display") or "")
        and existing.get("uid") == expected_uid
        and existing.get("profile_dir") == str(profile_dir)
    ):
        emit(
            {"ok": True, "pid": pid, "port": port, "profile_dir": str(profile_dir), "reused": True}
        )
        sys.exit(0)
    if isinstance(pid, int):
        kill_pid(pid, existing.get("process_identity"))

    if os.geteuid() == 0:
        try:
            prepare_profile(browser_dir, account)
        except (OSError, ValueError) as error:
            fail(str(error))

    for stale_name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        stale = profile_dir / stale_name
        try:
            if stale.exists() or stale.is_symlink():
                stale.unlink()
        except Exception:
            pass

    binary = None
    for candidate in (
        "sentinel-automation-browser",
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    ):
        found = shutil.which(candidate)
        if found:
            binary = found
            break
    if binary is None:
        fail("Required executable 'chromium' is not available in the runtime PATH.")

    def available_port():
        for candidate in range(9300, 9900):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.bind(("127.0.0.1", candidate))
                except OSError:
                    continue
                return candidate
        raise RuntimeError("No available Chromium CDP port in 9300-9899.")

    port = available_port()
    geometry = str(request.get("geometry") or "1600x1000")
    width, _, height = geometry.partition("x")
    window_size = f"{width}x{height}" if width.isdigit() and height.isdigit() else "1600x1000"
    log_file = logs_dir / "chromium.log"
    try:
        env, protocol = browser_environment(account, display)
        if not display and not snap_chromium:
            env["XDG_RUNTIME_DIR"] = prepare_headless_runtime(browser_dir, account)
    except (OSError, ValueError, KeyError) as error:
        fail(str(error))
    xdg_runtime_dir = env["XDG_RUNTIME_DIR"]
    command = browser_command(binary, port, profile_dir, window_size, protocol)
    if snap_chromium:
        try:
            command = snap_launch_command(command, account, env, headless=not display)
            xdg_runtime_dir = env["XDG_RUNTIME_DIR"]
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            fail(f"Cannot start Chromium in the native user session: {error}")
    with log_file.open("ab", buffering=0) as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
            **credentials,
        )
    try:
        child_identity = process_identity(process.pid)
    except (FileNotFoundError, ProcessLookupError):
        fail(f"Chromium exited before supervision started; see {log_file}.")

    deadline = time.time() + 20
    while time.time() < deadline:
        if process.poll() is not None:
            fail(f"Chromium exited early with status {process.returncode}; see {log_file}.")
        if cdp_ready(port):
            metadata = {
                "schema_version": 1,
                "pid": process.pid,
                "process_identity": child_identity,
                "port": port,
                "display": display,
                "uid": expected_uid,
                "profile_dir": str(profile_dir),
                "xdg_runtime_dir": xdg_runtime_dir,
                "log_file": str(log_file),
                "started_at": datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            }
            tmp = metadata_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(metadata, indent=2, sort_keys=True))
            tmp.replace(metadata_path)
            emit({"ok": True, **metadata, "reused": False})
            sys.exit(0)
        time.sleep(0.25)

    kill_pid(process.pid, child_identity)
    fail(f"Chromium did not expose CDP on 127.0.0.1:{port}; see {log_file}.")


if __name__ == "__main__":
    main()
