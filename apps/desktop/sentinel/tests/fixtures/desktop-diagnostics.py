"""Bounded, read-only diagnostics in the failed disposable guest only."""

import json
import os
import pwd
import subprocess
from pathlib import Path


def tail(path):
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 16384))
            print(f"\n--- {path} ---\n{handle.read().decode(errors='replace')}", flush=True)
    except OSError as error:
        print(f"{path}: {error}", flush=True)


for path in (
    Path("/var/log/sentinel-desktop.log"),
    Path("/var/log/messages"),
    Path("/var/log/apt/term.log"),
    Path("/var/log/dpkg.log"),
):
    tail(path)
# The active native session writes here until shutdown collects its log. On
# OpenRC there is no user journal, so inspecting only /var/log misses the actual
# compositor and audio service errors while the failed desktop is still alive.
for path in sorted(Path("/run/sentinel-login").glob("*/session.log"))[-2:]:
    tail(path)
session_environment = None
for name in ("session.json", "state.json"):
    path = Path("/run/sentinel-desktop") / name
    try:
        value = json.loads(path.read_text())
        # Environment may contain credentials; print only display/session keys.
        if "environment" in value:
            session_environment = value["environment"].copy()
            value["environment"] = {
                key: entry
                for key, entry in value["environment"].items()
                if key
                in {
                    "HOME",
                    "USER",
                    "DISPLAY",
                    "WAYLAND_DISPLAY",
                    "XDG_RUNTIME_DIR",
                    "XDG_SESSION_ID",
                    "XDG_SESSION_TYPE",
                    "DBUS_SESSION_BUS_ADDRESS",
                }
            }
        print(f"{path}: {json.dumps(value)}", flush=True)
    except (OSError, ValueError) as error:
        print(f"{path}: {error}", flush=True)
try:
    account = pwd.getpwnam("sentinel")
    for path in sorted((Path(account.pw_dir) / ".local/share/xorg").glob("Xorg.*.log"))[-2:]:
        tail(path)
except KeyError:
    account = None

commands = [
    ["loginctl", "list-sessions", "--no-pager"],
    ["ps", "-eo", "pid,ppid,stat,wchan:24,comm"],
    ["systemctl", "list-jobs", "--no-pager"],
    ["loginctl", "list-seats", "--no-pager"],
    ["rc-status", "--all"],
    ["journalctl", "-b", "--no-pager", "-n", "100", "-o", "short-monotonic"],
]
if Path("/run/snapd").exists():
    # Preserve namespace/confinement evidence before disposable VM teardown.
    # Installation can fail before any desktop or browser process exists.
    commands.extend(
        [
            ["findmnt", "-o", "TARGET,FSTYPE,PROPAGATION"],
            ["snap", "changes"],
            ["snap", "tasks", "--last=install"],
            ["snap", "debug", "sandbox-features"],
        ]
    )
if account:
    commands.append(["journalctl", "-b", "--no-pager", "-n", "100", f"_UID={account.pw_uid}"])
for command in commands:
    print(f"\n--- {' '.join(command)} ---", flush=True)
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=3,
            check=False,
        )
        print(result.stdout[-16384:], flush=True)
    except (OSError, subprocess.TimeoutExpired) as error:
        print(str(error), flush=True)

if account and session_environment:
    for arguments in [
        ("info",),
        ("--format=json", "list", "sinks"),
        ("--format=json", "list", "sources"),
        ("--format=json", "list", "source-outputs"),
    ]:
        print(f"\n--- native user pactl {' '.join(arguments)} ---", flush=True)
        try:
            result = subprocess.run(
                ["pactl", *arguments],
                env={
                    **session_environment,
                    "PULSE_SERVER": session_environment.get(
                        "PULSE_SERVER",
                        "unix:" + session_environment["XDG_RUNTIME_DIR"] + "/pulse/native",
                    ),
                },
                user=account.pw_uid,
                group=account.pw_gid,
                extra_groups=os.getgrouplist(account.pw_name, account.pw_gid),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=3,
                check=False,
            )
            print(result.stdout[-16384:], flush=True)
        except (OSError, subprocess.TimeoutExpired) as error:
            print(str(error), flush=True)
